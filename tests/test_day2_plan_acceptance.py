from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).parent / "fixtures/coding_tasks/day2_plan_acceptance.py"


def _run(tmp_path: Path, task: str, content: str) -> subprocess.CompletedProcess[str]:
    plan = tmp_path / f"{task}.json"
    plan.write_text(json.dumps({"content": content}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(GATE), str(plan), task], text=True, capture_output=True, check=False
    )


@pytest.mark.parametrize(
    ("task", "content"),
    [
        (
            "BR-06",
            """
Edit src/backtest/interfaces.py, src/backtest/strategy.py, src/backtest/engine.py,
and src/cli/daily_run.py. Sort by symbol/date, then groupby per-symbol and rolling(10,
min_periods=5). Use amount_adv10 with amount fallback. Before filtering, exempt
current_holdings passed from portfolio.positions.keys. Route v4 to MultiSource v2 data.
Run py_compile, git diff --check, and behavioral tests.
""",
        ),
        (
            "BR-07",
            """
Edit src/components/models.py and src/cli/factorvae_cs_train.py. Add a distinct
factor_logvar_max used by prior and posterior, separate from the broad KL bound.
Propagate it through config and model kwargs and --factor-logvar-max default 2.0.
Inside the epoch loop save a per-epoch checkpoint using try/except warning, nonfatal.
Write partial kfold_meta.json before fold training. Run py_compile, git diff --check,
and behavioral tests.
""",
        ),
        (
            "BR-08",
            """
Edit src/cli/daily_run.py and scripts/scheduled_daily_run.py. Treat last_nav_date None
as the normal single-day path; compare the next day catchup_start to target_date and
leave already >= target normal. Group catch-up simulations by model_dir, compute the
earliest start, then call run_date_range_multi once for the group with sim_ids,
start_date and end_date. Preserve normal run_single_day. Read trade_calendar.parquet
cal_date with weekday fallback; --force overrides the non-trading skip. Run py_compile,
git diff --check, and behavioral tests.
""",
        ),
    ],
)
def test_valid_day2_plans_pass(tmp_path: Path, task: str, content: str) -> None:
    result = _run(tmp_path, task, content)
    assert result.returncode == 0, result.stdout
    assert '"earned": 100' in result.stdout


def test_br06_rejects_global_rolling_and_post_filter_exemption(tmp_path: Path) -> None:
    content = """
src/backtest/interfaces.py src/backtest/strategy.py src/backtest/engine.py src/cli/daily_run.py
Use global rolling(10, min_periods=5), then filter and afterwards exempt current_holdings.
amount_adv10 falls back to amount. Pass portfolio.positions as current_holdings.
Route v4 to MultiSource.
Run py_compile, git diff --check, and tests.
"""
    result = _run(tmp_path, "BR-06", content)
    assert result.returncode == 1
    assert '"earned": 0, "error"' in result.stdout


def test_br07_rejects_checkpoint_only_after_epoch_loop(tmp_path: Path) -> None:
    content = """
Change src/components/models.py and src/cli/factorvae_cs_train.py.
Add a distinct factor_logvar_max to prior and posterior, separate from the broad bound.
Propagate through config, model kwargs and --factor-logvar-max default 2.0.
After the epoch loop save one checkpoint with try/except warning as nonfatal.
Write partial kfold_meta.json before training. Run py_compile, git diff --check and tests.
"""
    result = _run(tmp_path, "BR-07", content)
    assert result.returncode == 1
    assert "nonfatal_checkpoint_inside_epoch_loop" in result.stdout


def test_br08_rejects_per_sim_range_calls(tmp_path: Path) -> None:
    content = """
Change src/cli/daily_run.py and scripts/scheduled_daily_run.py.
Classify last_nav_date None as normal, compute catchup_start next day against target_date,
and leave already >= target in normal. Group by model_dir and find the earliest start,
but call run_date_range_multi for each sim with sim_ids, start_date and end_date.
Use trade_calendar.parquet cal_date with weekday fallback and --force skip override.
Preserve normal run_single_day. Run py_compile, git diff --check and tests.
"""
    result = _run(tmp_path, "BR-08", content)
    assert result.returncode == 1
    assert "grouped_earliest_range_catchup" in result.stdout
