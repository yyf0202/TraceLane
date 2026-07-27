from __future__ import annotations

import subprocess
from pathlib import Path

from tracelane.coding.commands import CommandEvidence
from tracelane.coding.contracts import (
    AcceptanceSpec,
    CodingTask,
    DiffPolicy,
    InteractionScript,
    RepositoryBaseline,
)
from tracelane.coding.workspace import capture_workspace
from tracelane.graders.coding import grade_acceptance, grade_diff, grade_recovery


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def task(repository: Path) -> CodingTask:
    return CodingTask(
        task_id="grader-fixture",
        version=1,
        baseline=RepositoryBaseline("fixture", git(repository, "rev-parse", "HEAD"), "a" * 64),
        objective="Make the acceptance command pass.",
        acceptance=AcceptanceSpec(public_commands=("python3 check.py",), hidden_commands=()),
        diff_policy=DiffPolicy(editable_paths=("calculator.py",), protected_paths=("data/**",)),
        interaction=InteractionScript(mode="scripted_optional", requires_approval=False),
        allowed_commands=("python3",),
        max_wall_seconds=60,
        max_tool_calls=10,
        max_model_tokens=1_000,
    )


def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "tracelane@example.test")
    git(tmp_path, "config", "user.name", "TraceLane Test")
    (tmp_path / "calculator.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "check.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    git(tmp_path, "add", "calculator.py", "check.py")
    git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path


def evidence(status: str, before: str, after: str, classification: str = "test") -> CommandEvidence:
    return CommandEvidence(
        command="python3 check.py",
        classification=classification,
        status=status,
        exit_code=0 if status == "passed" else 1,
        duration_ms=1,
        stdout_preview="",
        stderr_preview="",
        stdout_sha256="a" * 64,
        stderr_sha256="b" * 64,
        before_workspace_sha256=before,
        after_workspace_sha256=after,
    )


def test_acceptance_grader_runs_frozen_command(tmp_path: Path) -> None:
    root = repository(tmp_path)
    grade = grade_acceptance(root, task(root))
    assert grade.status == "pass"
    assert grade.commands[0].classification == "acceptance_public"


def test_diff_grader_rejects_protected_path_change(tmp_path: Path) -> None:
    root = repository(tmp_path)
    (root / "data").mkdir()
    (root / "data" / "leak.txt").write_text("nope\n", encoding="utf-8")
    grade = grade_diff(capture_workspace(root, git(root, "rev-parse", "HEAD")), task(root))
    assert grade.status == "invalid"
    assert grade.protected_changes == ("data/leak.txt",)


def test_recovery_grader_requires_change_before_later_success() -> None:
    grade = grade_recovery(
        (
            evidence("failed", "one", "one"),
            evidence("passed", "one", "one"),
        )
    )
    assert grade.status == "unrecovered"

    recovered = grade_recovery(
        (
            evidence("failed", "one", "one"),
            evidence("passed", "two", "two"),
        )
    )
    assert recovered.status == "recovered"
