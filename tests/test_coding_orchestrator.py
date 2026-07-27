from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tracelane.adapters.opencode import OpenCodeSession
from tracelane.coding.contracts import (
    AcceptanceSpec,
    AttemptEnd,
    CodingTask,
    DiffPolicy,
    InteractionScript,
    RepositoryBaseline,
    SessionRef,
)
from tracelane.coding.orchestrator import finalize_coding_attempt
from tracelane.coding.session_importer import AttemptSession
from tracelane.coding.workspace import capture_workspace


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_finalize_coding_attempt_writes_one_graded_run(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "tracelane@example.test")
    git(repository, "config", "user.name", "TraceLane Test")
    (repository / "calculator.py").write_text("value = 1\n", encoding="utf-8")
    (repository / "check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    git(repository, "add", "calculator.py", "check.py")
    git(repository, "commit", "-qm", "baseline")
    baseline = git(repository, "rev-parse", "HEAD")
    task = CodingTask(
        task_id="synthetic-fix",
        version=1,
        baseline=RepositoryBaseline("synthetic", baseline, "a" * 64),
        objective="Fix calculator.",
        acceptance=AcceptanceSpec(public_commands=("python3 check.py",), hidden_commands=()),
        diff_policy=DiffPolicy(editable_paths=("calculator.py",), protected_paths=()),
        interaction=InteractionScript(mode="scripted_optional", requires_approval=False),
        allowed_commands=("python3",),
        max_wall_seconds=60,
        max_tool_calls=10,
        max_model_tokens=1_000,
    )
    initial = capture_workspace(repository, baseline)
    (repository / "calculator.py").write_text("value = 2\n", encoding="utf-8")
    final = capture_workspace(repository, baseline)
    session = OpenCodeSession(
        session_id="ses_build",
        observations=(
            {
                "schema": "tracelane-opencode-observation/v0.1",
                "observed_at": "2026-07-27T00:00:00Z",
                "type": "model.request.prepared",
                "session_id": "ses_build",
                "payload": {"model": {"providerID": "opencode-go", "id": "glm-5.2"}},
            },
        ),
    )

    finalized = finalize_coding_attempt(
        task,
        attempt_id="attempt-001",
        sessions=(AttemptSession(SessionRef("ses_build", "ses_build", None, "build"), session),),
        initial_workspace=initial,
        final_workspace=final,
        end=AttemptEnd(reason="completed", final_answer="Done."),
        repository=repository,
        artifact_root=tmp_path / "artifacts",
        harness_config={"workflow": "direct-build"},
    )

    assert finalized.grades.overall == "pass"
    grades = json.loads((finalized.store.run_dir / "output" / "coding-grades.json").read_text())
    assert grades["acceptance"]["status"] == "pass"
