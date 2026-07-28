#!/usr/bin/env python3
"""Adjudicate the completed gate replay with V5 and resume Day 3."""

from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import run_day2_coding_eval as execution
import run_day3_coding_eval as day3

from tracelane.contracts import canonical_json

REPLAY = "day3v2-br-11-dsv4pro-r2-plan-build-gate-replay-br11-plan-v3"
INTERRUPTED = (
    "day3v2-br-11-k2.7code-r1-direct-build-"
    "operator-restart-br11-plan-gate-v3"
)
BASE_ROW = "day3v2-br-11-k2.7code-r1-direct-build"
RESTART_SUFFIX = "operator-restart-br11-grader-v5"
PLAN_GATE = day3.ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v3.py"
ADJUDICATOR = (
    day3.ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v5.py"
)


def _run_log(command: list[str], *, cwd: Path, output: Path) -> int:
    with output.open("wb") as handle:
        return subprocess.run(
            command,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode


def adjudicate(run_slug: str) -> None:
    raw = execution.RAW_ROOT / run_slug
    destination = raw / "adjudicated-grader-v5.log"
    if destination.exists():
        return
    worktree = execution.WORK_ROOT / f"bericher-{run_slug}"
    if not (raw / "workflow-end.json").is_file() or not worktree.is_dir():
        raise ValueError(f"completed BR-11 evidence is missing: {run_slug}")
    _run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        cwd=worktree,
        output=destination,
    )


def preserve_interruption() -> None:
    raw = execution.RAW_ROOT / INTERRUPTED
    destination = raw / "operator-interruption.json"
    if destination.exists():
        return
    cli = raw / "cli.jsonl"
    if not cli.is_file():
        raise ValueError(f"interrupted CLI evidence is missing: {cli}")
    destination.write_text(
        canonical_json(
            {
                "schema_version": "coding-eval-operator-interruption/v0.1",
                "run_id": INTERRUPTED,
                "classification": "operator_interrupted_for_confirmed_grader_bug",
                "reason": "BR-11 v4 did not contain candidate SystemExit at the slice boundary",
                "raw_cli_bytes": cli.stat().st_size,
                "automatic_retry_count": 0,
                "replacement_attempt": (
                    BASE_ROW + "-" + RESTART_SUFFIX
                ),
            }
        )
        + "\n",
        encoding="utf-8",
    )


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index for index, row in enumerate(rows) if row.run_slug == BASE_ROW
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
            raise ValueError(f"unknown remaining run slug: {args.only}")
    if args.dry_run:
        print(f"ADJUDICATE {REPLAY}")
        print(f"PRESERVE {INTERRUPTED}")
        for index, row in enumerate(rows, 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    adjudicate(REPLAY)
    preserve_interruption()
    for row in rows:
        execution.execute(row, plan_gate=PLAN_GATE)
        if row.task.short_id == "BR-11":
            adjudicate(row.run_slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
