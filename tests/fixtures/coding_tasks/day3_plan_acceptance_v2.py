"""Versioned Day 3 plan-gate adjudicator.

The frozen v1 gate remains unchanged.  V2 accepts the common ``A/C/M`` notation
for added, copied and modified files while retaining the contradiction checks.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from day3_plan_acceptance import _all, _any, _br11, _br12


def _br10(content: str) -> list[tuple[str, int, Callable[[], None]]]:
    low = content.lower()

    def cross_platform_scope() -> None:
        _all(
            low,
            (
                (r"sync_data\.sh", r"posix", r"shell"),
                (r"sync_data\.bat", r"windows", r"batch"),
                (r"incremental",),
                (r"stock", r"data repo"),
            ),
        )

    def push_existing_ahead_commits() -> None:
        _all(
            low,
            (
                (r"push",),
                (r"no (new )?(change|commit)", r"nothing .*commit", r"ahead commit"),
                (
                    r"commit .*conditional",
                    r"commit (only|if)",
                    r"only .*commit",
                    r"only if .*commit",
                ),
            ),
        )
        assert not _any(
            low,
            r"(skip|avoid|do not|don't) (the )?push .*no (new )?change",
            r"push only .*new (change|commit)",
        )

    def staged_large_file_preflight() -> None:
        _all(
            low,
            (
                (r"staged", r"cached"),
                (r"100\s*(mib|mb)", r"104857600"),
                (
                    r"added.*copied.*modified",
                    r"\ba\s*/\s*c\s*/\s*m(?:\s*/\s*r)?\b",
                    r"\bacmr?\b",
                    r"diff-filter",
                ),
                (r"stat .*-c", r"linux"),
                (r"stat .*-f", r"macos", r"darwin"),
                (r"%%~z", r"batch.*size", r"windows.*size"),
            ),
        )

    def abort_and_date_semantics() -> None:
        _all(
            low,
            (
                (r"abort", r"skip .*stock.*push", r"prevent .*stock.*push"),
                (r"independent", r"incremental .*continue", r"stock .*only"),
                (r"!datestr!", r"delayed .*expansion"),
                (r"git diff --check", r"bash -n", r"validation"),
            ),
        )
        assert not _any(low, r"abort (the )?(entire|both) .*flow")

    return [
        ("cross_platform_independent_flows", 20, cross_platform_scope),
        ("push_existing_ahead_commits", 30, push_existing_ahead_commits),
        ("staged_large_file_preflight", 30, staged_large_file_preflight),
        ("abort_scope_date_and_validation", 20, abort_and_date_semantics),
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
