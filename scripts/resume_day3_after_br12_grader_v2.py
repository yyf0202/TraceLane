#!/usr/bin/env python3
"""Adjudicate BR-12 with V2 and resume after the evaluator interruption."""

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

COMPLETED = "day3v2-br-12-k2.7code-r1-direct-build"
INTERRUPTED = "day3v2-br-12-k2.7code-r1-plan-build"
RESTART_SUFFIX = "operator-restart-br12-grader-v2"
PLAN_GATE = day3.ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v4.py"
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


def _termination() -> dict[str, object]:
    path = (
        execution.RAW_ROOT
        / INTERRUPTED
        / "plan-cli.jsonl.termination.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def adjudicate(run_slug: str) -> None:
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


def preserve_interruption() -> dict[str, object]:
    raw = execution.RAW_ROOT / INTERRUPTED
    destination = raw / "operator-interruption.json"
    termination = _termination()
    usage = termination["usage_at_termination"]
    evidence = {
        "schema_version": "coding-eval-operator-interruption/v0.1",
        "run_id": INTERRUPTED,
        "classification": "operator_interrupted_for_confirmed_grader_bug",
        "terminal_model_stop_observed": False,
        "partial_plan_usage": usage,
        "partial_plan_wall_ms": termination["wall_ms"],
        "automatic_retry_count": 0,
        "replacement_attempt": _restart_spec().run_slug,
    }
    if not destination.exists():
        destination.write_text(
            canonical_json(evidence) + "\n", encoding="utf-8"
        )
    return evidence


def _grade_and_finish(
    spec: execution.AttemptSpec,
    worktree: Path,
    raw: Path,
    end: dict[str, object],
) -> None:
    execution._grade(spec, worktree, raw)
    _run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        cwd=worktree,
        output=raw / "adjudicated-grader-v2.log",
    )
    execution._write_end(raw, end)


def recover_plan_build(interruption: dict[str, object]) -> None:
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
    partial_usage = interruption["partial_plan_usage"]
    partial_wall_ms = int(interruption["partial_plan_wall_ms"])
    plan_wall = max(
        1,
        execution.PLAN_WALL - (partial_wall_ms + 999) // 1000,
    )
    plan_tools = max(
        1,
        execution.PLAN_TOOLS - int(partial_usage["tool_calls"]),
    )
    plan_tokens = max(
        1,
        execution.PLAN_TOKENS - int(partial_usage["model_tokens"]),
    )
    recovery = {
        "schema_version": "coding-eval-evaluator-recovery/v0.1",
        "source_run": INTERRUPTED,
        "plan_rerun": True,
        "partial_plan_usage": partial_usage,
        "partial_plan_wall_ms": partial_wall_ms,
        "remaining_plan_budget": {
            "max_wall_seconds": plan_wall,
            "max_tool_calls": plan_tools,
            "max_model_tokens": plan_tokens,
        },
        "automatic_retry_count": 0,
    }
    (raw / "evaluator-recovery.json").write_text(
        canonical_json(recovery) + "\n", encoding="utf-8"
    )
    plan_code = execution._run(
        execution._runner_command(
            spec,
            worktree,
            raw,
            cli_name="plan-cli.jsonl",
            agent="plan",
            prompt=execution._prompt(task, spec.workflow),
            wall=plan_wall,
            tools=plan_tools,
            tokens=plan_tokens,
        )
    )
    if plan_code != 0:
        _grade_and_finish(
            spec,
            worktree,
            raw,
            {
                "build_started": False,
                "plan_runner_exit_code": plan_code,
                "reason": "plan_phase_failed",
                "layer": "evaluator-recovery",
            },
        )
        return

    handoff = raw / "handoff"
    prepare_code = execution._run(
        [
            str(execution.PYTHON),
            str(execution.HANDOFF),
            "--task",
            str(spec.task.manifest),
            "--plan-cli",
            str(raw / "plan-cli.jsonl"),
            "--output-dir",
            str(handoff),
        ],
        stdout=raw / "handoff.log",
    )
    gate_code = (
        execution._run(
            [
                str(execution.PYTHON),
                str(PLAN_GATE),
                str(handoff / "plan.json"),
                spec.task.short_id,
            ],
            stdout=raw / "plan-gate.log",
        )
        if prepare_code == 0
        else 1
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if gate_code != 0 or dirty:
        _grade_and_finish(
            spec,
            worktree,
            raw,
            {
                "build_started": False,
                "plan_runner_exit_code": plan_code,
                "handoff_exit_code": prepare_code,
                "plan_gate_exit_code": gate_code,
                "plan_modified_workspace": bool(dirty),
                "reason": (
                    "plan_gate_failed"
                    if gate_code
                    else "plan_modified_workspace"
                ),
                "layer": "evaluator-recovery",
            },
        )
        return

    plan_execution = execution._execution(raw, "plan-cli.jsonl")
    plan_usage = plan_execution["usage"]
    combined_wall_ms = partial_wall_ms + int(plan_execution["wall_ms"])
    combined_tools = int(partial_usage["tool_calls"]) + int(
        plan_usage["tool_calls"]
    )
    combined_tokens = int(partial_usage["model_tokens"]) + int(
        plan_usage["model_tokens"]
    )
    build_wall = max(
        1,
        execution.TOTAL_WALL - (combined_wall_ms + 999) // 1000,
    )
    build_tools = max(1, execution.TOTAL_TOOLS - combined_tools)
    build_tokens = max(1, execution.TOTAL_TOKENS - combined_tokens)
    build_code = execution._run(
        execution._runner_command(
            spec,
            worktree,
            raw,
            cli_name="build-cli.jsonl",
            agent="build",
            prompt_file=handoff / "build-prompt.txt",
            wall=build_wall,
            tools=build_tools,
            tokens=build_tokens,
        )
    )
    _grade_and_finish(
        spec,
        worktree,
        raw,
        {
            "build_started": True,
            "plan_runner_exit_code": plan_code,
            "plan_gate_exit_code": gate_code,
            "build_runner_exit_code": build_code,
            "layer": "evaluator-recovery",
            "combined_plan_usage": {
                "wall_ms": combined_wall_ms,
                "tool_calls": combined_tools,
                "model_tokens": combined_tokens,
            },
            "remaining_build_budget": {
                "max_wall_seconds": build_wall,
                "max_tool_calls": build_tools,
                "max_model_tokens": build_tokens,
            },
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
        print(f"ADJUDICATE {COMPLETED}")
        print(f"PRESERVE {INTERRUPTED}")
        print(f"RECOVER {_restart_spec().run_slug}")
        for index, row in enumerate(remaining_matrix(), 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    adjudicate(COMPLETED)
    interruption = preserve_interruption()
    recover_plan_build(interruption)
    for row in remaining_matrix():
        execution.execute(row, plan_gate=PLAN_GATE)
        if row.task.short_id == "BR-12":
            adjudicate(row.run_slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
