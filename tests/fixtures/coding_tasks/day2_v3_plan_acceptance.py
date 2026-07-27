"""Day 2 v3 plan gates with implementation-equivalent BR-08 checks."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

from day2_plan_acceptance import _br07, _terms
from day2_v2_plan_acceptance import _br06


def _br08(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def scope() -> None:
        assert "src/cli/daily_run.py" in content
        assert "scripts/scheduled_daily_run.py" in content

    def classification() -> None:
        _terms(low, ("last_nav_date",), ("none", "never run", "从未运行"))
        _terms(low, ("target_date",), ("already", "已跑", ">="))
        _terms(
            low,
            (
                "catchup_start",
                "next day",
                "next trading day",
                "next_trading_day",
                "_next_trading_day",
                "required_start",
                "missing[0]",
                "(last_nav, target_date]",
                "下一天",
                "下一交易日",
            ),
        )
        _terms(low, ("normal", "single-day", "single day", "单日", "fast path"))

    def grouping() -> None:
        _terms(low, ("model_dir",), ("group", "分组"), ("earliest", "min(", "最早"))
        _terms(low, ("run_date_range_multi",), ("sim_ids",), ("start_date",), ("end_date",))
        assert not re.search(
            r"(?:run_date_range_multi[\s\S]{0,200}(?:for\s+each\s+sim|逐个 sim|每个模拟盘)|"
            r"(?:for\s+each\s+sim|逐个 sim|每个模拟盘)[\s\S]{0,200}run_date_range_multi)",
            low,
        )

    def schedule() -> None:
        _terms(low, ("trade_calendar.parquet",), ("cal_date",), ("weekday", "工作日"))
        _terms(low, ("--force",), ("skip", "跳过", "bypass", "override"))

    def normal() -> None:
        _terms(
            low,
            (
                "run_single_day",
                "phase_tick_all",
                "single-day fast path",
                "single day fast path",
                "单日快路径",
            ),
            ("preserve", "保留", "normal", "existing", "原有", "new/current/already"),
        )

    def validation() -> None:
        _terms(low, ("py_compile",), ("git diff --check",), ("test", "验收", "验证"))

    return [
        ("editable_scope", 15, scope),
        ("state_and_time_classification", 25, classification),
        ("grouped_earliest_range_catchup", 30, grouping),
        ("calendar_fallback_and_force", 15, schedule),
        ("normal_path_preserved", 10, normal),
        ("validation", 5, validation),
    ]


def main(plan_path: Path, task_id: str) -> int:
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("plan artifact has no string content")
    builders = {"BR-06": _br06, "BR-07": _br07, "BR-08": _br08}
    checks = builders[task_id](content)
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
        "TRACELANE_PLAN_SCORE="
        + json.dumps({"earned": earned, "possible": 100, "criteria": outcomes}, sort_keys=True)
    )
    return 0 if earned == 100 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("task_id", choices=("BR-06", "BR-07", "BR-08"))
    args = parser.parse_args()
    raise SystemExit(main(args.plan.resolve(), args.task_id))
