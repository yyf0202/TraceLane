"""Day 2 v2 plan gates with semantic BR-06 rolling/fallback checks."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

from day2_plan_acceptance import _br07, _br08, _terms


def _br06(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def scope() -> None:
        for path in (
            "src/backtest/interfaces.py",
            "src/backtest/strategy.py",
            "src/backtest/engine.py",
            "src/cli/daily_run.py",
        ):
            assert path in content

    def rolling() -> None:
        _terms(low, ("groupby", "per-symbol", "per symbol", "逐股票"))
        _terms(low, ("sort", "排序"), ("min_periods=5", "min periods 5"))
        _terms(
            low,
            (
                "rolling(10",
                "rolling(window=adv10_window",
                "rolling(window = adv10_window",
                "10-day",
                "10-session",
                "10 session",
                "10日",
                "10 个交易日",
            ),
        )
        assert not re.search(r"(?:global|全局).{0,80}rolling", low)

    def filter_semantics() -> None:
        _terms(low, ("amount_adv10",), ("fallback", "回退", "兼容"), ("amount",))
        _terms(low, ("current_holdings",), ("exempt", "豁免"))
        assert not re.search(r"(?:filter|过滤).{0,120}(?:then|之后).{0,120}(?:exempt|豁免)", low)

        missing_branch = re.compile(
            r"if[^\n]{0,180}(?:adv10[^\n]{0,80}(?:none|nan)|"
            r"(?:none|nan)[^\n]{0,80}adv10)[^\n]*:"
            r"\n[ \t]+(?P<body>[^\n]+)",
            re.IGNORECASE,
        )
        for match in missing_branch.finditer(content):
            body = match.group("body")
            contradictory = re.search(
                r"(?:effective_amount|liquidity_amount)\s*=\s*adv10\b",
                body,
                re.IGNORECASE,
            )
            assert not contradictory, "missing ADV10 branch must fall back to amount"

    def handoff_and_version() -> None:
        _terms(low, ("portfolio.positions", "positions.keys"), ("current_holdings",))
        _terms(low, ("v4",), ("multisource", "v2 data", "增强数据"))

    def validation() -> None:
        _terms(low, ("py_compile",), ("git diff --check",), ("test", "验收", "验证"))

    return [
        ("editable_scope", 20, scope),
        ("per_symbol_sorted_adv10", 25, rolling),
        ("fallback_and_pre_filter_holding_exemption", 25, filter_semantics),
        ("engine_state_handoff_and_v4_routing", 20, handoff_and_version),
        ("validation", 10, validation),
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
