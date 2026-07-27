from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_opencode_coding_attempt.py"


def _fake_binary(path: Path, *, token_count: int) -> Path:
    path.write_text(
        (
            "#!/usr/bin/env python3\n"
            "import json, time\n"
            "print(json.dumps({'type':'tool_use','part':{'type':'tool'}}), flush=True)\n"
            "print(json.dumps({'type':'step_finish','part':{'type':'step-finish',"
            f"'tokens':{{'input':{token_count},'output':1,'reasoning':0,"
            "'cache':{'read':2}}}}), flush=True)\n"
            "time.sleep(30)\n"
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def _run(tmp_path: Path, *, token_count: int, budget: int) -> subprocess.CompletedProcess[str]:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    binary = _fake_binary(tmp_path / "fake-opencode", token_count=token_count)
    environment = os.environ.copy()
    environment["OPENCODE_API_KEY"] = "test-only"
    return subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--binary",
            str(binary),
            "--worktree",
            str(worktree),
            "--raw-directory",
            str(tmp_path / "raw"),
            "--cli-name",
            "cli.jsonl",
            "--title",
            "test",
            "--agent",
            "build",
            "--prompt",
            "do it",
            "--max-wall-seconds",
            "10",
            "--max-tool-calls",
            "5",
            "--max-model-tokens",
            str(budget),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_runner_enforces_total_provider_token_budget(tmp_path: Path) -> None:
    result = _run(tmp_path, token_count=100, budget=50)

    assert result.returncode == 124
    execution = json.loads(
        (tmp_path / "raw" / "cli.jsonl.execution.json").read_text(encoding="utf-8")
    )
    assert execution["reason"] == "token_budget_exhausted"
    assert execution["usage"]["model_tokens"] == 103


def test_runner_enforces_wall_budget(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    binary = _fake_binary(tmp_path / "fake-opencode", token_count=1)
    environment = os.environ.copy()
    environment["OPENCODE_API_KEY"] = "test-only"
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--binary",
            str(binary),
            "--worktree",
            str(worktree),
            "--raw-directory",
            str(tmp_path / "raw"),
            "--cli-name",
            "cli.jsonl",
            "--title",
            "test",
            "--agent",
            "build",
            "--prompt",
            "do it",
            "--max-wall-seconds",
            "1",
            "--max-tool-calls",
            "5",
            "--max-model-tokens",
            "1000",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 124
    execution = json.loads(
        (tmp_path / "raw" / "cli.jsonl.execution.json").read_text(encoding="utf-8")
    )
    assert execution["reason"] == "wall_budget_exhausted"
