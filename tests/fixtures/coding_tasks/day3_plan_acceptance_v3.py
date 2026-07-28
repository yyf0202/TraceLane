"""Multilingual, implementation-shape-neutral Day 3 plan adjudicator.

The frozen v1/v2 gates remain unchanged.  V3 changes only BR-11: equivalent
Chinese semantics and direct helper APIs are accepted, while ordering
contradictions remain rejected.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from day3_plan_acceptance import _all, _any, _br12
from day3_plan_acceptance_v2 import _br10


def _br11(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def qualified_discovery() -> None:
        _all(
            low,
            (
                (r"sim_real",),
                (r"config\.json",),
                (r"state_portfolio\.json",),
                (r"director", r"folder", r"path", r"目录", r"路径"),
            ),
        )

    def sync_before_pipeline() -> None:
        _all(
            low,
            (
                (r"filled.*csv", r"_filled\.csv", r"成交.*csv"),
                (r"sync_real_fills", r"sync_fills", r"同步成交"),
                (
                    r"before .*daily",
                    r"step 0",
                    r"sync.*then.*daily",
                    r"daily_run\s*之前",
                    r"预\s*daily_run",
                ),
                (
                    r"per.sim",
                    r"each sim",
                    r"isolate",
                    r"每个.*sim",
                    r"逐.*sim",
                    r"一个失败不影响",
                    r"继续处理其他",
                ),
                (
                    r"timeout",
                    r"returncode",
                    r"subprocess",
                    r"try/except",
                    r"捕获.*异常",
                    r"失败.*限制",
                ),
            ),
        )
        assert not _any(
            low,
            r"(one|single) .*failure (will |should )?(abort|stop).*(all|daily)",
            r"(abort|stop).*(all|daily) on .*failure",
            r"一个.*失败.*(终止|停止).*(全部|daily)",
        )

    def post_success_next_day() -> None:
        _all(
            low,
            (
                (
                    r"after .*daily",
                    r"daily.*succe",
                    r"pipeline_ok",
                    r"daily_run\s*成功后",
                    r"仅在\s*daily_run\s*成功",
                ),
                (
                    r"next trading day",
                    r"trade calendar",
                    r"cal_date",
                    r"下一交易日",
                    r"交易日历",
                ),
                (
                    r"fallback",
                    r"timedelta",
                    r"next calendar day",
                    r"回退",
                    r"工作日.*递增",
                    r"跳过周六",
                ),
                (
                    r"generate_order_list",
                    r"order list",
                    r"订单列表",
                    r"下单清单",
                ),
            ),
        )
        assert not _any(
            low,
            r"generat.*order.*before .*daily",
            r"daily_run\s*之前.*(订单列表|下单清单)",
        )

    def nav_guards_and_email() -> None:
        _all(
            low,
            (
                (r"cash", r"现金"),
                (r"position", r"持仓"),
                (
                    r"nav",
                    r"marked.*value",
                    r"last_trade_price",
                    r"持仓市值",
                ),
                (r"skip.real.sync",),
                (r"skip.real.orders",),
                (r"subject", r"email", r"邮件主题", r"主题"),
                (r"count", r"数量", r"订单数"),
            ),
        )

    return [
        ("qualified_real_sim_discovery", 20, qualified_discovery),
        ("fill_sync_before_pipeline_with_isolation", 30, sync_before_pipeline),
        ("post_success_next_trading_day_orders", 30, post_success_next_day),
        ("current_nav_skip_guards_and_email_count", 20, nav_guards_and_email),
    ]


def main(plan_path: Path, task_id: str) -> int:
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("plan artifact has no string content")
    builders = {"BR-10": _br10, "BR-11": _br11, "BR-12": _br12}
    outcomes, earned = [], 0
    for name, points, check in builders[task_id](content):
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
        + json.dumps(
            {"earned": earned, "possible": 100, "criteria": outcomes},
            sort_keys=True,
        )
    )
    return 0 if earned == 100 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("task_id", choices=("BR-10", "BR-11", "BR-12"))
    args = parser.parse_args()
    raise SystemExit(main(args.plan.resolve(), args.task_id))
