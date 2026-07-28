#!/usr/bin/env python3
"""Run the preregistered Day 3 coding-eval matrix strictly serially."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import run_day2_coding_eval as experiment

ROOT = Path(__file__).resolve().parents[1]
PLAN_GATE = ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance.py"
HARNESS = ROOT / "fixtures/coding/harnesses/opencode-h0.json"

TASKS = (
    experiment.TaskSpec(
        "BR-10",
        ROOT
        / "fixtures/coding/bericher-v0.9/tasks/"
        "BR-10-cross-platform-sync-push-safety.json",
        ROOT / "tests/fixtures/coding_tasks/br10_hidden_acceptance.py",
    ),
    experiment.TaskSpec(
        "BR-11",
        ROOT
        / "fixtures/coding/bericher-v0.9/tasks/"
        "BR-11-scheduled-real-trading-orchestration.json",
        ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance.py",
    ),
    experiment.TaskSpec(
        "BR-12",
        ROOT
        / "fixtures/coding/bericher-v0.9/tasks/"
        "BR-12-factorvae-kfold-warmup-orchestration.json",
        ROOT / "tests/fixtures/coding_tasks/br12_hidden_acceptance.py",
    ),
)
MODELS = experiment.MODELS


def matrix() -> list[experiment.AttemptSpec]:
    rows: list[experiment.AttemptSpec] = []
    for task_index, task in enumerate(TASKS):
        rotated = MODELS[task_index:] + MODELS[:task_index]
        for model_index, model in enumerate(rotated):
            for repeat in (1, 2):
                first = (
                    "direct-build"
                    if (task_index + model_index + repeat) % 2
                    else "plan-build"
                )
                second = "plan-build" if first == "direct-build" else "direct-build"
                rows.extend(
                    (
                        experiment.AttemptSpec(
                            task,
                            model,
                            repeat,
                            first,
                            experiment_day="day3",
                            harness_manifest=HARNESS,
                        ),
                        experiment.AttemptSpec(
                            task,
                            model,
                            repeat,
                            second,
                            experiment_day="day3",
                            harness_manifest=HARNESS,
                        ),
                    )
                )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only")
    args = parser.parse_args()
    rows = matrix()
    if args.only:
        rows = [row for row in rows if row.run_slug == args.only]
        if not rows:
            raise ValueError(f"unknown run slug: {args.only}")
    if args.dry_run:
        for index, row in enumerate(rows, 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    for row in rows:
        experiment.execute(row, plan_gate=PLAN_GATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
