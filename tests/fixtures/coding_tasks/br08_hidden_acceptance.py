"""Functional-slice acceptance for BR-08 scheduled catch-up semantics."""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    daily = (repository / "src/cli/daily_run.py").read_text(encoding="utf-8")
    scheduled = (repository / "scripts/scheduled_daily_run.py").read_text(encoding="utf-8")

    def calendar_exact_and_fallback() -> None:
        tree = ast.parse(scheduled)
        funcs = {
            n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        node = funcs["is_trading_day"]
        body = ast.get_source_segment(scheduled, node) or ""
        assert "trade_calendar.parquet" in scheduled
        assert "cal_date" in body
        assert "weekday() < 5" in body

    def force_and_skip() -> None:
        assert '"--force"' in scheduled
        assert "not args.force and not is_trading_day(today_str)" in scheduled
        assert (
            "return 0"
            in scheduled[scheduled.index("not args.force and not is_trading_day(today_str)") :][
                :400
            ]
        )

    def state_classification() -> None:
        assert "get_last_nav_date()" in daily
        assert "last_nav_date is None" in daily
        assert "catchup_start < target_date" in daily
        assert "normal_groups" in daily and "catchup_sims" in daily

    def grouped_earliest_range() -> None:
        assert "earliest_start = min(s[2] for s in sim_list)" in daily
        call = daily.index("runner.run_date_range_multi", daily.index("Phase A:"))
        window = daily[call : call + 900]
        assert "sim_ids=sim_ids_in_group" in window
        assert "start_date=earliest_start" in window
        assert "end_date=target_date" in window
        assert "results.append" in daily[call : call + 1600]

    def normal_path_preserved() -> None:
        assert "total_normal" in daily
        assert "run_single_day" in daily
        assert daily.index("engine.run_single_day") > daily.index("Phase B:")

    checks = [
        ("calendar_exact_and_weekday_fallback", 30, calendar_exact_and_fallback),
        ("force_override_and_nontrading_skip", 15, force_and_skip),
        ("state_based_catchup_classification", 20, state_classification),
        ("grouped_earliest_range_execution", 25, grouped_earliest_range),
        ("normal_single_day_path_preserved", 10, normal_path_preserved),
    ]
    return _score("BR-08", checks)


def _score(task: str, checks: list[tuple[str, int, Callable[[], None]]]) -> int:
    outcomes, earned = [], 0
    for name, points, check in checks:
        try:
            check()
        except Exception as exc:  # noqa: BLE001
            outcomes.append(
                {
                    "name": name,
                    "points": points,
                    "earned": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            earned += points
            outcomes.append({"name": name, "points": points, "earned": points})
    print(
        "TRACELANE_SCORE="
        + json.dumps({"earned": earned, "possible": 100, "criteria": outcomes}, sort_keys=True)
    )
    if earned == 100:
        print(f"{task} independent acceptance passed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
