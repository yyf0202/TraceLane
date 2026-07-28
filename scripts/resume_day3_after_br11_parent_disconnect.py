#!/usr/bin/env python3
"""Recover a nonterminal BR-11 build after its parent process disconnected."""

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
from run_opencode_coding_attempt import _consume

from tracelane.coding import load_coding_task
from tracelane.contracts import canonical_json

INTERRUPTED = "day3v2-br-11-glm52-r2-plan-build"
RESTART_SUFFIX = "external-restart-v1"
PLAN_GATE = day3.ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v4.py"
ADJUDICATOR = (
    day3.ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v6.py"
)
INTERRUPTED_WALL_SECONDS = 292


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


def _partial_usage(path: Path) -> dict[str, int]:
    metrics = {
        "tool_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "model_tokens": 0,
    }
    _consume(path, offset=0, pending=b"", metrics=metrics, final=True)
    return metrics


def restart_spec() -> execution.AttemptSpec:
    original = next(
        row for row in day3.matrix() if row.run_slug == INTERRUPTED
    )
    return replace(original, run_suffix=RESTART_SUFFIX)


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index for index, row in enumerate(rows) if row.run_slug == INTERRUPTED
    )
    return rows[index + 1 :]


def recover_build() -> None:
    spec = restart_spec()
    source = execution.RAW_ROOT / INTERRUPTED
    raw = execution.RAW_ROOT / spec.run_slug
    if raw.exists():
        required = (
            raw / "workflow-end.json",
            raw / "build-cli.jsonl.execution.json",
            raw / "adjudicated-grader-v6.log",
        )
        if all(path.exists() for path in required):
            return
        raise ValueError(f"partial recovery exists: {raw}")
    raw.mkdir(parents=True)
    task = load_coding_task(
        json.loads(spec.task.manifest.read_text(encoding="utf-8"))
    )
    worktree = execution._worktree(spec, task.baseline.commit_sha)
    handoff = raw / "handoff"
    handoff.mkdir()
    for name in ("plan.json", "build-prompt.txt"):
        shutil.copy2(source / "handoff" / name, handoff / name)

    plan_path = source / "plan-cli.jsonl.execution.json"
    partial_path = source / "build-cli.jsonl"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_usage = plan["usage"]
    partial = _partial_usage(partial_path)
    wall = max(
        1,
        execution.TOTAL_WALL
        - (int(plan["wall_ms"]) + 999) // 1000
        - INTERRUPTED_WALL_SECONDS,
    )
    tools = max(
        1,
        execution.TOTAL_TOOLS
        - int(plan_usage["tool_calls"])
        - partial["tool_calls"],
    )
    tokens = max(
        1,
        execution.TOTAL_TOKENS
        - int(plan_usage["model_tokens"])
        - partial["model_tokens"],
    )
    evidence = {
        "schema_version": "coding-eval-disconnect-recovery/v0.1",
        "layer": "external-recovery",
        "source_run": INTERRUPTED,
        "source_plan_sha256": _sha256(source / "handoff/plan.json"),
        "source_plan_execution_sha256": _sha256(plan_path),
        "source_partial_build_sha256": _sha256(partial_path),
        "source_partial_build_usage": partial,
        "source_partial_build_wall_seconds": INTERRUPTED_WALL_SECONDS,
        "terminal_model_stop_observed": False,
        "plan_rerun": False,
        "automatic_retry_count": 0,
        "remaining_build_budget": {
            "max_wall_seconds": wall,
            "max_tool_calls": tools,
            "max_model_tokens": tokens,
        },
    }
    (raw / "disconnect-recovery.json").write_text(
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
            wall=wall,
            tools=tools,
            tokens=tokens,
        )
    )
    execution._grade(spec, worktree, raw)
    _run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        cwd=worktree,
        output=raw / "adjudicated-grader-v6.log",
    )
    execution._write_end(
        raw,
        {
            "build_started": True,
            "build_runner_exit_code": build_code,
            "layer": "external-recovery",
            "source_run": INTERRUPTED,
            "plan_runner_exit_code": 0,
            "plan_gate_exit_code": 0,
            "remaining_build_budget": evidence["remaining_build_budget"],
        },
    )


def preserve_interruption() -> None:
    raw = execution.RAW_ROOT / INTERRUPTED
    destination = raw / "parent-disconnect.json"
    if destination.exists():
        return
    destination.write_text(
        canonical_json(
            {
                "schema_version": "coding-eval-parent-disconnect/v0.1",
                "run_id": INTERRUPTED,
                "classification": "external_parent_disconnect_during_nonterminal_build",
                "terminal_model_stop_observed": False,
                "automatic_retry_count": 0,
                "replacement_attempt": restart_spec().run_slug,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        print(f"RECOVER {restart_spec().run_slug}")
        for index, row in enumerate(remaining_matrix(), 1):
            print(f"{index:02d} {row.run_slug}")
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    preserve_interruption()
    recover_build()
    for row in remaining_matrix():
        execution.execute(row, plan_gate=PLAN_GATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
