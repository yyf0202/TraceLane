from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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
    }.get(event_type, "model.observed")


def _stage(event_type: str) -> str | None:
    return {"context.selected": "context", "model.called": "model", "tool.called": "tool", "tool.observed": "tool"}.get(event_type)
