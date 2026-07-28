"""Control-flow-code-aware BR-12 plan gate.

V5 accepted more natural-language blocking verbs but still rejected plans that
specified the short circuit directly as POSIX ``set -e`` and Windows
``if errorlevel ... goto/exit`` control flow.  V6 accepts those executable
semantics while retaining the contradiction guard.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

import day3_plan_acceptance_v5 as v5
from day3_plan_acceptance import _all, _any


def _br12(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = v5.v4._normalize_markdown(content)
    checks = v5._br12(low)

    def short_circuit_control() -> None:
        _all(
            low,
            (
                (r"main.only",),
                (r"skips? (the )?control", r"do not .*control"),
                (
                    r"main .*fail",
                    r"fail(?:ed|ure)? .*main",
                    r"non.zero",
                    r"errorlevel",
                    r"set -e",
                ),
                (
                    r"prevent .*control",
                    r"stop .*before .*control",
                    r"short.circuit",
                    r"block(?:s|ed|ing)? (?:the )?control",
                    r"control .*will not (?:start|run)",
                    r"control .*does not (?:start|run)",
                    r"set -e",
                    r"errorlevel[\s\S]{0,100}(?:goto|exit\s*/b)",
                ),
                (r"py_compile", r"bash -n", r"validation"),
            ),
        )
        assert not _any(low, r"(always|regardless).*run .*control")

    return [
        (name, points, short_circuit_control if name.startswith("main_only") else check)
        for name, points, check in checks
    ]


def main(plan_path: Path, task_id: str) -> int:
    value = json.loads(plan_path.read_text(encoding="utf-8"))
    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("plan artifact has no string content")
    builders = {"BR-10": v5._br10, "BR-11": v5.v4._br11, "BR-12": _br12}
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
