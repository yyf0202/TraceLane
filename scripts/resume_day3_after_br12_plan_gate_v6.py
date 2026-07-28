#!/usr/bin/env python3
"""Replay the frozen GLM BR-12 plan through V6 and resume Day 3."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import resume_day3_after_br12_plan_gate_v5 as direct
import run_day2_coding_eval as execution
import run_day3_coding_eval as day3

from tracelane.coding import load_coding_task
from tracelane.contracts import canonical_json

PLAN_RUN = (
    "day3v2-br-12-glm52-r1-plan-build-operator-restart-br12-grader-v3"
)
INTERRUPTED = "day3v2-br-12-glm52-r1-direct-build"
REPLAY_SUFFIX = "gate-replay-br12-plan-v6"
RESTART_SUFFIX = "operator-restart-br12-plan-gate-v6"
PLAN_GATE = day3.ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v6.py"
ADJUDICATOR = (
    day3.ROOT / "tests/fixtures/coding_tasks/br12_hidden_acceptance_v3.py"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan_spec() -> execution.AttemptSpec:
    return next(row for row in day3.matrix() if row.run_slug.startswith(
        "day3v2-br-12-glm52-r1-plan-build"
    ))


def _replay_spec() -> execution.AttemptSpec:
    return replace(_plan_spec(), run_suffix=REPLAY_SUFFIX)


def replay_frozen_plan() -> None:
    spec = _replay_spec()
    source = execution.RAW_ROOT / PLAN_RUN
    raw = execution.RAW_ROOT / spec.run_slug
    if raw.exists():
        required = (
            raw / "workflow-end.json",
            raw / "adjudicated-grader-v3.log",
            raw / "gate-replay.json",
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
    gate_code = execution._run(
        [
            str(execution.PYTHON),
            str(PLAN_GATE),
            str(handoff / "plan.json"),
            "BR-12",
        ],
        stdout=raw / "plan-gate.log",
    )
    if gate_code != 0:
        raise ValueError("V6 did not pass the frozen plan")

    partial = json.loads(
        (source / "evaluator-recovery.json").read_text(encoding="utf-8")
    )
    plan_execution = execution._execution(source, "plan-cli.jsonl")
    usage = plan_execution["usage"]
    partial_usage = partial["partial_plan_usage"]
    wall_ms = int(partial["partial_plan_wall_ms"]) + int(
        plan_execution["wall_ms"]
    )
    tools = int(partial_usage["tool_calls"]) + int(usage["tool_calls"])
    tokens = int(partial_usage["model_tokens"]) + int(usage["model_tokens"])
    build_wall = max(
        1, execution.TOTAL_WALL - (wall_ms + 999) // 1000
    )
    build_tools = max(1, execution.TOTAL_TOOLS - tools)
    build_tokens = max(1, execution.TOTAL_TOKENS - tokens)
    evidence = {
        "schema_version": "coding-eval-gate-replay/v0.1",
        "source_run": PLAN_RUN,
        "source_plan_sha256": _sha256(source / "handoff/plan.json"),
        "source_build_prompt_sha256": _sha256(
            source / "handoff/build-prompt.txt"
        ),
        "plan_rerun": False,
        "automatic_retry_count": 0,
        "combined_plan_usage": {
            "wall_ms": wall_ms,
            "tool_calls": tools,
            "model_tokens": tokens,
        },
        "remaining_build_budget": {
            "max_wall_seconds": build_wall,
            "max_tool_calls": build_tools,
            "max_model_tokens": build_tokens,
        },
    }
    (raw / "gate-replay.json").write_text(
        canonical_json(evidence) + "\n", encoding="utf-8"
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
    direct._run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        cwd=worktree,
        output=raw / "adjudicated-grader-v3.log",
    )
    execution._write_end(
        raw,
        {
            "build_started": True,
            "build_runner_exit_code": build_code,
            "plan_gate_exit_code": gate_code,
            "layer": "gate-replay",
            "source_run": PLAN_RUN,
            "remaining_build_budget": evidence["remaining_build_budget"],
        },
    )


def _configure_direct_recovery() -> None:
    direct.INTERRUPTED = INTERRUPTED
    direct.RESTART_SUFFIX = RESTART_SUFFIX
    direct.PLAN_GATE = PLAN_GATE
    direct.ADJUDICATOR = ADJUDICATOR
    direct.ADJUDICATOR_LABEL = "v3"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    _configure_direct_recovery()
    if args.dry_run:
        print(f"REPLAY {_replay_spec().run_slug}")
        print(f"PRESERVE {INTERRUPTED}")
        print(f"RECOVER {direct._restart_spec().run_slug}")
        for index, row in enumerate(direct.remaining_matrix(), 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    replay_frozen_plan()
    interruption = direct.preserve_interruption()
    direct.recover_direct_build(interruption)
    for row in direct.remaining_matrix():
        execution.execute(row, plan_gate=PLAN_GATE)
        if row.task.short_id == "BR-12":
            direct.adjudicate_build(row.run_slug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
