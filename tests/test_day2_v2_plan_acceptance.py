from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).parent / "fixtures/coding_tasks/day2_v2_plan_acceptance.py"


def _run(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(plan), "BR-06"],
        text=True,
        capture_output=True,
        check=False,
    )


def test_br06_accepts_constant_window_and_ten_session_language(tmp_path: Path) -> None:
    content = """
Edit src/backtest/interfaces.py, src/backtest/strategy.py, src/backtest/engine.py,
and src/cli/daily_run.py. Sort by symbol/date, then groupby per-symbol and use a
10-session rolling(window=ADV10_WINDOW, min_periods=5). Use amount_adv10 with amount
fallback. Before filtering, exempt current_holdings passed from
portfolio.positions.keys. Route v4 to MultiSource v2 data. Run py_compile,
git diff --check, and behavioral tests.
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0, result.stdout
    assert '"earned": 100' in result.stdout


def test_br06_rejects_prose_fallback_with_contradictory_code(tmp_path: Path) -> None:
    content = """
Edit src/backtest/interfaces.py, src/backtest/strategy.py, src/backtest/engine.py,
and src/cli/daily_run.py. Sort by symbol/date, then groupby per-symbol with
rolling(window=ADV10_WINDOW, min_periods=5) over 10 sessions. Use amount_adv10 with
fallback to amount. Before filtering, exempt current_holdings passed from
portfolio.positions.keys. Implement:
if adv10 is None or np.isnan(adv10):
    effective_amount = adv10
else:
    effective_amount = amount
Route v4 to MultiSource v2 data. Run py_compile, git diff --check, and tests.
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "missing ADV10 branch must fall back to amount" in result.stdout
