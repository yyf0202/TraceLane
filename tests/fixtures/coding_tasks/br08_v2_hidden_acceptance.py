"""Implementation-equivalent acceptance for BR-08 scheduled catch-up semantics."""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Callable
from pathlib import Path


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def main(repository: Path) -> int:
    daily = (repository / "src/cli/daily_run.py").read_text(encoding="utf-8")
    scheduled = (repository / "scripts/scheduled_daily_run.py").read_text(encoding="utf-8")
    daily_tree = ast.parse(daily)
    scheduled_tree = ast.parse(scheduled)

    def calendar_exact_and_fallback() -> None:
        candidates = [
            node
            for node in ast.walk(scheduled_tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and "trade_calendar.parquet"
            in (ast.get_source_segment(scheduled, node) or "")
        ]
        assert candidates
        body = (ast.get_source_segment(scheduled, candidates[0]) or "").lower()
        assert "cal_date" in body
        assert "weekday" in body
        assert "except" in body or "exists" in body or "fallback" in body

    def force_and_skip() -> None:
        assert any(
            isinstance(node, ast.Constant) and node.value == "--force"
            for node in ast.walk(scheduled_tree)
        )
        guarded_skips = []
        for node in ast.walk(scheduled_tree):
            if not isinstance(node, ast.If):
                continue
            condition = ast.unparse(node.test).lower()
            body = " ".join(ast.unparse(item).lower() for item in node.body)
            if (
                "force" in condition
                and ("trading" in condition or "trade" in condition)
                and ("return" in body or "exit" in body)
            ):
                guarded_skips.append(node)
        assert guarded_skips

    def state_classification() -> None:
        assert any(
            isinstance(node, ast.Call) and _call_name(node) == "get_last_nav_date"
            for node in ast.walk(daily_tree)
        )
        comparisons = [
            ast.unparse(node).lower()
            for node in ast.walk(daily_tree)
            if isinstance(node, ast.Compare)
        ]
        assert any("last_nav" in text and "none" in text for text in comparisons)
        assert any(
            "target_date" in text
            and ("catchup" in text or "last_nav" in text)
            and any(operator in text for operator in ("<", ">=", "=="))
            for text in comparisons
        )

    def grouped_earliest_range() -> None:
        calls = [
            node
            for node in ast.walk(daily_tree)
            if isinstance(node, ast.Call) and _call_name(node) == "run_date_range_multi"
        ]
        assert len(calls) == 1
        call = calls[0]
        keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in call.keywords}
        assert {"sim_ids", "start_date", "end_date"} <= keywords.keys()
        assert "target_date" in keywords["end_date"]

        ancestors = [
            node
            for node in ast.walk(daily_tree)
            if isinstance(node, ast.For)
            and node.lineno <= call.lineno <= (node.end_lineno or node.lineno)
        ]
        assert ancestors
        enclosing = min(ancestors, key=lambda node: (node.end_lineno or node.lineno) - node.lineno)
        loop_text = (ast.get_source_segment(daily, enclosing) or "").lower()
        assert "model" in loop_text or "group" in loop_text
        assert "min(" in loop_text or "earliest" in loop_text
        assert "results" in loop_text

    def normal_path_preserved() -> None:
        normal_calls = [
            node
            for node in ast.walk(daily_tree)
            if isinstance(node, ast.Call) and _call_name(node) == "run_single_day"
        ]
        assert normal_calls
        assert any(
            isinstance(node, (ast.For, ast.If))
            and node.lineno <= normal_calls[0].lineno <= (node.end_lineno or node.lineno)
            for node in ast.walk(daily_tree)
        )

    checks = [
        ("calendar_exact_and_weekday_fallback", 30, calendar_exact_and_fallback),
        ("force_override_and_nontrading_skip", 15, force_and_skip),
        ("state_based_catchup_classification", 20, state_classification),
        ("grouped_earliest_range_execution", 25, grouped_earliest_range),
        ("normal_single_day_path_preserved", 10, normal_path_preserved),
    ]
    return _score("BR-08 v2", checks)


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
