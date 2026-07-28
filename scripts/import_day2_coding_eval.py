#!/usr/bin/env python3
"""Import the valid Day 2 matrix and build its bounded result table."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import run_day2_coding_eval as experiment

from tracelane.adapters.opencode import diagnose_last_provider_turn, load_opencode_session
from tracelane.coding import (
    AttemptEnd,
    SessionRef,
    finalize_coding_attempt,
    load_coding_task,
    load_plan_artifact,
)
from tracelane.coding.session_importer import AttemptSession
from tracelane.coding.workspace import WorkspaceSnapshot, capture_workspace
from tracelane.contracts import canonical_json, sha256_json

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "artifacts/raw-opencode"
ARTIFACT_ROOT = ROOT / "artifacts/day2-coding-eval"
WORK_ROOT = experiment.WORK_ROOT


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
        if isinstance(row.get("sessionID"), str):
            session_ids.add(row["sessionID"])
        part = row.get("part")
        if not isinstance(part, dict):
            continue
        if row.get("type") == "text" and isinstance(part.get("text"), str):
            final_answer = part["text"]
        if part.get("type") == "step-finish":
            amount += float(part.get("cost", 0.0))
    if len(session_ids) != 1:
        raise ValueError(f"{path} must contain exactly one root session")
    return {
        "session_id": next(iter(session_ids)),
        "final_answer": final_answer,
        "amount_usd": round(amount, 8),
    }


def _prefixed_score(path: Path, prefix: str) -> dict[str, object] | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            value = json.loads(line.removeprefix(prefix))
            return value if isinstance(value, dict) else None
    return None


def _score_output(output: str, prefix: str) -> dict[str, object] | None:
    for line in output.splitlines():
        if line.startswith(prefix):
            value = json.loads(line.removeprefix(prefix))
            return value if isinstance(value, dict) else None
    return None


def _adjudicated_score(
    spec: experiment.AttemptSpec,
    worktree: Path,
) -> dict[str, object] | None:
    graders = {
        "BR-07": "br07_v2_hidden_acceptance.py",
        "BR-08": "br08_v2_hidden_acceptance.py",
    }
    grader_name = graders.get(spec.task.short_id)
    if grader_name is None:
        return None
    grader = ROOT / "tests/fixtures/coding_tasks" / grader_name
    result = subprocess.run(
        [str(experiment.GRADER_PYTHON), str(grader), "."],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    score = _score_output(result.stdout, "TRACELANE_SCORE=")
    if score is None:
        raise ValueError(f"{spec.run_slug} has no v2 adjudicated score")
    return {
        **score,
        "grader": grader_name,
        "grader_version": 2,
        "runner_exit_code": result.returncode,
    }


def _adjudicated_plan_score(
    spec: experiment.AttemptSpec,
    plan_path: Path,
) -> dict[str, object] | None:
    gates = {
        "BR-06": "day2_v2_plan_acceptance.py",
        "BR-08": "day2_v3_plan_acceptance.py",
    }
    gate_name = gates.get(spec.task.short_id)
    if gate_name is None or not plan_path.exists():
        return None
    gate = ROOT / "tests/fixtures/coding_tasks" / gate_name
    result = subprocess.run(
        [str(experiment.PYTHON), str(gate), str(plan_path), spec.task.short_id],
        capture_output=True,
        text=True,
        check=False,
    )
    score = _score_output(result.stdout, "TRACELANE_PLAN_SCORE=")
    if score is None:
        raise ValueError(f"{spec.run_slug} has no adjudicated plan score")
    return {
        **score,
        "gate": gate_name,
        "gate_version": 2 if spec.task.short_id == "BR-06" else 3,
        "runner_exit_code": result.returncode,
    }


def _termination(raw: Path, cli_name: str) -> dict[str, object] | None:
    path = raw / f"{cli_name}.termination.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _diagnosis(session: object, termination: dict[str, object] | None) -> dict[str, object]:
    try:
        value = diagnose_last_provider_turn(session, termination=termination)
    except ValueError as exc:
        return {"state": "no_observed_provider_turn", "error": str(exc)}
    return {
        "request_id": value.request_id,
        "state": value.state,
        "http_status": value.http_status,
        "first_token_ms": value.first_token_ms,
        "last_response_at": value.last_response_at,
        "local_termination": value.local_termination,
    }


def _end_reason(executions: list[dict[str, object]], workflow_end: dict[str, object]) -> str:
    if workflow_end.get("reason") in {"plan_gate_failed", "plan_modified_workspace"}:
        return "blocked"
    reason = str(executions[-1]["reason"])
    if reason.endswith("_budget_exhausted"):
        return "budget_exhausted"
    if reason == "completed":
        return "completed"
    return "crashed"


def _import(spec: experiment.AttemptSpec) -> dict[str, object]:
    task = load_coding_task(json.loads(spec.task.manifest.read_text(encoding="utf-8")))
    raw = RAW_ROOT / spec.run_slug
    worktree = WORK_ROOT / f"bericher-{spec.run_slug}"
    cli_names = (
        ["cli.jsonl"]
        if spec.workflow == "direct-build"
        else [
            name
            for name in ("plan-cli.jsonl", "build-cli.jsonl")
            if (raw / f"{name}.execution.json").exists()
        ]
    )
    if not cli_names:
        raise ValueError(f"{spec.run_slug} has no completed phase")
    cli_rows = [_cli(raw / name) for name in cli_names]
    executions = [
        json.loads((raw / f"{name}.execution.json").read_text(encoding="utf-8"))
        for name in cli_names
    ]
    workflow_end = json.loads((raw / "workflow-end.json").read_text(encoding="utf-8"))
    final_workspace = capture_workspace(worktree, task.baseline.commit_sha)
    if final_workspace.head_commit != task.baseline.commit_sha:
        raise ValueError(f"{spec.run_slug} baseline moved")

    root_id = str(cli_rows[0]["session_id"])
    sessions: list[AttemptSession] = []
    diagnoses: list[dict[str, object]] = []
    for index, (name, row) in enumerate(zip(cli_names, cli_rows, strict=True)):
        session_id = str(row["session_id"])
        loaded = load_opencode_session(raw / f"{session_id}.jsonl")
        sessions.append(
            AttemptSession(
                SessionRef(
                    session_id,
                    root_id,
                    root_id if index else None,
                    "plan" if name.startswith("plan") else "build",
                ),
                loaded,
            )
        )
        diagnoses.append(
            {
                "cli_file": name,
                **_diagnosis(loaded, _termination(raw, name)),
            }
        )

    usage = {
        key: sum(int(value["usage"][key]) for value in executions)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "model_tokens",
            "tool_calls",
        )
    }
    wall_ms = sum(int(value["wall_ms"]) for value in executions)
    amount_usd = round(sum(float(row["amount_usd"]) for row in cli_rows), 8)
    harnesses = [value.get("harness") for value in executions]
    observed_harnesses = [value for value in harnesses if isinstance(value, dict)]
    if observed_harnesses and any(value != observed_harnesses[0] for value in observed_harnesses):
        raise ValueError(f"{spec.run_slug} changed harness between phases")
    harness = observed_harnesses[0] if observed_harnesses else None
    plan_path = raw / "handoff/plan.json"
    plan_artifact = (
        load_plan_artifact(json.loads(plan_path.read_text(encoding="utf-8")))
        if plan_path.exists()
        else None
    )
    plan_score = _prefixed_score(raw / "plan-gate.log", "TRACELANE_PLAN_SCORE=")
    adjudicated_plan_score = _adjudicated_plan_score(spec, plan_path)
    score = _prefixed_score(raw / "independent-grader.log", "TRACELANE_SCORE=")
    if score is None:
        raise ValueError(f"{spec.run_slug} has no independent functional score")
    adjudicated_score = _adjudicated_score(spec, worktree)
    end_reason = _end_reason(executions, workflow_end)
    final_answer = cli_rows[-1]["final_answer"]
    finalized = finalize_coding_attempt(
        task,
        attempt_id=spec.run_slug,
        sessions=tuple(sessions),
        initial_workspace=_clean_snapshot(task.baseline.commit_sha),
        final_workspace=final_workspace,
        end=AttemptEnd(
            reason=end_reason,
            final_answer=(
                str(final_answer)
                if final_answer
                else f"Attempt ended with {end_reason} under the frozen budget."
            ),
        ),
        repository=worktree,
        artifact_root=ARTIFACT_ROOT,
        harness_config={
            "workflow": spec.workflow,
            "provider": "ark",
            "model": spec.model,
            "harness": harness,
            "observer_revision": "06d9803be9",
            "execution_mode": "strictly-serial-paired-matrix",
            "automatic_attempt_retries": 0,
            "provider_turn_watchdog_seconds": 300,
            "shared_task_budget": True,
            "phase_link": "manual-cli-split" if len(sessions) == 2 else None,
            "build_started": spec.workflow == "direct-build" or len(sessions) == 2,
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
                {"cli_file": name, **execution}
                for name, execution in zip(cli_names, executions, strict=True)
            ],
        },
        repeat=spec.repeat,
    )
    finalized.store.write_json("output/independent-functional-score.json", score)
    if adjudicated_score is not None:
        finalized.store.write_json(
            "output/adjudicated-functional-score.json",
            adjudicated_score,
        )
    finalized.store.write_json("output/provider-turn-diagnoses.json", {"phases": diagnoses})
    finalized.store.write_json("output/workflow-end.json", workflow_end)
    if plan_score is not None:
        finalized.store.write_json("output/plan-gate-score.json", plan_score)
    if adjudicated_plan_score is not None:
        finalized.store.write_json(
            "output/adjudicated-plan-gate-score.json",
            adjudicated_plan_score,
        )
    trace_bytes = sum((raw / f"{row['session_id']}.jsonl").stat().st_size for row in cli_rows)
    provider_transport_valid = all(
        phase.get("local_termination")
        or phase["state"] in {"completed", "model_completed_processor_incomplete"}
        for phase in diagnoses
    )
    analysis_score = adjudicated_score or score
    return {
        "attempt_id": spec.run_slug,
        "task": spec.task.short_id,
        "task_version": task.version,
        "model": spec.model,
        "workflow": spec.workflow,
        "repeat": spec.repeat,
        "run_id": finalized.store.run_id,
        "end_reason": end_reason,
        "execution_reason": executions[-1]["reason"],
        "build_started": spec.workflow == "direct-build" or len(sessions) == 2,
        "plan_score": plan_score["earned"] if plan_score else None,
        "adjudicated_plan_score": (
            adjudicated_plan_score["earned"] if adjudicated_plan_score else None
        ),
        "analysis_plan_score": (
            adjudicated_plan_score["earned"]
            if adjudicated_plan_score
            else plan_score["earned"]
            if plan_score
            else None
        ),
        "functional_score": score["earned"],
        "functional_possible": score["possible"],
        "adjudicated_functional_score": (
            adjudicated_score["earned"] if adjudicated_score else None
        ),
        "analysis_functional_score": analysis_score["earned"],
        "analysis_functional_possible": analysis_score["possible"],
        "capability_analysis_eligible": provider_transport_valid,
        "overall": finalized.grades.overall,
        "acceptance": finalized.grades.acceptance.status,
        "diff": finalized.grades.diff.status,
        "changed_paths": list(final_workspace.changed_paths),
        "model_tokens": usage["model_tokens"],
        "tool_calls": usage["tool_calls"],
        "wall_ms": wall_ms,
        "cost_usd": amount_usd,
        "raw_trace_bytes": trace_bytes,
        "provider_turns": diagnoses,
        "harness": harness,
    }


def main() -> int:
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    rows = [_import(spec) for spec in experiment.matrix()]
    result = {
        "schema_version": "coding-eval-day2/v0.1",
        "experiment": "TraceLane x OpenCode Day 2 complex-task matrix",
        "provider": "ark",
        "models": list(experiment.MODELS),
        "claim_scope": (
            "Strictly serial paired descriptive evidence across three tasks and three models; "
            "no statistical-significance claim and no pooling with OpenCode Go."
        ),
        "excluded_pilot": json.loads(
            (ROOT / "fixtures/coding/bericher-v0.6/day2-experiment.json").read_text(
                encoding="utf-8"
            )
        )["excluded_pilot"],
        "attempts": rows,
    }
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_ROOT / "results.json").write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
