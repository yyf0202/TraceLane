#!/usr/bin/env python3
"""Import Day 2 recovery attempts and corrected-gate build replays."""

from __future__ import annotations

import json
import os
from pathlib import Path

import import_day2_coding_eval as day2_import
import run_day2_recovery as recovery

from tracelane.adapters.opencode import load_opencode_session
from tracelane.coding import (
    AttemptEnd,
    SessionRef,
    finalize_coding_attempt,
    load_coding_task,
    load_plan_artifact,
)
from tracelane.coding.session_importer import AttemptSession
from tracelane.coding.workspace import capture_workspace
from tracelane.contracts import canonical_json

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "artifacts/raw-opencode"
ARTIFACT_ROOT = ROOT / "artifacts/day2-recovery"
RESULTS = ARTIFACT_ROOT / "results.json"


def _usage(executions: list[dict[str, object]]) -> dict[str, int]:
    return {
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


def _score(path: Path) -> dict[str, object]:
    value = day2_import._prefixed_score(path, "TRACELANE_SCORE=")
    if value is None:
        raise ValueError(f"{path} has no functional score")
    return value


def _cli_failure_stage(path: Path) -> str | None:
    errors: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("type") == "error" and isinstance(row.get("error"), dict):
            errors.append(row["error"])
    if not errors:
        return None
    data = errors[-1].get("data")
    message = str(data.get("message", "")) if isinstance(data, dict) else ""
    if "SSE read timed out" in message:
        return "provider_stream_interrupted"
    if "Cannot connect to API" in message:
        return "gateway_no_response_headers"
    return "opencode_provider_error"


def _import_recovery(spec: recovery.experiment.AttemptSpec) -> dict[str, object]:
    row = day2_import._import(spec)
    execution_usable = row["execution_reason"] in {
        "completed",
        "wall_budget_exhausted",
        "tool_budget_exhausted",
        "token_budget_exhausted",
    }
    return {
        **row,
        "layer": "quota_recovery_complete_pairs",
        "capability_analysis_eligible": execution_usable,
        "recovery_status": "usable" if execution_usable else "infrastructure_failure",
        "failure_stage": _cli_failure_stage(
            RAW_ROOT
            / spec.run_slug
            / ("cli.jsonl" if spec.workflow == "direct-build" else "plan-cli.jsonl")
        ),
    }


def _import_replay(spec: recovery.ReplaySpec) -> dict[str, object]:
    run_spec = spec.run_spec
    source_raw = RAW_ROOT / spec.source.run_slug
    replay_raw = RAW_ROOT / run_spec.run_slug
    worktree = recovery.experiment.WORK_ROOT / f"bericher-{run_spec.run_slug}"
    task = load_coding_task(
        json.loads(spec.source.task.manifest.read_text(encoding="utf-8"))
    )
    source_cli = day2_import._cli(source_raw / "plan-cli.jsonl")
    build_cli = day2_import._cli(replay_raw / "build-cli.jsonl")
    source_execution = json.loads(
        (source_raw / "plan-cli.jsonl.execution.json").read_text(encoding="utf-8")
    )
    build_execution = json.loads(
        (replay_raw / "build-cli.jsonl.execution.json").read_text(encoding="utf-8")
    )
    executions = [source_execution, build_execution]
    usage = _usage(executions)
    final_workspace = capture_workspace(worktree, task.baseline.commit_sha)
    source_session_id = str(source_cli["session_id"])
    build_session_id = str(build_cli["session_id"])
    source_session = load_opencode_session(source_raw / f"{source_session_id}.jsonl")
    build_session = load_opencode_session(replay_raw / f"{build_session_id}.jsonl")
    sessions = (
        AttemptSession(
            SessionRef(source_session_id, source_session_id, None, "plan"),
            source_session,
        ),
        AttemptSession(
            SessionRef(build_session_id, source_session_id, source_session_id, "build"),
            build_session,
        ),
    )
    source_record = json.loads(
        (replay_raw / "replay-source.json").read_text(encoding="utf-8")
    )
    plan_artifact = load_plan_artifact(
        json.loads((source_raw / "handoff/plan.json").read_text(encoding="utf-8"))
    )
    frozen_plan_score = day2_import._prefixed_score(
        source_raw / "plan-gate.log",
        "TRACELANE_PLAN_SCORE=",
    )
    corrected_plan_score = day2_import._prefixed_score(
        replay_raw / "corrected-plan-gate.log",
        "TRACELANE_PLAN_SCORE=",
    )
    if frozen_plan_score is None or corrected_plan_score is None:
        raise ValueError(f"{run_spec.run_slug} is missing a plan score")
    frozen_score = _score(replay_raw / "independent-grader.log")
    adjudicated_path = replay_raw / "adjudicated-grader.log"
    adjudicated_score = _score(adjudicated_path) if adjudicated_path.exists() else None
    analysis_score = adjudicated_score or frozen_score
    amount_usd = round(
        float(source_cli["amount_usd"]) + float(build_cli["amount_usd"]),
        8,
    )
    diagnoses = [
        {
            "cli_file": "source/plan-cli.jsonl",
            **day2_import._diagnosis(
                source_session,
                day2_import._termination(source_raw, "plan-cli.jsonl"),
            ),
        },
        {
            "cli_file": "replay/build-cli.jsonl",
            **day2_import._diagnosis(
                build_session,
                day2_import._termination(replay_raw, "build-cli.jsonl"),
            ),
        },
    ]
    end_reason = (
        "budget_exhausted"
        if str(build_execution["reason"]).endswith("_budget_exhausted")
        else "completed"
        if build_execution["reason"] == "completed"
        else "crashed"
    )
    final_answer = build_cli["final_answer"]
    finalized = finalize_coding_attempt(
        task,
        attempt_id=run_spec.run_slug,
        sessions=sessions,
        initial_workspace=day2_import._clean_snapshot(task.baseline.commit_sha),
        final_workspace=final_workspace,
        end=AttemptEnd(
            reason=end_reason,
            final_answer=(
                str(final_answer)
                if final_answer
                else f"Gate replay ended with {end_reason} under the inherited budget."
            ),
        ),
        repository=worktree,
        artifact_root=ARTIFACT_ROOT,
        harness_config={
            "workflow": "plan-build-gate-replay",
            "provider": "ark",
            "model": run_spec.model,
            "observer_revision": "06d9803be9",
            "execution_mode": "strictly-serial-build-only-replay",
            "automatic_attempt_retries": 0,
            "provider_turn_watchdog_seconds": 300,
            "shared_task_budget": True,
            "phase_link": "manual-cli-split-replay",
            "source_attempt_id": spec.source.run_slug,
            "source_plan_sha256": source_record["source_plan_sha256"],
            "source_build_prompt_sha256": source_record[
                "source_build_prompt_sha256"
            ],
            "corrected_gate_version": spec.gate_version,
            "remaining_build_budget": source_record["remaining_build_budget"],
        },
        plan_artifact=plan_artifact,
        input_tokens=usage["input_tokens"] + usage["cached_input_tokens"],
        output_tokens=usage["output_tokens"] + usage["reasoning_tokens"],
        provider_cost={
            "currency": "USD",
            "amount": amount_usd,
            **usage,
            "wall_ms": sum(int(value["wall_ms"]) for value in executions),
            "source_plan": source_execution,
            "incremental_replay_build": build_execution,
        },
        repeat=run_spec.repeat,
    )
    finalized.store.write_json("output/frozen-functional-score.json", frozen_score)
    if adjudicated_score is not None:
        finalized.store.write_json(
            "output/adjudicated-functional-score.json",
            adjudicated_score,
        )
    finalized.store.write_json("output/frozen-plan-gate-score.json", frozen_plan_score)
    finalized.store.write_json(
        "output/corrected-plan-gate-score.json",
        corrected_plan_score,
    )
    finalized.store.write_json("output/provider-turn-diagnoses.json", {"phases": diagnoses})
    finalized.store.write_json("output/replay-source.json", source_record)
    trace_bytes = (source_raw / f"{source_session_id}.jsonl").stat().st_size + (
        replay_raw / f"{build_session_id}.jsonl"
    ).stat().st_size
    return {
        "layer": "corrected_gate_build_replays",
        "attempt_id": run_spec.run_slug,
        "source_attempt_id": spec.source.run_slug,
        "task": spec.source.task.short_id,
        "task_version": task.version,
        "model": run_spec.model,
        "repeat": run_spec.repeat,
        "run_id": finalized.store.run_id,
        "end_reason": end_reason,
        "execution_reason": build_execution["reason"],
        "build_started": True,
        "frozen_plan_score": frozen_plan_score["earned"],
        "corrected_plan_score": corrected_plan_score["earned"],
        "frozen_functional_score": frozen_score["earned"],
        "adjudicated_functional_score": (
            adjudicated_score["earned"] if adjudicated_score else None
        ),
        "analysis_functional_score": analysis_score["earned"],
        "analysis_functional_possible": analysis_score["possible"],
        "acceptance": finalized.grades.acceptance.status,
        "diff": finalized.grades.diff.status,
        "changed_paths": list(final_workspace.changed_paths),
        "source_plan_model_tokens": source_execution["usage"]["model_tokens"],
        "incremental_build_model_tokens": build_execution["usage"]["model_tokens"],
        "counterfactual_total_model_tokens": usage["model_tokens"],
        "source_plan_wall_ms": source_execution["wall_ms"],
        "incremental_build_wall_ms": build_execution["wall_ms"],
        "counterfactual_total_wall_ms": sum(
            int(value["wall_ms"]) for value in executions
        ),
        "tool_calls": usage["tool_calls"],
        "cost_usd": amount_usd,
        "raw_trace_bytes": trace_bytes,
        "provider_turns": diagnoses,
    }


def main() -> int:
    os.environ["TRACELANE_ROOT"] = str(ROOT)
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    day2_import.ARTIFACT_ROOT = ARTIFACT_ROOT
    recoveries = [_import_recovery(spec) for spec in recovery.recovery_matrix()]
    replays = [_import_replay(spec) for spec in recovery.replay_matrix()]
    result = {
        "schema_version": "coding-eval-day2-recovery/v0.1",
        "experiment": "TraceLane x OpenCode Day 2 recovery and gate replay",
        "provider": "ark",
        "claim_scope": (
            "Three separate descriptive layers: the original preregistered 36 remain "
            "unchanged; infrastructure-failed recovery pairs are not capability evidence; "
            "gate replays diagnose corrected-gate build outcomes. No significance claim."
        ),
        "layers": {
            "original_preregistered_36": {
                "results": "../day2-coding-eval/results.json",
                "mutated": False,
            },
            "quota_recovery_complete_pairs": recoveries,
            "corrected_gate_build_replays": replays,
        },
    }
    RESULTS.write_text(canonical_json(result) + "\n", encoding="utf-8")
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
