#!/usr/bin/env python3
"""Import the four trusted TraceLane × OpenCode Day 1 coding attempts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from tracelane.adapters.opencode import load_opencode_session
from tracelane.coding import (
    AttemptEnd,
    SessionRef,
    finalize_coding_attempt,
    load_coding_task,
)
from tracelane.coding.session_importer import AttemptSession
from tracelane.coding.workspace import WorkspaceSnapshot, capture_workspace
from tracelane.contracts import canonical_json, sha256_json

ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = ROOT / "fixtures" / "coding" / "bericher-v0.1"


@dataclass(frozen=True)
class AttemptSpec:
    task_file: str
    attempt_id: str
    workflow: str
    worktree: Path
    raw_directory: Path
    cli_files: tuple[str, ...]


def _load_task(relative: str):
    value = json.loads((SUITE_ROOT / "tasks" / relative).read_text(encoding="utf-8"))
    return load_coding_task(value)


def _clean_snapshot(baseline_commit: str) -> WorkspaceSnapshot:
    patch_sha256 = hashlib.sha256(b"").hexdigest()
    workspace_sha256 = sha256_json(
        {
            "baseline_commit": baseline_commit,
            "head_commit": baseline_commit,
            "patch_sha256": patch_sha256,
            "untracked_files": [],
        }
    )
    return WorkspaceSnapshot(
        baseline_commit=baseline_commit,
        head_commit=baseline_commit,
        patch="",
        patch_sha256=patch_sha256,
        changed_paths=(),
        untracked_files=(),
        workspace_sha256=workspace_sha256,
    )


def _cli_summary(paths: tuple[Path, ...]) -> dict[str, object]:
    phases: list[dict[str, object]] = []
    all_session_ids: list[str] = []
    final_answer: str | None = None
    for path in paths:
        session_ids: set[str] = set()
        first_timestamp: int | None = None
        last_timestamp: int | None = None
        input_tokens = 0
        cached_input_tokens = 0
        output_tokens = 0
        reasoning_tokens = 0
        amount = 0.0
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            session_id = row.get("sessionID")
            if isinstance(session_id, str):
                session_ids.add(session_id)
            timestamp = row.get("timestamp")
            if isinstance(timestamp, int):
                first_timestamp = (
                    timestamp if first_timestamp is None else min(first_timestamp, timestamp)
                )
                last_timestamp = (
                    timestamp if last_timestamp is None else max(last_timestamp, timestamp)
                )
            part = row.get("part")
            if not isinstance(part, dict):
                continue
            if row.get("type") == "text" and isinstance(part.get("text"), str):
                final_answer = part["text"]
            if part.get("type") != "step-finish":
                continue
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                input_tokens += int(tokens.get("input", 0))
                output_tokens += int(tokens.get("output", 0))
                reasoning_tokens += int(tokens.get("reasoning", 0))
                cache = tokens.get("cache")
                if isinstance(cache, dict):
                    cached_input_tokens += int(cache.get("read", 0))
            amount += float(part.get("cost", 0.0))
        if len(session_ids) != 1:
            raise ValueError(f"{path.name} must contain exactly one OpenCode session")
        session_id = next(iter(session_ids))
        all_session_ids.append(session_id)
        phases.append(
            {
                "name": path.stem.replace("-cli", ""),
                "session_id": session_id,
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
                "reasoning_tokens": reasoning_tokens,
                "amount_usd": round(amount, 8),
                "wall_ms": (
                    last_timestamp - first_timestamp
                    if first_timestamp is not None and last_timestamp is not None
                    else 0
                ),
            }
        )
    return {
        "currency": "USD",
        "amount": round(sum(float(phase["amount_usd"]) for phase in phases), 8),
        "input_tokens": sum(int(phase["input_tokens"]) for phase in phases),
        "cached_input_tokens": sum(int(phase["cached_input_tokens"]) for phase in phases),
        "output_tokens": sum(int(phase["output_tokens"]) for phase in phases),
        "reasoning_tokens": sum(int(phase["reasoning_tokens"]) for phase in phases),
        "wall_ms": sum(int(phase["wall_ms"]) for phase in phases),
        "session_ids": all_session_ids,
        "phases": phases,
        "final_answer": final_answer,
    }


def _sessions(
    workflow: str, raw_directory: Path, session_ids: list[str]
) -> tuple[AttemptSession, ...]:
    if workflow == "direct-build":
        session_id = session_ids[0]
        return (
            AttemptSession(
                SessionRef(session_id, session_id, None, "build"),
                load_opencode_session(raw_directory / f"{session_id}.jsonl"),
            ),
        )
    if workflow != "plan-build" or len(session_ids) != 2:
        raise ValueError("plan-build attempts require one plan and one build session")
    root_session_id, build_session_id = session_ids
    return (
        AttemptSession(
            SessionRef(root_session_id, root_session_id, None, "plan"),
            load_opencode_session(raw_directory / f"{root_session_id}.jsonl"),
        ),
        AttemptSession(
            SessionRef(
                build_session_id,
                root_session_id,
                root_session_id,
                "build",
            ),
            load_opencode_session(raw_directory / f"{build_session_id}.jsonl"),
        ),
    )


def import_attempt(spec: AttemptSpec, artifact_root: Path) -> dict[str, object]:
    task = _load_task(spec.task_file)
    final_workspace = capture_workspace(spec.worktree, task.baseline.commit_sha)
    if final_workspace.head_commit != task.baseline.commit_sha:
        raise ValueError(f"{spec.attempt_id} worktree is not pinned to its baseline")
    if set(final_workspace.changed_paths) - set(task.diff_policy.editable_paths):
        raise ValueError(f"{spec.attempt_id} has changes outside its editable paths")

    cli = _cli_summary(tuple(spec.raw_directory / relative for relative in spec.cli_files))
    session_ids = list(cli["session_ids"])
    provider_cost = {key: value for key, value in cli.items() if key != "final_answer"}
    finalized = finalize_coding_attempt(
        task,
        attempt_id=spec.attempt_id,
        sessions=_sessions(spec.workflow, spec.raw_directory, session_ids),
        initial_workspace=_clean_snapshot(task.baseline.commit_sha),
        final_workspace=final_workspace,
        end=AttemptEnd(
            reason="completed",
            final_answer=cli["final_answer"] or "OpenCode completed the requested change.",
        ),
        repository=spec.worktree,
        artifact_root=artifact_root,
        harness_config={
            "workflow": spec.workflow,
            "provider": "opencode-go",
            "model": "glm-5.2",
            "observer_revision": "7cd3d44",
            **({"phase_link": "manual-cli-split"} if spec.workflow == "plan-build" else {}),
        },
        input_tokens=int(cli["input_tokens"]) + int(cli["cached_input_tokens"]),
        output_tokens=int(cli["output_tokens"]) + int(cli["reasoning_tokens"]),
        provider_cost=provider_cost,
    )
    trace_bytes = sum(
        (spec.raw_directory / f"{session_id}.jsonl").stat().st_size for session_id in session_ids
    )
    return {
        "task_id": task.task_id,
        "attempt_id": spec.attempt_id,
        "workflow": spec.workflow,
        "run_id": finalized.store.run_id,
        "overall": finalized.grades.overall,
        "acceptance": finalized.grades.acceptance.status,
        "diff": finalized.grades.diff.status,
        "changed_paths": list(final_workspace.changed_paths),
        "session_count": len(session_ids),
        "raw_trace_bytes": trace_bytes,
        "provider_cost_usd": provider_cost["amount"],
        "input_tokens": provider_cost["input_tokens"],
        "cached_input_tokens": provider_cost["cached_input_tokens"],
        "output_tokens": provider_cost["output_tokens"],
        "agent_wall_ms": provider_cost["wall_ms"],
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, default=ROOT / "artifacts" / "raw-opencode")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=ROOT / "artifacts" / "day1-coding-eval",
    )
    parser.add_argument("--br01-direct-worktree", type=Path, required=True)
    parser.add_argument("--br01-plan-worktree", type=Path, required=True)
    parser.add_argument("--br02-direct-worktree", type=Path, required=True)
    parser.add_argument("--br02-plan-worktree", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    attempts = (
        AttemptSpec(
            "BR-01-pit-value-date.json",
            "br01-glm-direct-day1",
            "direct-build",
            args.br01_direct_worktree,
            args.raw_root / "br01-final-direct",
            ("cli.jsonl",),
        ),
        AttemptSpec(
            "BR-01-pit-value-date.json",
            "br01-glm-plan-build-day1",
            "plan-build",
            args.br01_plan_worktree,
            args.raw_root / "br01-final-plan",
            ("plan-cli.jsonl", "build-cli.jsonl"),
        ),
        AttemptSpec(
            "BR-02-expanded-static-detection.json",
            "br02-glm-direct-day1",
            "direct-build",
            args.br02_direct_worktree,
            args.raw_root / "br02-glm-direct",
            ("cli.jsonl",),
        ),
        AttemptSpec(
            "BR-02-expanded-static-detection.json",
            "br02-glm-plan-build-day1",
            "plan-build",
            args.br02_plan_worktree,
            args.raw_root / "br02-glm-plan",
            ("plan-cli.jsonl", "build-cli.jsonl"),
        ),
    )
    rows = [import_attempt(spec, args.artifact_root) for spec in attempts]
    result = {
        "schema_version": "coding-eval-day1/v0.1",
        "experiment": "TraceLane x OpenCode Day 1",
        "model": "opencode-go/glm-5.2",
        "claim_scope": (
            "Four integration samples validate the evaluation chain only; "
            "they do not establish a statistically significant workflow effect."
        ),
        "attempt_count": len(rows),
        "all_attempts_trusted": all(
            row["overall"] == "pass" and row["acceptance"] == "pass" and row["diff"] == "pass"
            for row in rows
        ),
        "attempts": rows,
    }
    args.artifact_root.mkdir(parents=True, exist_ok=True)
    (args.artifact_root / "day1-results.json").write_text(
        canonical_json(result) + "\n",
        encoding="utf-8",
    )
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
