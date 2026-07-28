"""Functional-slice acceptance for BR-11 scheduled real-trading orchestration."""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    path = repository / "scripts/scheduled_daily_run.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def _find(*terms: str) -> str:
        matches = [
            body for body in functions.values() if all(term in body for term in terms)
        ]
        assert matches, terms
        return max(matches, key=len)

    def real_sim_discovery() -> None:
        body = _find("sim_REAL_", "config.json", "state_portfolio.json")
        assert "is_dir" in body and ("sorted" in body or ".sort" in body)

    def trading_day_resolution() -> None:
        body = _find("TRADE_CAL_PATH", "cal_date", "timedelta")
        assert ">" in body and ("sort_values" in body or "sorted" in body)
        assert "except" in body

    def fill_sync_isolated_per_sim() -> None:
        body = _find("_filled.csv", "sync_real_fills.py", "subprocess.run")
        assert "sim_REAL_" in body or "real_sims" in body
        assert "returncode" in body
        assert "TimeoutExpired" in body or "timeout=" in body
        assert "except" in body and ("continue" in body or "return" in body)

    def order_generation_uses_current_nav() -> None:
        body = _find("generate_order_list.py", "state_portfolio.json", "real-cash")
        for term in ("cash", "positions", "total_amount", "last_trade_price"):
            assert term in body
        assert "subprocess.run" in body and "returncode" in body

    def pipeline_order_and_guards() -> None:
        main_body = functions["main"]
        sync_at = main_body.index("auto_sync_real_fills")
        daily_at = main_body.index("run_daily_pipeline")
        orders_at = main_body.index("auto_generate_real_orders")
        assert sync_at < daily_at < orders_at
        assert "pipeline_ok" in main_body[daily_at:orders_at]
        assert "skip_real_sync" in main_body
        assert "skip_real_orders" in main_body
        assert "real_order_count" in main_body and "subject" in main_body

    return _score(
        "BR-11",
        [
            ("qualified_real_sim_discovery", 10, real_sim_discovery),
            ("next_trading_day_with_fallback", 10, trading_day_resolution),
            ("per_sim_fill_sync_failure_isolation", 20, fill_sync_isolated_per_sim),
            ("order_generation_from_current_nav", 20, order_generation_uses_current_nav),
            ("pipeline_state_ordering_and_skip_guards", 40, pipeline_order_and_guards),
        ],
    )


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
