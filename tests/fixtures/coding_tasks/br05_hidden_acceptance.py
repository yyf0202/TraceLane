"""Behavioral acceptance and functional score for BeRicher task BR-05."""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Criterion:
    name: str
    points: int
    check: Callable[[], None]


def _load(repository: Path) -> tuple[object, object, object]:
    sys.path.insert(0, str(repository))
    from src.backtest.engine import BacktestEngine
    from src.components.models import BottleneckFusionModel, FiLMBottleneckFusionModel
    from src.components.target_generator import (
        CrossSectionalRankingTarget,
        OHLCRegressionTarget,
    )

    return (
        BacktestEngine,
        (BottleneckFusionModel, FiLMBottleneckFusionModel),
        (CrossSectionalRankingTarget, OHLCRegressionTarget),
    )


def _engine_check(engine_class: type) -> None:
    class Feeder:
        def __init__(self, values: dict[str, object]) -> None:
            self.values = values

        def get_data(self, date: str) -> object:
            return self.values[date]

    class Strategy:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def on_data_ready(
            self,
            date: str,
            market: object,
            scores: object,
            *,
            temperature_ratio: float,
        ) -> None:
            assert temperature_ratio == 1.0
            self.calls.append((date, scores))

        def generate_sell_orders(self, portfolio: object) -> list[object]:
            return []

        def generate_buy_orders(self, portfolio: object) -> list[object]:
            return []

    class Portfolio:
        initial_cash = 1_000_000.0
        cash = initial_cash

        def __init__(self) -> None:
            self.settled: list[str] = []

        def settle_daily(self, market: object, date: str) -> None:
            self.settled.append(date)

        def get_total_value(self, market: object) -> float:
            return self.initial_cash

        def get_position_count(self) -> int:
            return 0

    class Analyzer:
        def __init__(self) -> None:
            self.dates: list[str] = []

        def record_daily_nav(self, date: str, nav: float) -> None:
            assert nav == 1_000_000.0
            self.dates.append(date)

    dates = ["20250102", "20250103", "20250106"]
    signals = {date: {"symbol": index + 1.0} for index, date in enumerate(dates)}
    strategy = Strategy()
    portfolio = Portfolio()
    analyzer = Analyzer()
    engine = engine_class.__new__(engine_class)
    engine.trading_dates = dates
    engine.data_feeder = Feeder({date: {"close": index} for index, date in enumerate(dates)})
    engine.signal_feeder = Feeder(signals)
    engine.thermometer = None
    engine.strategy = strategy
    engine.portfolio = portfolio
    engine.broker = object()
    engine.analyzer = analyzer

    result = engine.run()

    assert strategy.calls == [
        ("20250103", signals["20250102"]),
        ("20250106", signals["20250103"]),
    ], strategy.calls
    assert portfolio.settled == dates
    assert analyzer.dates == dates
    assert result["total_orders"] == 0


def _ranking_target_check(target_class: type) -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {
            "trade_date": ["d1", "d2", "d3", "d4"] * 2,
            "symbol": ["A"] * 4 + ["B"] * 4,
            # T→T+1 says A wins on d1; T+1→T+2 says B wins on d1.
            "close": [10.0, 20.0, 10.0, 10.0, 10.0, 11.0, 22.0, 22.0],
        }
    )
    result = target_class(min_stocks_per_day=2).generate(frame)
    assert len(result) == 4
    assert set(result["trade_date"]) == {"d1", "d2"}
    day_one = result[result["trade_date"] == "d1"].set_index("symbol")["target"]
    assert day_one["B"] > day_one["A"], day_one.to_dict()


def _ohlc_target_check(target_class: type) -> None:
    import pandas as pd

    prices = [10.0, 20.0, 10.0, 40.0]
    frame = pd.DataFrame(
        {
            "trade_date": ["d1", "d2", "d3", "d4"],
            "symbol": ["A"] * 4,
            "open": prices,
            "high": [value + 1.0 for value in prices],
            "low": [value - 1.0 for value in prices],
            "close": prices,
        }
    )
    result = target_class().generate(frame)
    assert len(result) == 2
    assert list(result["trade_date"]) == ["d1", "d2"]
    assert math.isclose(result.iloc[0]["target_3"], math.log(10.0 / 20.0))
    assert math.isclose(result.iloc[1]["target_3"], math.log(40.0 / 10.0))


def _film_check(model_class: type) -> None:
    import torch

    model = model_class(
        input_dim=4,
        output_dim=1,
        n_static_features=2,
        d_model=8,
        nhead=2,
        num_layers=1,
        dim_feedforward=16,
        dropout=0.0,
        max_len=8,
        static_hidden_dim=4,
    )
    observed: list[object] = []
    hook = model.film_generator.register_forward_pre_hook(
        lambda _module, inputs: observed.append(inputs[0].detach().clone())
    )
    sample = torch.tensor(
        [[[0.0, 0.0, 1.0, 10.0], [0.0, 0.0, 2.0, 20.0], [0.0, 0.0, 3.0, 30.0]]]
    )
    with torch.no_grad():
        model(sample)
    hook.remove()
    assert len(observed) == 1
    assert tuple(observed[0].shape) == (1, 3, 2)
    assert torch.equal(observed[0], sample[:, :, -2:])


def _bottleneck_check(model_class: type) -> None:
    import torch

    model = model_class(
        input_dim=4,
        output_dim=1,
        n_static_features=2,
        d_model=8,
        nhead=2,
        num_layers=1,
        dim_feedforward=16,
        dropout=0.0,
        max_len=8,
        static_hidden_dim=4,
    )
    observed: list[object] = []
    hook = model.static_projector.register_forward_pre_hook(
        lambda _module, inputs: observed.append(inputs[0].detach().clone())
    )
    sample = torch.tensor(
        [[[0.0, 0.0, 1.0, 10.0], [0.0, 0.0, 3.0, 30.0], [0.0, 0.0, 5.0, 50.0]]]
    )
    with torch.no_grad():
        model(sample)
    hook.remove()
    expected = sample[:, :, -2:].mean(dim=1)
    assert len(observed) == 1
    assert torch.equal(observed[0], expected)


def main(repository: Path) -> int:
    engine_class, model_classes, target_classes = _load(repository)
    bottleneck_class, film_class = model_classes
    ranking_class, ohlc_class = target_classes
    criteria = (
        Criterion("engine_t1_execution", 30, lambda: _engine_check(engine_class)),
        Criterion("ranking_t1_to_t2_label", 20, lambda: _ranking_target_check(ranking_class)),
        Criterion("ohlc_t1_to_t2_label", 20, lambda: _ohlc_target_check(ohlc_class)),
        Criterion("film_stepwise_static", 20, lambda: _film_check(film_class)),
        Criterion("bottleneck_sequence_context", 10, lambda: _bottleneck_check(bottleneck_class)),
    )
    outcomes: list[dict[str, object]] = []
    earned = 0
    for criterion in criteria:
        try:
            criterion.check()
        except Exception as exc:  # noqa: BLE001 - grader must report every criterion
            outcomes.append(
                {
                    "name": criterion.name,
                    "points": criterion.points,
                    "earned": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            earned += criterion.points
            outcomes.append(
                {
                    "name": criterion.name,
                    "points": criterion.points,
                    "earned": criterion.points,
                }
            )

    score = {"earned": earned, "possible": 100, "criteria": outcomes}
    print(f"TRACELANE_SCORE={json.dumps(score, sort_keys=True)}")
    if earned != 100:
        return 1
    print("BR-05 independent acceptance passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
