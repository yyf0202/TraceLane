#!/usr/bin/env python3
"""Resume Day 3 after decoupling BR-11 v4 functional slices."""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import replace

import run_day2_coding_eval as execution
import run_day3_coding_eval as day3

INTERRUPTED = "day3v2-br-11-dsv4pro-r2-plan-build"
RESTART_SUFFIX = "operator-restart-br11-v4"
ADJUDICATOR = (
    day3.ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v4.py"
)


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index for index, row in enumerate(rows) if row.run_slug == INTERRUPTED
    )
    replacement = replace(rows[index], run_suffix=RESTART_SUFFIX)
    return [replacement, *rows[index + 1 :]]


def adjudicate_br11(spec: execution.AttemptSpec) -> None:
    if spec.task.short_id != "BR-11":
        return
    raw = execution.RAW_ROOT / spec.run_slug
    destination = raw / "adjudicated-grader-v4.log"
    if destination.exists():
        raise ValueError(f"BR-11 v4 output already exists: {destination}")
    worktree = execution.WORK_ROOT / f"bericher-{spec.run_slug}"
    with destination.open("wb") as output:
        subprocess.run(
            [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
            cwd=worktree,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only")
    args = parser.parse_args()
    rows = remaining_matrix()
    if args.only:
        rows = [row for row in rows if row.run_slug == args.only]
        if not rows:
            raise ValueError(f"unknown remaining run slug: {args.only}")
    if args.dry_run:
        for index, row in enumerate(rows, 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    for row in rows:
        execution.execute(row, plan_gate=day3.PLAN_GATE)
        adjudicate_br11(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
