#!/usr/bin/env python3
"""Replay one frozen BR-11 plan, then resume Day 3 with plan gate v3."""

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

SOURCE_PLAN_RUN = (
    "day3v2-br-11-dsv4pro-r2-plan-build-operator-restart-br11-v4"
)
REPLAY_SUFFIX = "gate-replay-br11-plan-v3"
INTERRUPTED_NEXT = "day3v2-br-11-k2.7code-r1-direct-build"
NEXT_RESTART_SUFFIX = "operator-restart-br11-plan-gate-v3"
PLAN_GATE_V3 = (
    day3.ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v3.py"
)
BR11_V4 = (
    day3.ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v4.py"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replay_spec() -> execution.AttemptSpec:
    original = next(
        row
        for row in day3.matrix()
        if row.run_slug == "day3v2-br-11-dsv4pro-r2-plan-build"
    )
    return replace(original, run_suffix=REPLAY_SUFFIX)


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index
        for index, row in enumerate(rows)
        if row.run_slug == INTERRUPTED_NEXT
    )
    replacement = replace(rows[index], run_suffix=NEXT_RESTART_SUFFIX)
    return [replacement, *rows[index + 1 :]]


def _run_log(command: list[str], *, cwd: Path, output: Path) -> int:
    with output.open("wb") as handle:
        return subprocess.run(
            command,
            cwd=cwd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode


def replay_frozen_plan() -> None:
    spec = replay_spec()
    source = execution.RAW_ROOT / SOURCE_PLAN_RUN
    raw = execution.RAW_ROOT / spec.run_slug
    if raw.exists():
        required = (
            raw / "workflow-end.json",
            raw / "build-cli.jsonl.execution.json",
            raw / "adjudicated-grader-v4.log",
        )
        if all(path.exists() for path in required):
            return
        raise ValueError(f"partial gate replay already exists: {raw}")
    raw.mkdir(parents=True)

    task_value = json.loads(spec.task.manifest.read_text(encoding="utf-8"))
    task = load_coding_task(task_value)
    worktree = execution._worktree(spec, task.baseline.commit_sha)
    source_handoff = source / "handoff"
    handoff = raw / "handoff"
    handoff.mkdir()
    for name in ("plan.json", "build-prompt.txt"):
        shutil.copy2(source_handoff / name, handoff / name)

    gate_code = _run_log(
        [
            str(execution.PYTHON),
            str(PLAN_GATE_V3),
            str(handoff / "plan.json"),
            "BR-11",
        ],
        cwd=worktree,
        output=raw / "plan-gate-v3.log",
    )
    if gate_code != 0:
        raise ValueError("frozen BR-11 plan did not pass plan gate v3")
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if dirty:
        raise ValueError("gate-replay worktree is dirty before build")

    plan_execution_path = source / "plan-cli.jsonl.execution.json"
    plan_execution = json.loads(
        plan_execution_path.read_text(encoding="utf-8")
    )
    usage = plan_execution["usage"]
    build_wall = max(
        1,
        execution.TOTAL_WALL
        - (int(plan_execution["wall_ms"]) + 999) // 1000,
    )
    build_tools = max(
        1, execution.TOTAL_TOOLS - int(usage["tool_calls"])
    )
    build_tokens = max(
        1, execution.TOTAL_TOKENS - int(usage["model_tokens"])
    )
    provenance = {
        "schema_version": "coding-eval-gate-replay/v0.1",
        "layer": "gate-replay",
        "source_plan_run": SOURCE_PLAN_RUN,
        "source_plan_sha256": _sha256(source_handoff / "plan.json"),
        "source_plan_execution_sha256": _sha256(plan_execution_path),
        "plan_gate": str(PLAN_GATE_V3.relative_to(day3.ROOT)),
        "plan_gate_sha256": _sha256(PLAN_GATE_V3),
        "plan_rerun": False,
        "build_rerun": False,
        "automatic_retry_count": 0,
    }
    (raw / "gate-replay.json").write_text(
        canonical_json(provenance) + "\n", encoding="utf-8"
    )

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
    execution._grade(spec, worktree, raw)
    _run_log(
        [str(execution.GRADER_PYTHON), str(BR11_V4), "."],
        cwd=worktree,
        output=raw / "adjudicated-grader-v4.log",
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
                "max_wall_seconds": build_wall,
                "max_tool_calls": build_tools,
                "max_model_tokens": build_tokens,
            },
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--only")
    args = parser.parse_args()
    rows = remaining_matrix()
    if args.only:
        rows = [row for row in rows if row.run_slug == args.only]
        if not rows:
            raise ValueError(f"unknown remaining run slug: {args.only}")
    if args.dry_run:
        print(f"REPLAY {replay_spec().run_slug}")
        for index, row in enumerate(rows, 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    replay_frozen_plan()
    if args.replay_only:
        return 0
    for row in rows:
        execution.execute(row, plan_gate=PLAN_GATE_V3)
        if row.task.short_id == "BR-11":
            raw = execution.RAW_ROOT / row.run_slug
            worktree = execution.WORK_ROOT / f"bericher-{row.run_slug}"
            _run_log(
                [str(execution.GRADER_PYTHON), str(BR11_V4), "."],
                cwd=worktree,
                output=raw / "adjudicated-grader-v4.log",
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
