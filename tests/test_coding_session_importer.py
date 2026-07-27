from __future__ import annotations

import json
from pathlib import Path

from tests.test_coding_contracts import task
from tracelane.adapters.opencode import OpenCodeSession
from tracelane.coding.contracts import AttemptEnd, SessionRef
from tracelane.coding.session_importer import AttemptSession, import_coding_attempt
from tracelane.coding.workspace import WorkspaceSnapshot


def snapshot() -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        baseline_commit="a" * 40,
        head_commit="a" * 40,
        patch="",
        patch_sha256="b" * 64,
        changed_paths=(),
        untracked_files=(),
        workspace_sha256="c" * 64,
    )


def session(session_id: str, observed_at: str, event_type: str) -> OpenCodeSession:
    return OpenCodeSession(
        session_id=session_id,
        observations=(
            {
                "schema": "tracelane-opencode-observation/v0.1",
                "observed_at": observed_at,
                "type": event_type,
                "session_id": session_id,
                "payload": {"model": {"providerID": "opencode-go", "id": "glm-5.2"}},
            },
        ),
    )


def test_task_level_import_merges_root_and_child_sessions(tmp_path: Path) -> None:
    root = session("ses_build", "2026-07-27T00:00:01Z", "model.request.prepared")
    child = session("ses_explore", "2026-07-27T00:00:00Z", "tool.execute.after")
    store = import_coding_attempt(
        task(),
        attempt_id="attempt-001",
        sessions=(
            AttemptSession(SessionRef("ses_build", "ses_build", None, "build"), root),
            AttemptSession(SessionRef("ses_explore", "ses_build", "ses_build", "explore"), child),
        ),
        initial_workspace=snapshot(),
        final_workspace=snapshot(),
        end=AttemptEnd(reason="completed", final_answer="Done."),
        artifact_root=tmp_path,
        harness_config={"workflow": "plan-build"},
    )

    events = [
        json.loads(line)
        for line in (store.run_dir / "trace" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[0]["event_type"] == "attempt.started"
    assert [event["event_type"] for event in events].count("session.linked") == 2
    assert events[-1]["event_type"] == "attempt.ended"
    assert (store.run_dir / "input" / "sessions" / "ses_build.json").exists()
    assert (store.run_dir / "workspace" / "final.patch").read_text(encoding="utf-8") == ""


def test_task_level_import_rejects_orphaned_session(tmp_path: Path) -> None:
    orphan = session("ses_orphan", "2026-07-27T00:00:00Z", "opencode.event")
    try:
        import_coding_attempt(
            task(),
            attempt_id="attempt-001",
            sessions=(
                AttemptSession(SessionRef("ses_orphan", "ses_root", "ses_root", "explore"), orphan),
            ),
            initial_workspace=snapshot(),
            final_workspace=snapshot(),
            end=AttemptEnd(reason="crashed", final_answer=None),
            artifact_root=tmp_path,
            harness_config={},
        )
    except ValueError as exc:
        assert "root" in str(exc)
    else:
        raise AssertionError("orphaned session must be rejected")
