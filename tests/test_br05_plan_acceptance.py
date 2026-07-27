from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRADER = ROOT / "tests/fixtures/coding_tasks/br05_plan_acceptance.py"


def test_br05_plan_gate_accepts_complete_equivalent_plan(tmp_path: Path) -> None:
    plan = {
        "content": """
        - In src/backtest/engine.py keep the previous signal so T executes at T+1;
          skip the first day and make no trade when there is no prior signal.
        - In src/components/target_generator.py update both crosssectional ranking and
          OHLC labels to the T+1 -> T+2 window with shift(-2).
        - In src/components/models.py make FiLM per-timestep and use the sequence mean
          mean(dim=1) for the Bottleneck context token.
        - Validation: run tests, python3 -m py_compile, and git diff --check.
        """
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(GRADER), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert '"earned": 100' in result.stdout


def test_br05_plan_gate_rejects_open_ended_research_note(tmp_path: Path) -> None:
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"content": "Read more training and inference files before deciding."}),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(GRADER), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert '"earned": 0' in result.stdout
