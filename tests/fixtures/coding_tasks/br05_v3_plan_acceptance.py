"""Independent artifact gate for a BR-05 v3 implementation plan."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path


def main(plan_path: Path) -> int:
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("plan artifact has no string content")
    lowered = content.lower()
    criteria: list[dict[str, object]] = []

    def check(name: str, points: int, callback: Callable[[], None]) -> None:
        try:
            callback()
        except Exception as exc:  # noqa: BLE001 - report every independent gate
            criteria.append(
                {
                    "name": name,
                    "points": points,
                    "earned": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        else:
            criteria.append({"name": name, "points": points, "earned": points})

    def scope() -> None:
        for path in (
            "src/backtest/engine.py",
            "src/components/models.py",
            "src/components/target_generator.py",
        ):
            assert path in content, path

    def execution_timing() -> None:
        assert "t+1" in lowered
        assert any(term in lowered for term in ("first day", "first-day", "首日"))
        assert any(term in lowered for term in ("previous signal", "prior signal", "昨日信号"))
        assert any(term in lowered for term in ("no trade", "skip", "不交易", "跳过"))

    def target_window() -> None:
        assert any(term in lowered for term in ("ranking", "crosssectional", "横截面"))
        assert "ohlc" in lowered
        assert "t+2" in lowered
        assert any(term in lowered for term in ("shift(-2)", "shift(-2", "t+1→t+2", "t+1 -> t+2"))

    def film_stepwise_static() -> None:
        windows = [
            lowered[max(0, match.start() - 160) : match.start() + 1800]
            for match in re.finditer(r"\bfilm\b", lowered)
        ]
        assert windows, "FiLM is not discussed"
        assert any(_valid_film_window(window) for window in windows), (
            "FiLM must preserve static context and gamma/beta as [B,T,*]; "
            "a [B,1,D] broadcast or time-reduced static tensor is not stepwise modulation"
        )

    def bottleneck_context() -> None:
        assert any(term in lowered for term in ("bottleneck", "context token"))
        assert any(term in lowered for term in ("mean(dim=1)", "sequence mean", "序列均值"))

    def validation() -> None:
        assert "py_compile" in lowered
        assert "git diff --check" in lowered
        assert any(term in lowered for term in ("test", "验收", "验证"))

    check("editable_scope", 20, scope)
    check("engine_execution_timing", 25, execution_timing)
    check("target_holding_window", 20, target_window)
    check("film_stepwise_static", 15, film_stepwise_static)
    check("bottleneck_context", 10, bottleneck_context)
    check("validation_plan", 10, validation)
    earned = sum(int(item["earned"]) for item in criteria)
    print(
        "TRACELANE_PLAN_SCORE="
        + json.dumps({"earned": earned, "possible": 100, "criteria": criteria}, sort_keys=True)
    )
    return 0 if earned == 100 else 1


def _valid_film_window(window: str) -> bool:
    stepwise = any(term in window for term in ("per-timestep", "per timestep", "逐时间步"))
    static_time_shape = any(
        term in window
        for term in (
            "[b, t, n_static]",
            "[b,t,n_static]",
            "[b, seq_len, n_static]",
            "[b,seq_len,n_static]",
            "x[:, :, -n_static:]",
        )
    )
    modulation_time_shape = any(
        term in window
        for term in (
            "[b, t, d_model]",
            "[b,t,d_model]",
            "[b, seq_len, d_model]",
            "[b,seq_len,d_model]",
        )
    )
    named_parameters = "gamma" in window and "beta" in window
    time_reduced_static = re.search(
        r"x_static\s*=.{0,160}(?:mean\s*\(\s*dim\s*=\s*1\s*\)|\[:,\s*-1\s*,)",
        window,
        flags=re.DOTALL,
    )
    singleton_modulation = re.search(
        r"(?:gamma|beta).{0,120}\[b,\s*1,\s*(?:d|d_model)\]",
        window,
        flags=re.DOTALL,
    )
    broadcast_claim = re.search(
        r"(?:broadcast.{0,120}(?:per[- ]timestep|逐时间步)|"
        r"(?:per[- ]timestep|逐时间步).{0,120}broadcast)",
        window,
        flags=re.DOTALL,
    )
    return (
        stepwise
        and static_time_shape
        and modulation_time_shape
        and named_parameters
        and time_reduced_static is None
        and singleton_modulation is None
        and broadcast_claim is None
    )


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))
