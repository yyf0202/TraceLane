from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).parent / "fixtures/coding_tasks/day2_v4_plan_acceptance.py"


def _run(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(plan), "BR-08"],
        text=True,
        capture_output=True,
        check=False,
    )


def test_br08_v4_accepts_equivalent_grouped_plan(tmp_path: Path) -> None:
    content = """
Edit src/cli/daily_run.py and scripts/scheduled_daily_run.py. Classify last_nav_date
None as new and >= target_date as already run. Use next_trading_day for lagging
simulations and preserve new/current/already in the normal single-day fast path.
Group by model_dir, take the earliest missing start, then call run_date_range_multi
once with sim_ids, start_date and end_date. Read trade_calendar.parquet cal_date with
weekday fallback; --force overrides the skip. Run py_compile, git diff --check, tests.
"""
    result = _run(tmp_path, content)
    assert result.returncode == 0, result.stdout


def test_br08_v4_rejects_correct_prose_with_contradictory_loop(
    tmp_path: Path,
) -> None:
    content = """
Edit src/cli/daily_run.py and scripts/scheduled_daily_run.py. Classify last_nav_date
None as new and >= target_date as already run. Use next_trading_day for lagging
simulations and preserve the normal single-day fast path through phase_tick_all.
Group by model_dir, take the earliest missing start, and make one grouped range call.
Implementation:
for sim in grouped_sims:
    run_date_range_multi(sim_ids=[sim], start_date=start, end_date=target)
Read trade_calendar.parquet cal_date with weekday fallback; --force overrides the skip.
Run py_compile, git diff --check, and behavioral tests.
"""
    result = _run(tmp_path, content)
    assert result.returncode == 1
    assert "grouped_earliest_range_catchup" in result.stdout
