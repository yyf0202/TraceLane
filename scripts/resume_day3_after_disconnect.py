#!/usr/bin/env python3
"""Recover one terminal Day 3 build and resume after its original matrix row."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import run_day2_coding_eval as execution
import run_day3_coding_eval as day3
from run_opencode_coding_attempt import _consume

from tracelane.contracts import canonical_json

INTERRUPTED = "day3v2-br-11-dsv4pro-r1-plan-build"
COMPLETED_RESTART = INTERRUPTED + "-operator-restart-v3"
RAW = day3.ROOT / "artifacts/raw-opencode" / COMPLETED_RESTART
WORKTREE = Path(
    "/Users/efunyang/Documents/Codex/2026-07-26/"
    "realtime-voice-chat-3/work/bericher-" + COMPLETED_RESTART
)
ADJUDICATOR = (
    day3.ROOT / "tests/fixtures/coding_tasks/br11_hidden_acceptance_v2.py"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl_bounds(path: Path) -> tuple[int, int, bool]:
    timestamps: list[int] = []
    terminal_stop = False
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            timestamp = row.get("timestamp")
            if isinstance(timestamp, int):
                timestamps.append(timestamp)
            part = row.get("part")
            terminal_stop = terminal_stop or (
                isinstance(part, dict)
                and part.get("type") == "step-finish"
                and part.get("reason") == "stop"
            )
    if not timestamps:
        raise ValueError("build CLI has no timestamped events")
    return min(timestamps), max(timestamps), terminal_stop


def _run_log(command: list[str], destination: Path) -> int:
    with destination.open("wb") as output:
        return subprocess.run(
            command,
            cwd=WORKTREE,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        ).returncode


def recover_completed_build() -> None:
    cli = RAW / "build-cli.jsonl"
    execution_path = RAW / "build-cli.jsonl.execution.json"
    workflow_path = RAW / "workflow-end.json"
    evidence_path = RAW / "disconnect-recovery.json"
    outputs = (
        execution_path,
        workflow_path,
        evidence_path,
        RAW / "independent-grader.log",
        RAW / "adjudicated-grader-v2.log",
        RAW / "diff-check.log",
    )
    if all(path.exists() for path in outputs):
        return
    existing = [str(path) for path in outputs if path.exists()]
    if existing:
        raise ValueError(f"partial recovery outputs already exist: {existing}")
    if not cli.is_file() or not WORKTREE.is_dir():
        raise ValueError("recovery source CLI or worktree is missing")

    metrics = {
        "tool_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "model_tokens": 0,
    }
    _consume(cli, offset=0, pending=b"", metrics=metrics, final=True)
    first_event_ms, last_event_ms, terminal_stop = _jsonl_bounds(cli)
    if not terminal_stop:
        raise ValueError("build CLI has no terminal model stop event")
    stat = cli.stat()
    observed_wall_ms = round((stat.st_mtime - stat.st_birthtime) * 1000)

    plan = json.loads(
        (RAW / "plan-cli.jsonl.execution.json").read_text(encoding="utf-8")
    )
    plan_usage = plan["usage"]
    budgets = {
        "max_wall_seconds": max(
            1, execution.TOTAL_WALL - (int(plan["wall_ms"]) + 999) // 1000
        ),
        "max_tool_calls": max(
            1, execution.TOTAL_TOOLS - int(plan_usage["tool_calls"])
        ),
        "max_model_tokens": max(
            1, execution.TOTAL_TOKENS - int(plan_usage["model_tokens"])
        ),
        "provider_turn_timeout_seconds": execution.WATCHDOG,
        "provider_turn_retries": 0,
    }
    observations = {}
    for name, limit, observed in (
        ("wall_ms", budgets["max_wall_seconds"] * 1000, observed_wall_ms),
        ("tool_calls", budgets["max_tool_calls"], metrics["tool_calls"]),
        ("model_tokens", budgets["max_model_tokens"], metrics["model_tokens"]),
    ):
        overshoot = max(0, observed - limit)
        observations[name] = {
            "limit": limit,
            "observed": observed,
            "overshoot": overshoot,
            "overshoot_ratio": round(overshoot / limit, 8),
        }
    recovered_execution = {
        "schema_version": "opencode-budget-execution/v0.1",
        "provider": "ark",
        "model": "deepseek-v4-pro",
        "reason": "completed",
        "exit_code": None,
        "wall_ms": observed_wall_ms,
        "budgets": budgets,
        "usage": metrics,
        "budget_observation": observations,
        "harness": plan["harness"],
        "recovery": {
            "kind": "parent_disconnect_after_terminal_model_stop",
            "os_exit_code_observed": False,
            "terminal_stop_observed": True,
            "first_cli_event_ms": first_event_ms,
            "last_cli_event_ms": last_event_ms,
            "wall_source": "cli_file_birth_to_terminal_event_mtime",
        },
    }
    execution_path.write_text(
        canonical_json(recovered_execution) + "\n", encoding="utf-8"
    )

    frozen_code = _run_log(
        [
            str(execution.GRADER_PYTHON),
            str(day3.TASKS[1].grader),
            ".",
        ],
        RAW / "independent-grader.log",
    )
    adjudicated_code = _run_log(
        [str(execution.GRADER_PYTHON), str(ADJUDICATOR), "."],
        RAW / "adjudicated-grader-v2.log",
    )
    diff_code = _run_log(["git", "diff", "--check"], RAW / "diff-check.log")
    evidence = {
        "schema_version": "coding-eval-disconnect-recovery/v0.1",
        "run_id": COMPLETED_RESTART,
        "classification": "external_parent_disconnect_after_model_completion",
        "raw_cli_sha256": _sha256(cli),
        "terminal_stop_observed": True,
        "process_exit_code": "unobserved",
        "frozen_grader_exit_code": frozen_code,
        "adjudicated_grader_exit_code": adjudicated_code,
        "diff_check_exit_code": diff_code,
        "automatic_retry_count": 0,
        "build_rerun": False,
    }
    evidence_path.write_text(canonical_json(evidence) + "\n", encoding="utf-8")
    workflow = {
        "build_started": True,
        "plan_runner_exit_code": 0,
        "plan_gate_exit_code": 0,
        "build_runner_exit_code": None,
        "remaining_build_budget": {
            key: budgets[key]
            for key in (
                "max_wall_seconds",
                "max_tool_calls",
                "max_model_tokens",
            )
        },
        "recovery": {
            "classification": evidence["classification"],
            "terminal_stop_observed": True,
            "build_rerun": False,
        },
    }
    workflow_path.write_text(canonical_json(workflow) + "\n", encoding="utf-8")


def remaining_matrix() -> list[execution.AttemptSpec]:
    rows = day3.matrix()
    index = next(
        index for index, row in enumerate(rows) if row.run_slug == INTERRUPTED
    )
    return rows[index + 1 :]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recover-only", action="store_true")
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
    recover_completed_build()
    if args.recover_only:
        return 0
    os.environ["TRACELANE_ROOT"] = str(day3.ROOT)
    for row in rows:
        execution.execute(row, plan_gate=day3.PLAN_GATE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
