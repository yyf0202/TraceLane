"""Behavioral acceptance for BR-06 ADV10 liquidity semantics."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path


def main(repository: Path) -> int:
    sys.path.insert(0, str(repository))

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

        for keyword in ("current_holdings", "holdings"):
            strategy = TargetNAVRotationStrategy(top_n=2, min_daily_amount千元=100)
            strategy.on_data_ready(
                "20250102",
                {"held": {"amount": 1, "amount_adv10": 1}, "new": {"amount_adv10": 200}},
                {"held": 2.0, "new": 1.0},
                **{keyword: {"held"}},
            )
            if strategy._target_symbols == {"held", "new"}:
                return
        raise AssertionError("strategy does not accept a holding-state handoff")

    def engine_handoff() -> None:
        from src.backtest.engine import BacktestEngine

        class Feeder:
            def get_data(self, date: str) -> dict[str, object]:
                return {"held": {"amount": 1, "amount_adv10": 1}}

            def get_adv10_map(self, date: str) -> dict[str, float]:
                return {"held": 1}

        class Strategy:
            def __init__(self) -> None:
                self.kwargs: list[dict[str, object]] = []

            def on_data_ready(self, *args: object, **kwargs: object) -> None:
                self.kwargs.append(kwargs)

            def generate_sell_orders(self, portfolio: object) -> list[object]:
                return []

            def generate_buy_orders(self, portfolio: object) -> list[object]:
                return []

        class Portfolio:
            initial_cash = 1_000_000.0
            cash = initial_cash

            def __init__(self) -> None:
                self.positions = {"held": object()}

            def settle_daily(self, market: object, date: str) -> None:
                pass

            def get_total_value(self, market: object) -> float:
                return self.initial_cash

            def get_position_count(self) -> int:
                return 1

        class Analyzer:
            def record_daily_nav(self, date: str, nav: float) -> None:
                pass

        strategy = Strategy()
        engine = BacktestEngine.__new__(BacktestEngine)
        engine.trading_dates = ["20250102", "20250103"]
        engine.data_feeder = Feeder()
        engine.signal_feeder = Feeder()
        engine.thermometer = None
        engine.strategy = strategy
        engine.portfolio = Portfolio()
        engine.broker = object()
        engine.analyzer = Analyzer()
        engine.run()

        assert len(strategy.kwargs) == 1
        state = strategy.kwargs[0].get("current_holdings", strategy.kwargs[0].get("holdings"))
        assert state == {"held"}

    def datasource_and_default() -> None:
        daily = (repository / "src/cli/daily_run.py").read_text(encoding="utf-8")
        engine = (repository / "src/backtest/engine.py").read_text(encoding="utf-8")
        assert '"v4"' in daily[daily.index("needs_v2_data") : daily.index("needs_v2_data") + 180]
        assert "min_daily_amount千元: float = 30000.0" in engine

    checks = [
        ("per_symbol_adv10", 20, feeder),
        ("adv10_filter_and_amount_fallback", 20, adv10_filter_and_fallback),
        ("current_holding_exemption", 20, holding_exemption),
        ("engine_holding_state_handoff", 20, engine_handoff),
        ("v4_datasource_and_default", 20, datasource_and_default),
    ]
    return _score("BR-06 v2", checks)


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
