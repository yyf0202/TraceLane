from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from tracelane.artifacts import RunIdentity, RunStore
from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.tracing import TraceRecorder


@dataclass(frozen=True)
class OpenCodeSession:
    session_id: str
    observations: tuple[Mapping[str, object], ...]

    @property
    def digest(self) -> str:
        return sha256_json({"session_id": self.session_id, "observations": self.observations})


@dataclass(frozen=True)
class ProviderTurnDiagnosis:
    request_id: str
    state: str
    http_status: int | None
    first_token_ms: int | None
    last_response_at: str | None
    local_termination: str | None


def diagnose_last_provider_turn(
    session: OpenCodeSession,
    *,
    termination: Mapping[str, object] | None = None,
) -> ProviderTurnDiagnosis:
    start = next(
        (
            index
            for index in range(len(session.observations) - 1, -1, -1)
            if session.observations[index].get("type") == "model.turn.created"
        ),
        None,
    )
    if start is None:
        raise ValueError("OpenCode session has no observed provider turn")
    rows = session.observations[start:]
    created = rows[0].get("payload")
    request_id = created.get("request_id") if isinstance(created, Mapping) else None
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("observed provider turn has no request ID")
    types = {str(row.get("type")) for row in rows}
    state = "request_not_dispatched"
    if "model.request.dispatching" in types or "provider.http.request.started" in types:
        state = "gateway_no_response_headers"
    if "provider.http.response.headers" in types:
        state = "gateway_no_first_token"
    http_status = _last_payload_integer(rows, "provider.http.response.headers", "status")
    if http_status is not None and not 200 <= http_status < 300:
        state = "provider_rejected_before_stream"
    if "model.response.first_chunk" in types:
        state = "stream_interrupted"
    stream_failed = bool({"model.stream.error", "model.stream.aborted"} & types)
    if "model.stream.completed" in types and not stream_failed:
        state = "model_completed_processor_incomplete"
    if "model.processor.finalized" in types and not stream_failed:
        state = "completed"

    first_token_ms = _last_payload_integer(rows, "model.response.first_token", "first_token_ms")
    response_rows = [
        row
        for row in rows
        if row.get("type")
        in {
            "model.response.first_chunk",
            "model.response.first_token",
            "model.response.progress",
            "model.provider_turn.completed",
            "model.stream.completed",
            "model.stream.error",
            "model.stream.aborted",
        }
    ]
    last_response_at = str(response_rows[-1]["observed_at"]) if response_rows else None
    local_termination = (
        str(termination["reason"])
        if termination is not None
        and termination.get("source") == "local_budget"
        and isinstance(termination.get("reason"), str)
        else None
    )
    return ProviderTurnDiagnosis(
        request_id=request_id,
        state=state,
        http_status=http_status,
        first_token_ms=first_token_ms,
        last_response_at=last_response_at,
        local_termination=local_termination,
    )


def load_opencode_session(path: str | Path) -> OpenCodeSession:
    source = Path(path)
    rows: list[Mapping[str, object]] = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("OpenCode observation file is unavailable") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"OpenCode observation line {line_number} is invalid JSON") from exc
        if not isinstance(row, dict) or row.get("schema") != "tracelane-opencode-observation/v0.1":
            raise ValueError(f"OpenCode observation line {line_number} has an unknown schema")
        if not isinstance(row.get("session_id"), str) or not row["session_id"].strip():
            raise ValueError(f"OpenCode observation line {line_number} has no session ID")
        if not isinstance(row.get("observed_at"), str):
            raise ValueError(f"OpenCode observation line {line_number} has no timestamp")
        parse_utc(row["observed_at"])
        rows.append(row)
    if not rows:
        raise ValueError("OpenCode observation file is empty")
    session_ids = {str(row["session_id"]) for row in rows}
    if len(session_ids) != 1:
        raise ValueError("OpenCode observation file contains multiple sessions")
    return OpenCodeSession(session_id=session_ids.pop(), observations=tuple(rows))


def _last_payload_integer(
    rows: tuple[Mapping[str, object], ...] | list[Mapping[str, object]],
    event_type: str,
    field: str,
) -> int | None:
    for row in reversed(rows):
        if row.get("type") != event_type:
            continue
        payload = row.get("payload")
        value = payload.get(field) if isinstance(payload, Mapping) else None
        return value if isinstance(value, int) else None
    return None


def import_opencode_session(
    session: OpenCodeSession,
    artifact_root: str | Path,
    *,
    harness_config: Mapping[str, object],
    code_revision: str,
    repeat: int = 1,
) -> RunStore:
    """Freeze an observed OpenCode session as a TraceLane run and normalized trace."""
    if not isinstance(code_revision, str) or not code_revision.strip():
        raise ValueError("code_revision must be a non-empty string")
    config = json.loads(canonical_json(harness_config))
    identity = RunIdentity(
        task_sha256=session.digest,
        bundle_sha256=session.digest,
        config_sha256=sha256_json({"harness_config": config, "code_revision": code_revision}),
        model_id=_model_id(session.observations),
        repeat=repeat,
    )
    store = RunStore.create(artifact_root, identity.run_id)
    store.write_json(
        "input/opencode-session.json",
        {"session_id": session.session_id, "observations": session.observations},
    )
    store.write_json(
        "input/opencode-import.json",
        {
            "adapter": "opencode/v0.1",
            "code_revision": code_revision,
            "harness_config": config,
            "source_sha256": session.digest,
        },
    )
    recorder = TraceRecorder(store)
    recorder.emit("run.started", {"source": "opencode", "session_id": session.session_id})
    for row in session.observations:
        recorded_at = parse_utc(str(row["observed_at"]))
        event_type = _trace_event_type(str(row.get("type", "opencode.event")))
        recorder.emit(
            event_type,
            {
                "opencode_type": row.get("type"),
                "observed_at": recorded_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "payload": row.get("payload", {}),
            },
            stage=_stage(event_type),
        )
    recorder.emit("run.completed", {"source": "opencode", "session_id": session.session_id})
    return store


def _model_id(observations: tuple[Mapping[str, object], ...]) -> str:
    for row in observations:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        event = payload.get("event")
        model = event.get("model") if isinstance(event, Mapping) else payload.get("model")
        if isinstance(model, Mapping) and isinstance(model.get("id"), str):
            provider = model.get("providerID")
            return f"{provider}/{model['id']}" if isinstance(provider, str) else model["id"]
    return "opencode/unknown"


def _trace_event_type(event_type: str) -> str:
    return {
        "chat.message": "stage.started",
        "chat.params": "model.called",
        "model.request.prepared": "context.selected",
        "tool.execute.before": "tool.called",
        "tool.execute.after": "tool.observed",
        "context.compaction.selected": "context.selected",
        "session.compacting": "context.selected",
    }.get(event_type, "opencode.event")


def _stage(event_type: str) -> str | None:
    return {
        "context.selected": "context",
        "model.called": "model",
        "tool.called": "tool",
        "tool.observed": "tool",
    }.get(event_type)
