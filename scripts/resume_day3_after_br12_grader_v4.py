#!/usr/bin/env python3
"""Resume Day 3 using the delayed-expansion-aware BR-12 V4 grader."""

from __future__ import annotations

import argparse
import os

import resume_day3_after_br12_grader_v2 as recovery

COMPLETED = "day3v2-br-12-glm52-r2-direct-build"
INTERRUPTED = "day3v2-br-12-glm52-r2-plan-build"
RESTART_SUFFIX = "operator-restart-br12-grader-v4"
PLAN_GATE = (
    recovery.day3.ROOT
    / "tests/fixtures/coding_tasks/day3_plan_acceptance_v6.py"
)
ADJUDICATOR = (
    recovery.day3.ROOT
    / "tests/fixtures/coding_tasks/br12_hidden_acceptance_v4.py"
)


def _configure() -> None:
    recovery.COMPLETED = COMPLETED
    recovery.INTERRUPTED = INTERRUPTED
    recovery.RESTART_SUFFIX = RESTART_SUFFIX
    recovery.PLAN_GATE = PLAN_GATE
    recovery.ADJUDICATOR = ADJUDICATOR
    recovery.ADJUDICATOR_LABEL = "v4"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    _configure()
    if args.dry_run:
        print(f"ADJUDICATE {COMPLETED}")
        print(f"PRESERVE {INTERRUPTED}")
        print(f"RECOVER {recovery._restart_spec().run_slug}")
        for index, row in enumerate(recovery.remaining_matrix(), 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(recovery.day3.ROOT)
    recovery.adjudicate(COMPLETED)
    interruption = recovery.preserve_interruption()
    recovery.recover_plan_build(interruption)
    for row in recovery.remaining_matrix():
        recovery.execution.execute(row, plan_gate=PLAN_GATE)
        if row.task.short_id == "BR-12":
            recovery.adjudicate(row.run_slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
