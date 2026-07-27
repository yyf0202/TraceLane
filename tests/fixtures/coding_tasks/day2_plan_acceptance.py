"""Independent semantic plan gates for the Day 2 BeRicher tasks."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path


def _terms(text: str, *groups: tuple[str, ...]) -> None:
    for group in groups:
        assert any(term in text for term in group), group


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
        _terms(
            low, ("groupby", "per-symbol", "per symbol", "逐股票"), ("rolling(10", "10-day", "10日")
        )
        _terms(low, ("sort", "排序"), ("min_periods=5", "min periods 5"))
        assert not re.search(r"(?:global|全局).{0,80}rolling", low)

    def filter_semantics() -> None:
        _terms(low, ("amount_adv10",), ("fallback", "回退", "兼容"), ("amount",))
        _terms(low, ("current_holdings",), ("exempt", "豁免"))
        assert not re.search(r"(?:filter|过滤).{0,120}(?:then|之后).{0,120}(?:exempt|豁免)", low)

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


def _br07(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def scope() -> None:
        assert "src/components/models.py" in content
        assert "src/cli/factorvae_cs_train.py" in content

    def bound() -> None:
        _terms(
            low,
            ("factor_logvar_max",),
            ("prior",),
            ("posterior",),
            ("separate", "distinct", "独立", "不同"),
        )
        assert not re.search(
            r"(?:reuse|same|共享|复用).{0,80}(?:sigma_logvar_max|broad logvar)", low
        )

    def propagation() -> None:
        _terms(
            low,
            ("config",),
            ("model kwargs", "model_kwargs", "传递", "pass"),
            ("--factor-logvar-max",),
        )
        _terms(low, ("default", "默认"), ("2.0",))

    def checkpoint() -> None:
        _terms(
            low,
            ("each epoch", "per-epoch", "每个 epoch", "每-epoch"),
            ("inside", "循环内", "epoch loop"),
        )
        _terms(low, ("try",), ("except",), ("nonfatal", "non-fatal", "非致命", "warning"))

    def metadata() -> None:
        _terms(low, ("kfold_meta.json",), ("before", "提前", "训练前"), ("partial",))

    def validation() -> None:
        _terms(low, ("py_compile",), ("git diff --check",), ("test", "验收", "验证"))

    return [
        ("editable_scope", 15, scope),
        ("distinct_factor_variance_bound", 25, bound),
        ("config_cli_model_propagation", 20, propagation),
        ("nonfatal_checkpoint_inside_epoch_loop", 25, checkpoint),
        ("partial_metadata_before_fold_training", 10, metadata),
        ("validation", 5, validation),
    ]


def _br08(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def scope() -> None:
        assert "src/cli/daily_run.py" in content
        assert "scripts/scheduled_daily_run.py" in content

    def classification() -> None:
        _terms(low, ("last_nav_date",), ("none", "never run", "从未运行"), ("normal", "单日"))
        _terms(low, ("catchup_start", "next day", "下一天"), ("target_date",))
        _terms(low, ("already", "已跑", ">="), ("normal", "skip", "单日"))

    def grouping() -> None:
        _terms(low, ("model_dir",), ("group", "分组"), ("earliest", "min(", "最早"))
        _terms(low, ("run_date_range_multi",), ("sim_ids",), ("start_date",), ("end_date",))
        assert not re.search(
            r"run_date_range_multi.{0,200}(?:for each sim|逐个 sim|每个模拟盘)", low
        )

    def schedule() -> None:
        _terms(low, ("trade_calendar.parquet",), ("cal_date",), ("weekday", "工作日"))
        _terms(low, ("--force",), ("skip", "跳过"))

    def normal() -> None:
        _terms(low, ("run_single_day",), ("preserve", "保留", "normal", "原有"))

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
