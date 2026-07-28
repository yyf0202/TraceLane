#!/usr/bin/env python3
"""Replay the frozen GLM BR-11 plan under gate V4, then resume Day 3."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import run_day2_coding_eval as execution
import run_day3_coding_eval as day3

from tracelane.coding import load_coding_task
from tracelane.contracts import canonical_json

SOURCE_PLAN_RUN = "day3v2-br-11-glm52-r1-plan-build"
BASE_NEXT = "day3v2-br-11-glm52-r1-direct-build"
INTERRUPTED_NEXT = BASE_NEXT
REPLAY_SUFFIX = "gate-replay-br11-plan-v4"
NEXT_SUFFIX = "operator-restart-br11-plan-gate-v4"
PLAN_GATE = day3.ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v4.py"
ADJUDICATOR = (
    day3.ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v5.py"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_log(command: list[str], *, cwd: Path, output: Path) -> int:
    with output.open("wb") as handle:
        return subprocess.run(
            command,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode


def replay_spec() -> execution.AttemptSpec:
    original = next(
        row
        for row in day3.matrix()
        if row.run_slug == "day3v2-br-11-glm52-r1-plan-build"
    )
    return replace(original, run_suffix=REPLAY_SUFFIX)


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index for index, row in enumerate(rows) if row.run_slug == BASE_NEXT
    )
    replacement = replace(rows[index], run_suffix=NEXT_SUFFIX)
    return [replacement, *rows[index + 1 :]]


def preserve_interruption() -> None:
    raw = execution.RAW_ROOT / INTERRUPTED_NEXT
    destination = raw / "operator-interruption.json"
    if destination.exists():
        return
    cli = raw / "cli.jsonl"
    if not cli.is_file():
        raise ValueError(f"interrupted evidence missing: {cli}")
    destination.write_text(
        canonical_json(
            {
                "schema_version": "coding-eval-operator-interruption/v0.1",
                "run_id": INTERRUPTED_NEXT,
                "classification": "operator_interrupted_for_confirmed_plan_gate_bug",
                "raw_cli_bytes": cli.stat().st_size,
                "automatic_retry_count": 0,
                "replacement_attempt": BASE_NEXT + "-" + NEXT_SUFFIX,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def replay_frozen_plan() -> None:
    spec = replay_spec()
    source = execution.RAW_ROOT / SOURCE_PLAN_RUN
    raw = execution.RAW_ROOT / spec.run_slug
    if raw.exists():
        required = (
            raw / "workflow-end.json",
            raw / "build-cli.jsonl.execution.json",
            raw / "adjudicated-grader-v5.log",
        )
        if all(path.exists() for path in required):
            return
        raise ValueError(f"partial gate replay exists: {raw}")
    raw.mkdir(parents=True)
    task = load_coding_task(
        json.loads(spec.task.manifest.read_text(encoding="utf-8"))
    )
    worktree = execution._worktree(spec, task.baseline.commit_sha)
    handoff = raw / "handoff"
    handoff.mkdir()
    for name in ("plan.json", "build-prompt.txt"):
        shutil.copy2(source / "handoff" / name, handoff / name)
    gate_code = _run_log(
        [
            str(execution.PYTHON),
            str(PLAN_GATE),
            str(handoff / "plan.json"),
            "BR-11",
        ],
        cwd=worktree,
        output=raw / "plan-gate-v4.log",
    )
    if gate_code:
        raise ValueError("frozen plan failed gate v4")
    if subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout:
        raise ValueError("gate-replay worktree dirty before build")
    plan_path = source / "plan-cli.jsonl.execution.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    usage = plan["usage"]
    wall = max(
        1, execution.TOTAL_WALL - (int(plan["wall_ms"]) + 999) // 1000
    )
    tools = max(1, execution.TOTAL_TOOLS - int(usage["tool_calls"]))
    tokens = max(1, execution.TOTAL_TOKENS - int(usage["model_tokens"]))
    (raw / "gate-replay.json").write_text(
        canonical_json(
            {
                "schema_version": "coding-eval-gate-replay/v0.1",
                "layer": "gate-replay",
                "source_plan_run": SOURCE_PLAN_RUN,
                "source_plan_sha256": _sha256(source / "handoff/plan.json"),
                "source_plan_execution_sha256": _sha256(plan_path),
                "plan_gate": str(PLAN_GATE.relative_to(day3.ROOT)),
                "plan_gate_sha256": _sha256(PLAN_GATE),
                "plan_rerun": False,
                "build_rerun": False,
                "automatic_retry_count": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    build_code = execution._run(
        execution._runner_command(
            spec,
            worktree,
            raw,
            cli_name="build-cli.jsonl",
            agent="build",
            prompt_file=handoff / "build-prompt.txt",
            wall=wall,
            tools=tools,
            tokens=tokens,
        )
    )
    execution._grade(spec, worktree, raw)
    _run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        cwd=worktree,
        output=raw / "adjudicated-grader-v5.log",
    )
    execution._write_end(
        raw,
        {
            "build_started": True,
            "build_runner_exit_code": build_code,
            "layer": "gate-replay",
            "source_plan_run": SOURCE_PLAN_RUN,
            "plan_gate_exit_code": gate_code,
            "remaining_build_budget": {
                "max_wall_seconds": wall,
                "max_tool_calls": tools,
                "max_model_tokens": tokens,
            },
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    rows = remaining_matrix()
    if args.dry_run:
        print(f"REPLAY {replay_spec().run_slug}")
        for index, row in enumerate(rows, 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    preserve_interruption()
    replay_frozen_plan()
    for row in rows:
        execution.execute(row, plan_gate=PLAN_GATE)
        if row.task.short_id == "BR-11":
            raw = execution.RAW_ROOT / row.run_slug
            worktree = execution.WORK_ROOT / f"bericher-{row.run_slug}"
            _run_log(
                [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
                cwd=worktree,
                output=raw / "adjudicated-grader-v5.log",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
