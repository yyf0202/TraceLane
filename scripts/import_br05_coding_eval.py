#!/usr/bin/env python3
"""Import the six budget-enforced BR-05 complexity-stress attempts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from tracelane.adapters.opencode import load_opencode_session
from tracelane.coding import AttemptEnd, SessionRef, finalize_coding_attempt, load_coding_task
from tracelane.coding.contracts import CodingTask
from tracelane.coding.session_importer import AttemptSession
from tracelane.coding.workspace import WorkspaceSnapshot, capture_workspace
from tracelane.contracts import canonical_json, sha256_json

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = (
    ROOT
    / "fixtures/coding/bericher-v0.3/tasks/BR-05-t1-causality-alignment-v2.json"
)
RAW_ROOT = ROOT / "artifacts/raw-opencode"
ARTIFACT_ROOT = ROOT / "artifacts/br05-coding-eval"
WORK_ROOT = Path(
    "/Users/efunyang/Documents/Codex/2026-07-26/realtime-voice-chat-3/work"
)


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


def _import(workflow: str, repeat: int, task: CodingTask) -> dict[str, object]:
    if workflow == "direct-build":
        raw = RAW_ROOT / f"br05-v2-r{repeat}-direct"
        worktree = WORK_ROOT / f"bericher-br05-v2-r{repeat}-direct"
        cli_name = "cli.jsonl"
        agent_kind = "build"
    else:
        raw = RAW_ROOT / f"br05-v2-r{repeat}-plan-build"
        worktree = WORK_ROOT / f"bericher-br05-r{repeat}-plan"
        cli_name = "plan-cli.jsonl"
        agent_kind = "plan"
    cli = _cli(raw / cli_name)
    execution = json.loads((raw / f"{cli_name}.execution.json").read_text(encoding="utf-8"))
    usage = execution["usage"]
    session_id = str(cli["session_id"])
    final_workspace = capture_workspace(worktree, task.baseline.commit_sha)
    session = AttemptSession(
        SessionRef(session_id, session_id, None, agent_kind),
        load_opencode_session(raw / f"{session_id}.jsonl"),
    )
    finalized = finalize_coding_attempt(
        task,
        attempt_id=f"br05-v2-{workflow}-r{repeat}",
        sessions=(session,),
        initial_workspace=_clean_snapshot(task.baseline.commit_sha),
        final_workspace=final_workspace,
        end=AttemptEnd(
            reason="budget_exhausted",
            final_answer=cli["final_answer"] or "Attempt stopped by its frozen budget.",
        ),
        repository=worktree,
        artifact_root=ARTIFACT_ROOT,
        harness_config={
            "workflow": workflow,
            "provider": "opencode-go",
            "model": "glm-5.2",
            "observer_revision": "7cd3d44",
            "execution_mode": "concurrent-pressure-calibration",
            "budget_enforcement": "runtime-v0.1",
            **(
                {"phase_reached": "plan", "build_started": False}
                if workflow == "plan-build"
                else {}
            ),
        },
        input_tokens=int(usage["input_tokens"]) + int(usage["cached_input_tokens"]),
        output_tokens=int(usage["output_tokens"]) + int(usage["reasoning_tokens"]),
        provider_cost={
            "currency": "USD",
            "amount": cli["amount_usd"],
            **usage,
            "wall_ms": execution["wall_ms"],
        },
        repeat=repeat,
    )
    finalized.store.write_json("output/execution.json", execution)
    score = _score(raw / "independent-grader.log")
    return {
        "workflow": workflow,
        "repeat": repeat,
        "run_id": finalized.store.run_id,
        "end_reason": execution["reason"],
        "functional_score": score["earned"],
        "functional_possible": score["possible"],
        "overall": finalized.grades.overall,
        "changed_paths": list(final_workspace.changed_paths),
        "model_tokens": usage["model_tokens"],
        "tool_calls": usage["tool_calls"],
        "wall_ms": execution["wall_ms"],
        "cost_usd": cli["amount_usd"],
    }


def main() -> int:
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    task = load_coding_task(json.loads(TASK_PATH.read_text(encoding="utf-8")))
    rows = [
        _import(workflow, repeat, task)
        for workflow in ("direct-build", "plan-build")
        for repeat in (1, 2, 3)
    ]
    result = {
        "schema_version": "coding-eval-complexity-stress/v0.1",
        "experiment": "TraceLane x OpenCode BR-05 complexity stress",
        "claim_scope": (
            "Concurrent budget-pressure calibration only. Provider contention and the absence "
            "of any completed attempt prevent a direct-versus-plan workflow comparison."
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
