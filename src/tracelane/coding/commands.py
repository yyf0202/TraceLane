from __future__ import annotations

import hashlib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tracelane.coding.contracts import CodingTask
from tracelane.coding.workspace import capture_workspace
from tracelane.security import classify_and_redact


def _allowed(task: CodingTask, command: str) -> bool:
    return any(
        command == prefix or command.startswith(f"{prefix} ") for prefix in task.allowed_commands
    )


def _classification(task: CodingTask, command: str) -> str:
    if command in task.acceptance.public_commands:
        return "acceptance_public"
    if command in task.acceptance.hidden_commands:
        return "acceptance_hidden"
    lowered = command.lower()
    if "pytest" in lowered or "unittest" in lowered:
        return "test"
    if "build" in lowered or "compile" in lowered:
        return "build"
    if "lint" in lowered or "ruff" in lowered:
        return "lint"
    return "other"


def _preview(value: str) -> str:
    redacted = classify_and_redact(value).value
    assert isinstance(redacted, str)
    return redacted[:4_000]


@dataclass(frozen=True)
class CommandEvidence:
    command: str
    classification: str
    status: str
    exit_code: int | None
    duration_ms: int
    stdout_preview: str
    stderr_preview: str
    stdout_sha256: str
    stderr_sha256: str
    before_workspace_sha256: str
    after_workspace_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "classification": self.classification,
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "stdout_sha256": self.stdout_sha256,
            "stderr_sha256": self.stderr_sha256,
            "before_workspace_sha256": self.before_workspace_sha256,
            "after_workspace_sha256": self.after_workspace_sha256,
        }


def capture_command(
    repository: str | Path,
    task: CodingTask,
    command: str,
    *,
    timeout_seconds: int | None = None,
    enforce_allowed: bool = True,
) -> CommandEvidence:
    """Run one allowlisted command and capture its result relative to the task baseline."""
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    command = command.strip()
    if enforce_allowed and not _allowed(task, command):
        raise ValueError("command is not allowed by this CodingTask")
    root = Path(repository).resolve()
    before = capture_workspace(root, task.baseline.commit_sha)
    started = time.monotonic()
    timeout = timeout_seconds if timeout_seconds is not None else task.max_wall_seconds
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stdout = result.stdout
        stderr = result.stderr
        exit_code = result.returncode
        status = "passed" if exit_code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = None
        status = "timed_out"
    duration_ms = round((time.monotonic() - started) * 1_000)
    after = capture_workspace(root, task.baseline.commit_sha)
    return CommandEvidence(
        command=command,
        classification=_classification(task, command),
        status=status,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_preview=_preview(stdout),
        stderr_preview=_preview(stderr),
        stdout_sha256=hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        before_workspace_sha256=before.workspace_sha256,
        after_workspace_sha256=after.workspace_sha256,
    )
