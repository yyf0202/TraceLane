from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v4.py"
SOURCE_PLAN = (
    ROOT
    / "artifacts/raw-opencode/day3v2-br-11-glm52-r1-plan-build"
    / "handoff/plan.json"
)


def _run(plan: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), str(plan), "BR-11"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_v4_accepts_frozen_markdown_formatted_ordering() -> None:
    result = _run(SOURCE_PLAN)
    assert result.returncode == 0, result.stdout
    assert '"earned": 100' in result.stdout


def test_v4_rejects_markdown_formatted_wrong_order(tmp_path: Path) -> None:
    content = json.loads(SOURCE_PLAN.read_text(encoding="utf-8"))["content"]
    content = content.replace(
        "**Phase A — pre-`daily_run` real-fills sync**",
        "**Phase A — after `daily_run` real-fills sync**",
    ).replace(
        "sync must run **before** `daily_run`",
        "sync must run **after** `daily_run`",
    )
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    result = _run(plan)
    assert result.returncode == 1
    assert '"earned": 70' in result.stdout
