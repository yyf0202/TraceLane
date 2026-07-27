#!/usr/bin/env python3
"""Import the first strictly serial BR-05 direct-versus-plan pair."""

from __future__ import annotations

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
    load_plan_artifact,
)
from tracelane.coding.contracts import CodingTask
from tracelane.coding.session_importer import AttemptSession
from tracelane.coding.workspace import WorkspaceSnapshot, capture_workspace
from tracelane.contracts import canonical_json, sha256_json

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / "fixtures/coding/bericher-v0.3/tasks/BR-05-t1-causality-alignment-v2.json"
RAW_ROOT = ROOT / "artifacts/raw-opencode"
ARTIFACT_ROOT = ROOT / "artifacts/br05-serial-paired-eval"
WORK_ROOT = Path("/Users/efunyang/Documents/Codex/2026-07-26/realtime-voice-chat-3/work")


@dataclass(frozen=True)
class AttemptSpec:
    attempt_id: str
    workflow: str
    worktree: Path
    raw_directory: Path
    cli_files: tuple[str, ...]


def _clean_snapshot(commit: str) -> WorkspaceSnapshot:
    empty = hashlib.sha256(b"").hexdigest()
    workspace = sha256_json(
        {
            "baseline_commit": commit,
            "head_commit": commit,
            "patch_sha256": empty,
            "untracked_files": [],
        }
    )
    return WorkspaceSnapshot(commit, commit, "", empty, (), (), workspace)


def _cli(path: Path) -> dict[str, object]:
    session_ids: set[str] = set()
    final_answer: str | None = None
    amount = 0.0
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        session_id = row.get("sessionID")
        if isinstance(session_id, str):
            session_ids.add(session_id)
        part = row.get("part")
        if not isinstance(part, dict):
            continue
        if row.get("type") == "text" and isinstance(part.get("text"), str):
            final_answer = part["text"]
        if part.get("type") == "step-finish":
            amount += float(part.get("cost", 0.0))
    if len(session_ids) != 1:
        raise ValueError(f"{path} must contain exactly one session")
    return {
        "session_id": next(iter(session_ids)),
        "final_answer": final_answer,
        "amount_usd": round(amount, 8),
    }


def _score(path: Path) -> dict[str, object]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("TRACELANE_SCORE="):
            value = json.loads(line.removeprefix("TRACELANE_SCORE="))
            if isinstance(value, dict):
                return value
    raise ValueError(f"{path} has no functional score")


def _sessions(
    spec: AttemptSpec, cli_rows: tuple[dict[str, object], ...]
) -> tuple[AttemptSession, ...]:
    session_ids = tuple(str(row["session_id"]) for row in cli_rows)
    if spec.workflow == "direct-build":
        session_id = session_ids[0]
        return (
            AttemptSession(
                SessionRef(session_id, session_id, None, "build"),
                load_opencode_session(spec.raw_directory / f"{session_id}.jsonl"),
            ),
        )
    plan_session_id, build_session_id = session_ids
    return (
        AttemptSession(
            SessionRef(plan_session_id, plan_session_id, None, "plan"),
            load_opencode_session(spec.raw_directory / f"{plan_session_id}.jsonl"),
        ),
        AttemptSession(
            SessionRef(
                build_session_id,
                plan_session_id,
                plan_session_id,
                "build",
            ),
            load_opencode_session(spec.raw_directory / f"{build_session_id}.jsonl"),
        ),
    )


def _import(spec: AttemptSpec, task: CodingTask) -> dict[str, object]:
    cli_rows = tuple(_cli(spec.raw_directory / name) for name in spec.cli_files)
    executions = tuple(
        json.loads((spec.raw_directory / f"{name}.execution.json").read_text(encoding="utf-8"))
        for name in spec.cli_files
    )
    final_workspace = capture_workspace(spec.worktree, task.baseline.commit_sha)
    if final_workspace.head_commit != task.baseline.commit_sha:
        raise ValueError(f"{spec.attempt_id} is not pinned to the frozen baseline")
    if set(final_workspace.changed_paths) - set(task.diff_policy.editable_paths):
        raise ValueError(f"{spec.attempt_id} changed a non-editable path")

    usage = {
        key: sum(int(execution["usage"][key]) for execution in executions)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "model_tokens",
            "tool_calls",
        )
    }
    wall_ms = sum(int(execution["wall_ms"]) for execution in executions)
    amount_usd = round(sum(float(row["amount_usd"]) for row in cli_rows), 8)
    plan_artifact = (
        load_plan_artifact(
            json.loads((spec.raw_directory / "handoff/plan.json").read_text(encoding="utf-8"))
        )
        if spec.workflow == "plan-build"
        else None
    )
    execution_reason = str(executions[-1]["reason"])
    end_reason = (
        "budget_exhausted" if execution_reason.endswith("_budget_exhausted") else execution_reason
    )
    final_answer = cli_rows[-1]["final_answer"]
    finalized = finalize_coding_attempt(
        task,
        attempt_id=spec.attempt_id,
        sessions=_sessions(spec, cli_rows),
        initial_workspace=_clean_snapshot(task.baseline.commit_sha),
        final_workspace=final_workspace,
        end=AttemptEnd(
            reason=end_reason,
            final_answer=(
                str(final_answer)
                if final_answer
                else f"Attempt ended with {end_reason} under its frozen budget."
            ),
        ),
        repository=spec.worktree,
        artifact_root=ARTIFACT_ROOT,
        harness_config={
            "workflow": spec.workflow,
            "provider": "opencode-go",
            "model": "glm-5.2",
            "observer_revision": "7cd3d44",
            "execution_mode": "strictly-serial-pair",
            "budget_enforcement": "runtime-v0.1",
            "shared_task_budget": True,
            **(
                {"phase_link": "manual-cli-split", "plan_gate": "passed-100-of-100"}
                if spec.workflow == "plan-build"
                else {}
            ),
        },
        plan_artifact=plan_artifact,
        input_tokens=usage["input_tokens"] + usage["cached_input_tokens"],
        output_tokens=usage["output_tokens"] + usage["reasoning_tokens"],
        provider_cost={
            "currency": "USD",
            "amount": amount_usd,
            **usage,
            "wall_ms": wall_ms,
            "phases": [
                {
                    "cli_file": name,
                    "session_id": row["session_id"],
                    "amount_usd": row["amount_usd"],
                    **execution,
                }
                for name, row, execution in zip(spec.cli_files, cli_rows, executions, strict=True)
            ],
        },
    )
    score = _score(spec.raw_directory / "independent-grader.log")
    finalized.store.write_json("output/independent-functional-score.json", score)
    finalized.store.write_json(
        "output/executions.json",
        {"phases": list(executions), "combined_wall_ms": wall_ms},
    )
    trace_bytes = sum(
        (spec.raw_directory / f"{row['session_id']}.jsonl").stat().st_size for row in cli_rows
    )
    return {
        "attempt_id": spec.attempt_id,
        "workflow": spec.workflow,
        "run_id": finalized.store.run_id,
        "end_reason": end_reason,
        "execution_reason": execution_reason,
        "functional_score": score["earned"],
        "functional_possible": score["possible"],
        "overall": finalized.grades.overall,
        "acceptance": finalized.grades.acceptance.status,
        "diff": finalized.grades.diff.status,
        "changed_paths": list(final_workspace.changed_paths),
        "session_count": len(cli_rows),
        "raw_trace_bytes": trace_bytes,
        "model_tokens": usage["model_tokens"],
        "tool_calls": usage["tool_calls"],
        "wall_ms": wall_ms,
        "cost_usd": amount_usd,
    }


def main() -> int:
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    task = load_coding_task(json.loads(TASK_PATH.read_text(encoding="utf-8")))
    specs = (
        AttemptSpec(
            "br05-serial-r1-plan-build",
            "plan-build",
            WORK_ROOT / "bericher-br05-plan-gate-1",
            RAW_ROOT / "br05-plan-gate-1",
            ("plan-cli.jsonl", "build-cli.jsonl"),
        ),
        AttemptSpec(
            "br05-serial-r1-direct",
            "direct-build",
            WORK_ROOT / "bericher-br05-serial-r1-direct",
            RAW_ROOT / "br05-serial-r1-direct",
            ("cli.jsonl",),
        ),
    )
    rows = [_import(spec, task) for spec in specs]
    result = {
        "schema_version": "coding-eval-serial-pair/v0.1",
        "experiment": "TraceLane x OpenCode BR-05 strict serial pair 1",
        "model": "opencode-go/glm-5.2",
        "claim_scope": (
            "One strictly serial matched pair. Results are descriptive evidence about this "
            "pair and do not establish a statistically significant workflow effect."
        ),
        "attempts": rows,
    }
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_ROOT / "results.json").write_text(
        canonical_json(result) + "\n",
        encoding="utf-8",
    )
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
