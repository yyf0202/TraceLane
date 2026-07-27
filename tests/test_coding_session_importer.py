from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.test_coding_contracts import task
from tracelane.adapters.opencode import OpenCodeSession
from tracelane.coding.contracts import AttemptEnd, SessionRef
from tracelane.coding.plan_artifact import PlanArtifact
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


def test_task_level_import_redacts_local_paths_in_frozen_session(tmp_path: Path) -> None:
    observed = OpenCodeSession(
        session_id="ses_build",
        observations=(
            {
                "schema": "tracelane-opencode-observation/v0.1",
                "observed_at": "2026-07-27T00:00:00Z",
                "type": "tool.execute.after",
                "session_id": "ses_build",
                "payload": {"output": "read /Users/private/repository/source.py"},
            },
        ),
    )
    store = import_coding_attempt(
        task(),
        attempt_id="attempt-redacted",
        sessions=(
            AttemptSession(
                SessionRef("ses_build", "ses_build", None, "build"),
                observed,
            ),
        ),
        initial_workspace=snapshot(),
        final_workspace=snapshot(),
        end=AttemptEnd(reason="completed", final_answer="Done."),
        artifact_root=tmp_path,
        harness_config={},
    )

    frozen = (store.run_dir / "input" / "sessions" / "ses_build.json").read_text(encoding="utf-8")
    assert "/Users/private" not in frozen
    assert "[LOCAL_PATH]" in frozen


def test_plan_artifact_is_bound_to_root_and_frozen_with_attempt(tmp_path: Path) -> None:
    root = session("ses_plan", "2026-07-27T00:00:00Z", "chat.message")
    child = session("ses_build", "2026-07-27T00:00:01Z", "chat.message")
    content = "Inspect the failure and implement the smallest safe fix."
    plan = PlanArtifact(
        task_sha256=task().task_sha256,
        plan_session_id="ses_plan",
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_cli_sha256="a" * 64,
    )

    store = import_coding_attempt(
        task(),
        attempt_id="attempt-plan-build",
        sessions=(
            AttemptSession(SessionRef("ses_plan", "ses_plan", None, "plan"), root),
            AttemptSession(
                SessionRef("ses_build", "ses_plan", "ses_plan", "build"),
                child,
            ),
        ),
        initial_workspace=snapshot(),
        final_workspace=snapshot(),
        end=AttemptEnd(reason="completed", final_answer="Done."),
        artifact_root=tmp_path,
        harness_config={"workflow": "plan-build"},
        plan_artifact=plan,
    )

    frozen = json.loads((store.run_dir / "input" / "plan.json").read_text())
    assert frozen["content_sha256"] == plan.content_sha256
    assert frozen["plan_session_id"] == "ses_plan"


def test_plan_artifact_is_preserved_when_gate_blocks_build(tmp_path: Path) -> None:
    root = session("ses_plan", "2026-07-27T00:00:00Z", "chat.message")
    content = "Plan."
    plan = PlanArtifact(
        task_sha256=task().task_sha256,
        plan_session_id="ses_plan",
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_cli_sha256="a" * 64,
    )
    store = import_coding_attempt(
        task(),
        attempt_id="attempt-plan-only",
        sessions=(AttemptSession(SessionRef("ses_plan", "ses_plan", None, "plan"), root),),
        initial_workspace=snapshot(),
        final_workspace=snapshot(),
        end=AttemptEnd(reason="blocked", final_answer="Plan gate blocked build."),
        artifact_root=tmp_path,
        harness_config={"workflow": "plan-build", "build_started": False},
        plan_artifact=plan,
    )

    frozen = json.loads((store.run_dir / "input" / "plan.json").read_text())
    assert frozen["content_sha256"] == plan.content_sha256
    assert not (store.run_dir / "input" / "sessions" / "ses_build.json").exists()


def test_plan_artifact_rejects_non_plan_root(tmp_path: Path) -> None:
    root = session("ses_build", "2026-07-27T00:00:00Z", "chat.message")
    content = "Plan."
    plan = PlanArtifact(
        task_sha256=task().task_sha256,
        plan_session_id="ses_build",
        content=content,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        source_cli_sha256="a" * 64,
    )
    try:
        import_coding_attempt(
            task(),
            attempt_id="attempt-wrong-root",
            sessions=(
                AttemptSession(SessionRef("ses_build", "ses_build", None, "build"), root),
            ),
            initial_workspace=snapshot(),
            final_workspace=snapshot(),
            end=AttemptEnd(reason="blocked", final_answer=None),
            artifact_root=tmp_path,
            harness_config={},
            plan_artifact=plan,
        )
    except ValueError as exc:
        assert "plan root" in str(exc)
    else:
        raise AssertionError("plan artifact must be bound to a plan root")
