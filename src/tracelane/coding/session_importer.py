from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from tracelane.adapters.opencode import OpenCodeSession
from tracelane.artifacts import RunIdentity, RunStore
from tracelane.coding.contracts import AttemptEnd, CodingTask, SessionRef
from tracelane.coding.workspace import WorkspaceSnapshot
from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.security import classify_and_redact
from tracelane.tracing import TraceRecorder


@dataclass(frozen=True)
class AttemptSession:
    ref: SessionRef
    session: OpenCodeSession

    def __post_init__(self) -> None:
        if self.ref.session_id != self.session.session_id:
            raise ValueError("session reference ID does not match the observation session")


def _validate_sessions(sessions: tuple[AttemptSession, ...]) -> str:
    if not sessions:
        raise ValueError("an attempt must contain at least one session")
    ids = [item.ref.session_id for item in sessions]
    if len(set(ids)) != len(ids):
        raise ValueError("attempt session IDs must be unique")
    roots = {item.ref.root_session_id for item in sessions}
    if len(roots) != 1:
        raise ValueError("attempt sessions must share one root session")
    root = roots.pop()
    root_refs = [item.ref for item in sessions if item.ref.session_id == root]
    if len(root_refs) != 1 or root_refs[0].parent_session_id is not None:
        raise ValueError("attempt root session must be present without a parent")
    known = set(ids)
    parent_ids = (item.ref.parent_session_id for item in sessions if item.ref.parent_session_id)
    if any(parent_id not in known for parent_id in parent_ids):
        raise ValueError("attempt session parent must be included in the attempt")
    return root


def _model_id(sessions: tuple[AttemptSession, ...]) -> str:
    models: set[str] = set()
    for item in sessions:
        for row in item.session.observations:
            payload = row.get("payload")
            if not isinstance(payload, Mapping):
                continue
            event = payload.get("event")
            model = event.get("model") if isinstance(event, Mapping) else payload.get("model")
            if not isinstance(model, Mapping) or not isinstance(model.get("id"), str):
                continue
            provider = model.get("providerID")
            models.add(f"{provider}/{model['id']}" if isinstance(provider, str) else model["id"])
    return next(iter(models)) if len(models) == 1 else "opencode/multi-or-unknown"


def _event_type(value: str) -> str:
    return {
        "chat.message": "agent.message",
        "chat.params": "model.called",
        "model.request.prepared": "context.selected",
        "tool.execute.before": "tool.called",
        "tool.execute.after": "tool.observed",
        "context.compaction.selected": "context.compacted",
        "session.compacting": "context.compacted",
    }.get(value, "opencode.event")


def _stage(event_type: str) -> str | None:
    return {
        "context.selected": "context",
        "context.compacted": "context",
        "model.called": "model",
        "tool.called": "tool",
        "tool.observed": "tool",
    }.get(event_type)


def _sorted_rows(
    sessions: tuple[AttemptSession, ...],
) -> list[tuple[AttemptSession, Mapping[str, object]]]:
    rows = [(item, row) for item in sessions for row in item.session.observations]
    return sorted(
        rows,
        key=lambda item: (
            parse_utc(str(item[1]["observed_at"])),
            item[0].ref.session_id,
            canonical_json(item[1]),
        ),
    )


def import_coding_attempt(
    task: CodingTask,
    *,
    attempt_id: str,
    sessions: tuple[AttemptSession, ...],
    initial_workspace: WorkspaceSnapshot,
    final_workspace: WorkspaceSnapshot,
    end: AttemptEnd,
    artifact_root: str | Path,
    harness_config: Mapping[str, object],
    repeat: int = 1,
) -> RunStore:
    """Freeze a full OpenCode session tree as one coding-task attempt."""
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id must be a non-empty string")
    if initial_workspace.baseline_commit != task.baseline.commit_sha:
        raise ValueError("initial workspace does not match the task baseline")
    if final_workspace.baseline_commit != task.baseline.commit_sha:
        raise ValueError("final workspace does not match the task baseline")
    root_session_id = _validate_sessions(sessions)
    config = json.loads(canonical_json(harness_config))
    session_digest = sha256_json(
        [
            {
                "ref": item.ref.__dict__,
                "session_id": item.session.session_id,
                "digest": item.session.digest,
            }
            for item in sessions
        ]
    )
    identity = RunIdentity(
        task_sha256=task.task_sha256,
        bundle_sha256=session_digest,
        config_sha256=sha256_json(
            {
                "harness_config": config,
                "initial_workspace": initial_workspace.to_dict(),
                "final_workspace": final_workspace.to_dict(),
            }
        ),
        model_id=_model_id(sessions),
        repeat=repeat,
    )
    store = RunStore.create(artifact_root, identity.run_id)
    store.write_json("input/coding-task.json", task.to_dict())
    store.write_json(
        "input/attempt.json",
        {
            "attempt_id": attempt_id.strip(),
            "root_session_id": root_session_id,
            "harness_config": config,
            "end": end.to_dict(),
        },
    )
    for item in sessions:
        sanitized_observations = classify_and_redact(item.session.observations).value
        store.write_json(
            Path("input/sessions") / f"{item.ref.session_id}.json",
            {"ref": item.ref.__dict__, "observations": sanitized_observations},
        )
    for label, snapshot in (("initial", initial_workspace), ("final", final_workspace)):
        store.write_json(Path("workspace") / f"{label}.json", snapshot.to_dict())
        store.write_bytes(Path("workspace") / f"{label}.patch", snapshot.patch.encode("utf-8"))

    recorder = TraceRecorder(store)
    recorder.emit(
        "attempt.started",
        {
            "attempt_id": attempt_id.strip(),
            "task_id": task.task_id,
            "root_session_id": root_session_id,
        },
    )
    for item in sorted(sessions, key=lambda value: value.ref.session_id):
        recorder.emit("session.linked", {"attempt_id": attempt_id.strip(), **item.ref.__dict__})
    for item, row in _sorted_rows(sessions):
        observed_at = (
            parse_utc(str(row["observed_at"])).astimezone(UTC).isoformat().replace("+00:00", "Z")
        )
        event_type = _event_type(str(row.get("type", "opencode.event")))
        sanitized = classify_and_redact(row.get("payload", {})).value
        recorder.emit(
            event_type,
            {
                "session_id": item.ref.session_id,
                "root_session_id": root_session_id,
                "agent_kind": item.ref.agent_kind,
                "opencode_type": row.get("type"),
                "observed_at": observed_at,
                "payload": sanitized,
            },
            stage=_stage(event_type),
        )
    recorder.emit("attempt.ended", {"attempt_id": attempt_id.strip(), **end.to_dict()})
    return store
