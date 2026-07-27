from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).parent / "fixtures/coding_tasks/day2_v3_plan_acceptance.py"


def _run(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(plan), "BR-08"],
        text=True,
        capture_output=True,
        check=False,
    )


def test_br08_accepts_equivalent_helper_and_fast_path_names(tmp_path: Path) -> None:
    content = """
Edit src/cli/daily_run.py and scripts/scheduled_daily_run.py. Classify last_nav_date
None as new and >= target_date as already run. Use _next_trading_day for lagging
simulations and retain new/current/already in the normal single-day fast path through
the existing phase_tick_all. Group by model_dir, find min( start dates), then call
run_date_range_multi once with sim_ids, start_date and end_date. Read
trade_calendar.parquet cal_date with weekday fallback. --force overrides the skip.
Run py_compile, git diff --check, and tests.
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0, result.stdout
    assert '"earned": 100' in result.stdout


def test_br08_still_rejects_per_sim_range_calls(tmp_path: Path) -> None:
    content = """
Edit src/cli/daily_run.py and scripts/scheduled_daily_run.py. Classify last_nav_date
None as new and >= target_date as already run. Use _next_trading_day for lagging
simulations and retain the normal single-day fast path through phase_tick_all.
Group by model_dir and compute min(start dates), but call run_date_range_multi for
each sim with sim_ids, start_date and end_date. Read trade_calendar.parquet cal_date
with weekday fallback; --force overrides the skip. Run py_compile, git diff --check,
and tests.
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "grouped_earliest_range_catchup" in result.stdout
