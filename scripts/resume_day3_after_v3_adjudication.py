#!/usr/bin/env python3
"""Resume Day 3 after the helper-abstraction grader diagnosis."""

from __future__ import annotations

import argparse
import os
from dataclasses import replace

import run_day2_coding_eval as execution
import run_day3_coding_eval as day3

INTERRUPTED = "day3v2-br-10-k2.7code-r2-direct-build"
RESTART_SUFFIX = "operator-restart-v2"


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index for index, row in enumerate(rows) if row.run_slug == INTERRUPTED
    )
    replacement = replace(rows[index], run_suffix=RESTART_SUFFIX)
    return [replacement, *rows[index + 1 :]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only")
    args = parser.parse_args()
    rows = remaining_matrix()
    if args.only:
        rows = [row for row in rows if row.run_slug == args.only]
        if not rows:
            raise ValueError(f"unknown recovery run slug: {args.only}")
    if args.dry_run:
        for index, row in enumerate(rows, 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    for row in rows:
        execution.execute(row, plan_gate=day3.PLAN_GATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
