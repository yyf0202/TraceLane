#!/usr/bin/env python3
"""Adjudicate BR-12 R2 plan with V5 and resume the interrupted matrix."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import run_day2_coding_eval as execution
import run_day3_coding_eval as day3

from tracelane.coding import load_coding_task
from tracelane.contracts import canonical_json

PLAN_RUN = "day3v2-br-12-k2.7code-r2-plan-build"
INTERRUPTED = "day3v2-br-12-k2.7code-r2-direct-build"
RESTART_SUFFIX = "operator-restart-br12-plan-gate-v5"
PLAN_GATE = day3.ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v5.py"
ADJUDICATOR = (
    day3.ROOT / "tests/fixtures/coding_tasks/br12_hidden_acceptance_v2.py"
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


def _base_spec() -> execution.AttemptSpec:
    return next(row for row in day3.matrix() if row.run_slug == INTERRUPTED)


def _restart_spec() -> execution.AttemptSpec:
    return replace(_base_spec(), run_suffix=RESTART_SUFFIX)


def adjudicate_plan() -> None:
    raw = execution.RAW_ROOT / PLAN_RUN
    destination = raw / "plan-gate-v5.log"
    if destination.exists():
        return
    _run_log(
        [
            str(execution.PYTHON),
            str(PLAN_GATE),
            str(raw / "handoff/plan.json"),
            "BR-12",
        ],
        cwd=day3.ROOT,
        output=destination,
    )


def adjudicate_build(run_slug: str) -> None:
    raw = execution.RAW_ROOT / run_slug
    destination = raw / "adjudicated-grader-v2.log"
    if destination.exists():
        return
    worktree = execution.WORK_ROOT / f"bericher-{run_slug}"
    if not (raw / "workflow-end.json").is_file() or not worktree.is_dir():
        raise ValueError(f"completed evidence missing: {run_slug}")
    _run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        cwd=worktree,
        output=destination,
    )


def _termination() -> dict[str, object]:
    path = execution.RAW_ROOT / INTERRUPTED / "cli.jsonl.termination.json"
    return json.loads(path.read_text(encoding="utf-8"))


def preserve_interruption() -> dict[str, object]:
    raw = execution.RAW_ROOT / INTERRUPTED
    destination = raw / "operator-interruption.json"
    termination = _termination()
    evidence = {
        "schema_version": "coding-eval-operator-interruption/v0.1",
        "run_id": INTERRUPTED,
        "classification": "operator_interrupted_for_confirmed_plan_gate_bug",
        "terminal_model_stop_observed": False,
        "partial_build_usage": termination["usage_at_termination"],
        "partial_build_wall_ms": termination["wall_ms"],
        "automatic_retry_count": 0,
        "replacement_attempt": _restart_spec().run_slug,
    }
    if not destination.exists():
        destination.write_text(
            canonical_json(evidence) + "\n", encoding="utf-8"
        )
    return evidence


def recover_direct_build(interruption: dict[str, object]) -> None:
    spec = _restart_spec()
    raw = execution.RAW_ROOT / spec.run_slug
    if raw.exists():
        required = (
            raw / "workflow-end.json",
            raw / "adjudicated-grader-v2.log",
        )
        if all(path.exists() for path in required):
            return
        raise ValueError(f"partial recovery exists: {raw}")
    raw.mkdir(parents=True)
    task = load_coding_task(
        json.loads(spec.task.manifest.read_text(encoding="utf-8"))
    )
    worktree = execution._worktree(spec, task.baseline.commit_sha)
    partial_usage = interruption["partial_build_usage"]
    partial_wall_ms = int(interruption["partial_build_wall_ms"])
    wall = max(
        1,
        execution.TOTAL_WALL - (partial_wall_ms + 999) // 1000,
    )
    tools = max(
        1,
        execution.TOTAL_TOOLS - int(partial_usage["tool_calls"]),
    )
    tokens = max(
        1,
        execution.TOTAL_TOKENS - int(partial_usage["model_tokens"]),
    )
    recovery = {
        "schema_version": "coding-eval-evaluator-recovery/v0.1",
        "source_run": INTERRUPTED,
        "build_rerun": True,
        "partial_build_usage": partial_usage,
        "partial_build_wall_ms": partial_wall_ms,
        "remaining_build_budget": {
            "max_wall_seconds": wall,
            "max_tool_calls": tools,
            "max_model_tokens": tokens,
        },
        "automatic_retry_count": 0,
    }
    (raw / "evaluator-recovery.json").write_text(
        canonical_json(recovery) + "\n", encoding="utf-8"
    )
    build_code = execution._run(
        execution._runner_command(
            spec,
            worktree,
            raw,
            cli_name="cli.jsonl",
            agent="build",
            prompt=execution._prompt(task, spec.workflow),
            wall=wall,
            tools=tools,
            tokens=tokens,
        )
    )
    execution._grade(spec, worktree, raw)
    _run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        cwd=worktree,
        output=raw / "adjudicated-grader-v2.log",
    )
    execution._write_end(
        raw,
        {
            "build_started": True,
            "runner_exit_code": build_code,
            "layer": "evaluator-recovery",
            "source_run": INTERRUPTED,
            "remaining_build_budget": recovery["remaining_build_budget"],
        },
    )


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index for index, row in enumerate(rows) if row.run_slug == INTERRUPTED
    )
    return rows[index + 1 :]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(f"ADJUDICATE {PLAN_RUN}")
        print(f"PRESERVE {INTERRUPTED}")
        print(f"RECOVER {_restart_spec().run_slug}")
        for index, row in enumerate(remaining_matrix(), 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    adjudicate_plan()
    interruption = preserve_interruption()
    recover_direct_build(interruption)
    for row in remaining_matrix():
        execution.execute(row, plan_gate=PLAN_GATE)
        if row.task.short_id == "BR-12":
            adjudicate_build(row.run_slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
