from __future__ import annotations

import subprocess
from pathlib import Path

from tracelane.coding.commands import capture_command
from tracelane.coding.contracts import (
    AcceptanceSpec,
    CodingTask,
    DiffPolicy,
    InteractionScript,
    RepositoryBaseline,
)


def git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()


def task(repository: Path) -> CodingTask:
    return CodingTask(
        task_id="command-capture",
        version=1,
        baseline=RepositoryBaseline(
            repository_id="fixture",
            commit_sha=git(repository, "rev-parse", "HEAD"),
            tree_sha256="a" * 64,
        ),
        objective="Run a command.",
        acceptance=AcceptanceSpec(
            public_commands=("python3 -c \"print('passed')\"",),
            hidden_commands=(),
        ),
        diff_policy=DiffPolicy(editable_paths=("calculator.py",), protected_paths=()),
        interaction=InteractionScript(mode="scripted_optional", requires_approval=False),
        allowed_commands=("python3",),
        max_wall_seconds=60,
        max_tool_calls=10,
        max_model_tokens=1_000,
    )


def test_command_capture_classifies_acceptance_and_redacts_absolute_paths(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "tracelane@example.test")
    git(tmp_path, "config", "user.name", "TraceLane Test")
    (tmp_path / "calculator.py").write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "calculator.py")
    git(tmp_path, "commit", "-qm", "baseline")

    evidence = capture_command(tmp_path, task(tmp_path), "python3 -c \"print('passed')\"")

    assert evidence.classification == "acceptance_public"
    assert evidence.status == "passed"
    assert evidence.exit_code == 0
    assert evidence.stdout_preview == "passed\n"
    assert evidence.before_workspace_sha256 == evidence.after_workspace_sha256


def test_command_capture_records_failure_without_raising(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "tracelane@example.test")
    git(tmp_path, "config", "user.name", "TraceLane Test")
    (tmp_path / "calculator.py").write_text("value = 1\n", encoding="utf-8")
    git(tmp_path, "add", "calculator.py")
    git(tmp_path, "commit", "-qm", "baseline")

    evidence = capture_command(tmp_path, task(tmp_path), "python3 -c \"import sys; sys.exit(2)\"")

    assert evidence.classification == "other"
    assert evidence.status == "failed"
    assert evidence.exit_code == 2
