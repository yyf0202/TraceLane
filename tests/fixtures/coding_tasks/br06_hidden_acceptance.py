"""Functional-slice acceptance for BR-06 ADV10 liquidity semantics."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    sys.path.insert(0, str(repository))
    checks: list[tuple[str, int, Callable[[], None]]] = []

    def feeder() -> None:
        import pandas as pd
        from src.backtest.interfaces import DataFrameDataFeeder

        frame = pd.DataFrame(
            {
                "symbol": ["B"] * 5 + ["A"] * 5,
                "trade_date": ["20250105", "20250104", "20250103", "20250102", "20250101"] * 2,
                "amount": [50, 40, 30, 20, 10, 5, 4, 3, 2, 1],
            }
        )
        value = DataFrameDataFeeder(frame).get_data("20250105")
        assert value["B"]["amount_adv10"] == 30
        assert value["A"]["amount_adv10"] == 3

    def adv10_filter_and_fallback() -> None:
        from src.backtest.strategy import TargetNAVRotationStrategy

        strategy = TargetNAVRotationStrategy(top_n=2, min_daily_amount千元=100)
        strategy.on_data_ready(
            "20250102",
            {
                "adv": {"amount": 1, "amount_adv10": 150},
                "fallback": {"amount": 120},
                "illiquid": {"amount": 200, "amount_adv10": 50},
            },
            {"adv": 3.0, "fallback": 2.0, "illiquid": 1.0},
        )
        assert strategy._target_symbols == {"adv", "fallback"}

    def holding_exemption() -> None:
        from src.backtest.strategy import TargetNAVRotationStrategy

        strategy = TargetNAVRotationStrategy(top_n=2, min_daily_amount千元=100)
        strategy.on_data_ready(
            "20250102",
            {"held": {"amount": 1, "amount_adv10": 1}, "new": {"amount_adv10": 200}},
            {"held": 2.0, "new": 1.0},
            current_holdings={"held"},
        )
        assert strategy._target_symbols == {"held", "new"}

    def engine_handoff() -> None:
        source = (repository / "src/backtest/engine.py").read_text(encoding="utf-8")
        assert "current_holdings=set(self.portfolio.positions.keys())" in source

    def datasource_and_default() -> None:
        daily = (repository / "src/cli/daily_run.py").read_text(encoding="utf-8")
        engine = (repository / "src/backtest/engine.py").read_text(encoding="utf-8")
        assert '"v4"' in daily[daily.index("needs_v2_data") : daily.index("needs_v2_data") + 180]
        assert "min_daily_amount千元: float = 30000.0" in engine

    checks.extend(
        [
            ("per_symbol_adv10", 20, feeder),
            ("adv10_filter_and_amount_fallback", 20, adv10_filter_and_fallback),
            ("current_holding_exemption", 20, holding_exemption),
            ("engine_holding_state_handoff", 20, engine_handoff),
            ("v4_datasource_and_default", 20, datasource_and_default),
        ]
    )
    return _score("BR-06", checks)


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
