from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tracelane.coding.workspace import capture_workspace


def git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "tracelane@example.test")
    git(tmp_path, "config", "user.name", "TraceLane Test")
    (tmp_path / "calculator.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    git(tmp_path, "add", "calculator.py")
    git(tmp_path, "commit", "-qm", "baseline")
    return tmp_path


def test_workspace_snapshot_uses_baseline_commit_and_records_patch(repository: Path) -> None:
    baseline = git(repository, "rev-parse", "HEAD")
    (repository / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (repository / "notes.txt").write_text("agent note\n", encoding="utf-8")

    snapshot = capture_workspace(repository, baseline)

    assert snapshot.baseline_commit == baseline
    assert snapshot.head_commit == baseline
    assert snapshot.changed_paths == ("calculator.py", "notes.txt")
    assert "return a + b" in snapshot.patch
    assert snapshot.untracked_files[0].path == "notes.txt"
    assert len(snapshot.workspace_sha256) == 64


def test_workspace_snapshot_rejects_non_repository(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Git repository"):
        capture_workspace(tmp_path, "a" * 40)
