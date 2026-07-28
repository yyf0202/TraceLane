#!/usr/bin/env python3
"""Import the audited Day 3 matrix and its separate gate-replay layer."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import import_day2_coding_eval as base
import run_day3_coding_eval as experiment

from tracelane.adapters.opencode import diagnose_last_provider_turn, load_opencode_session
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
ARTIFACT_ROOT = ROOT / "artifacts/day3-coding-eval"
WORK_ROOT = experiment.experiment.WORK_ROOT
RESOLUTION_PATH = (
    ROOT / "fixtures/coding/bericher-v0.9/day3-analysis-resolution.json"
)
H0_ID = "opencode-h0-06d9803be9"
H0_MANIFEST = "e1e231b7bc730f8467528756d17ce12b099ae1cad8dc46735fe71bb970a3288a"
LATEST_GRADERS = {
    "BR-10": ("adjudicated-grader-v4.log", 4),
    "BR-11": ("adjudicated-grader-v6.log", 6),
    "BR-12": ("adjudicated-grader-v5.log", 5),
}
LATEST_GATES = {
    "BR-10": ("adjudicated-plan-gate-v2.log", 2),
    "BR-11": ("adjudicated-plan-gate-v4.log", 4),
    "BR-12": ("adjudicated-plan-gate-v6.log", 6),
}


@dataclass(frozen=True)
class Phase:
    raw_slug: str
    cli_name: str
    role: str
    terminal: bool


def _resolution() -> dict[str, Any]:
    return json.loads(RESOLUTION_PATH.read_text(encoding="utf-8"))


def _execution(raw: Path, cli_name: str) -> dict[str, Any]:
    path = raw / f"{cli_name}.execution.json"
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        harness = value.get("harness")
        if harness and (
            harness.get("harness_id") != H0_ID
            or harness.get("manifest_sha256") != H0_MANIFEST
        ):
            raise ValueError(f"{path} does not match frozen H0")
        return value

    cli = raw / cli_name
    rows = [json.loads(line) for line in cli.read_text(encoding="utf-8").splitlines()]
    finishes = [
        row["part"]
        for row in rows
        if isinstance(row.get("part"), dict)
        and row["part"].get("type") == "step-finish"
    ]
    usage = {
        "input_tokens": sum(int(row.get("tokens", {}).get("input", 0)) for row in finishes),
        "cached_input_tokens": sum(
            int(row.get("tokens", {}).get("cache", {}).get("read", 0))
            for row in finishes
        ),
        "output_tokens": sum(
            int(row.get("tokens", {}).get("output", 0)) for row in finishes
        ),
        "reasoning_tokens": sum(
            int(row.get("tokens", {}).get("reasoning", 0)) for row in finishes
        ),
        "model_tokens": sum(
            int(row.get("tokens", {}).get("total", 0)) for row in finishes
        ),
        "tool_calls": len(
            {
                row["part"].get("callID")
                for row in rows
                if isinstance(row.get("part"), dict)
                and row["part"].get("type") == "tool"
                and row["part"].get("callID")
            }
        ),
    }
    termination_path = raw / f"{cli_name}.termination.json"
    termination_text = (
        termination_path.read_text(encoding="utf-8")
        if termination_path.exists()
        else ""
    )
    termination = json.loads(termination_text) if termination_text.strip() else None
    if termination and isinstance(termination.get("usage_at_termination"), dict):
        usage = termination["usage_at_termination"]
    session_path = next(raw.glob("ses_*.jsonl"))
    observations = [
        json.loads(line)
        for line in session_path.read_text(encoding="utf-8").splitlines()
    ]
    times = [
        datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
        for row in observations
        if isinstance(row.get("observed_at"), str)
    ]
    wall_ms = (
        int(termination["wall_ms"])
        if termination and termination.get("wall_ms") is not None
        else int((max(times) - min(times)).total_seconds() * 1000)
        if times
        else 0
    )
    return {
        "schema_version": "opencode-budget-execution/v0.1",
        "reason": "operator_interrupted",
        "exit_code": None,
        "usage": usage,
        "wall_ms": wall_ms,
        "harness": None,
        "reconstructed_from_preserved_trace": True,
    }


def _partial_cli(raw: Path, workflow: str) -> str:
    candidates = (
        ("cli.jsonl",)
        if workflow == "direct-build"
        else ("build-cli.jsonl", "plan-cli.jsonl")
    )
    return next(name for name in candidates if (raw / name).exists())


def _terminal_phases(raw_slug: str, workflow: str) -> list[Phase]:
    raw = RAW_ROOT / raw_slug
    if workflow == "direct-build":
        return [Phase(raw_slug, "cli.jsonl", "build", True)]
    phases = []
    for name, role in (("plan-cli.jsonl", "plan"), ("build-cli.jsonl", "build")):
        if (raw / name).exists():
            phases.append(Phase(raw_slug, name, role, True))
    if not phases:
        raise ValueError(f"{raw_slug} has no terminal phase")
    return phases


def _primary_phases(
    spec: experiment.experiment.AttemptSpec,
    resolution: dict[str, Any],
) -> tuple[str, list[Phase]]:
    attempt_id = spec.run_slug
    terminal = resolution["primary_evidence_overrides"].get(attempt_id, attempt_id)
    split = resolution["split_phase_attempts"].get(attempt_id)
    if split:
        return terminal, [
            Phase(split["plan_source"], "plan-cli.jsonl", "plan", True),
            Phase(
                split["interrupted_build_source"],
                "build-cli.jsonl",
                "build",
                False,
            ),
            Phase(split["terminal_build_source"], "build-cli.jsonl", "build", True),
        ]
    partials = [
        Phase(
            slug,
            _partial_cli(RAW_ROOT / slug, spec.workflow),
            "build" if spec.workflow == "direct-build" else "plan",
            False,
        )
        for slug in resolution["charged_partial_sources"].get(attempt_id, [])
    ]
    terminal_phases = _terminal_phases(terminal, spec.workflow)
    if spec.workflow == "plan-build":
        plan = [phase for phase in terminal_phases if phase.role == "plan"]
        build = [phase for phase in terminal_phases if phase.role == "build"]
        return terminal, plan + partials + build
    return terminal, partials + terminal_phases


def _gate_replay_phases(
    source_spec: experiment.experiment.AttemptSpec,
    replay_slug: str,
    resolution: dict[str, Any],
) -> tuple[str, list[Phase]]:
    source = resolution["primary_evidence_overrides"].get(
        source_spec.run_slug, source_spec.run_slug
    )
    return replay_slug, [
        Phase(source, "plan-cli.jsonl", "plan", True),
        Phase(replay_slug, "build-cli.jsonl", "build", True),
    ]


def _latest_score(raw: Path, task: str) -> dict[str, Any]:
    name, version = LATEST_GRADERS[task]
    score = base._prefixed_score(raw / name, "TRACELANE_SCORE=")
    if score is None:
        raise ValueError(f"{raw.name} has no final {task} adjudication")
    return {**score, "grader": name, "grader_version": version}


def _latest_gate(raw: Path, task: str) -> dict[str, Any] | None:
    name, version = LATEST_GATES[task]
    score = base._prefixed_score(raw / name, "TRACELANE_PLAN_SCORE=")
    return {**score, "gate": name, "gate_version": version} if score else None


def _provider_diagnosis(raw: Path, cli_name: str, session: object) -> dict[str, Any]:
    termination_path = raw / f"{cli_name}.termination.json"
    termination_text = (
        termination_path.read_text(encoding="utf-8")
        if termination_path.exists()
        else ""
    )
    termination = json.loads(termination_text) if termination_text.strip() else None
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


def _import_attempt(
    spec: experiment.experiment.AttemptSpec,
    *,
    attempt_id: str,
    terminal_slug: str,
    phases: list[Phase],
    resolution: dict[str, Any],
    layer: str,
) -> dict[str, Any]:
    task = load_coding_task(json.loads(spec.task.manifest.read_text(encoding="utf-8")))
    terminal_raw = RAW_ROOT / terminal_slug
    worktree = WORK_ROOT / f"bericher-{terminal_slug}"
    workflow_end = json.loads(
        (terminal_raw / "workflow-end.json").read_text(encoding="utf-8")
    )
    final_workspace = capture_workspace(worktree, task.baseline.commit_sha)
    if final_workspace.head_commit != task.baseline.commit_sha:
        raise ValueError(f"{terminal_slug} baseline moved")

    sessions: list[AttemptSession] = []
    executions: list[dict[str, Any]] = []
    diagnoses: list[dict[str, Any]] = []
    cli_rows: list[dict[str, Any]] = []
    root_id: str | None = None
    for phase in phases:
        raw = RAW_ROOT / phase.raw_slug
        row = base._cli(raw / phase.cli_name)
        session_id = str(row["session_id"])
        root_id = root_id or session_id
        loaded = load_opencode_session(raw / f"{session_id}.jsonl")
        sessions.append(
            AttemptSession(
                SessionRef(
                    session_id,
                    root_id,
                    None if session_id == root_id else root_id,
                    phase.role,
                ),
                loaded,
            )
        )
        execution = _execution(raw, phase.cli_name)
        executions.append(execution)
        cli_rows.append(row)
        diagnoses.append(
            {
                "raw_attempt": phase.raw_slug,
                "cli_file": phase.cli_name,
                "role": phase.role,
                "terminal": phase.terminal,
                **_provider_diagnosis(raw, phase.cli_name, loaded),
            }
        )

    usage = {
        key: sum(int(value["usage"].get(key, 0)) for value in executions)
        for key in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "model_tokens",
            "tool_calls",
        )
    }
    wall_ms = sum(int(value.get("wall_ms", 0)) for value in executions)
    amount_usd = round(sum(float(row["amount_usd"]) for row in cli_rows), 8)
    plan_path = terminal_raw / "handoff/plan.json"
    plan_artifact = (
        load_plan_artifact(json.loads(plan_path.read_text(encoding="utf-8")))
        if plan_path.exists()
        else None
    )
    frozen_score = base._prefixed_score(
        terminal_raw / "independent-grader.log", "TRACELANE_SCORE="
    )
    if frozen_score is None:
        raise ValueError(f"{terminal_slug} has no independent score")
    score = _latest_score(terminal_raw, spec.task.short_id)
    frozen_gate = base._prefixed_score(
        terminal_raw / "plan-gate.log", "TRACELANE_PLAN_SCORE="
    )
    gate = _latest_gate(terminal_raw, spec.task.short_id)
    build_started = bool(workflow_end.get("build_started"))
    final_index = max(
        index for index, phase in enumerate(phases) if phase.terminal
    )
    final_execution = executions[final_index]
    if workflow_end.get("reason") in {"plan_gate_failed", "plan_modified_workspace"}:
        end_reason = "blocked"
    elif str(final_execution["reason"]).endswith("_budget_exhausted"):
        end_reason = "budget_exhausted"
    elif final_execution["reason"] == "completed":
        end_reason = "completed"
    else:
        end_reason = "crashed"
    final_answer = cli_rows[final_index]["final_answer"]
    budget_issue = resolution["budget_integrity_exclusions"].get(spec.run_slug)
    terminal_diagnoses = [
        value for value in diagnoses if value["terminal"]
    ]
    provider_valid = all(
        value.get("local_termination")
        or value["state"] in {"completed", "model_completed_processor_incomplete"}
        for value in terminal_diagnoses
    )
    capability_eligible = provider_valid and budget_issue is None and layer == "primary"
    harness = next(
        (
            value["harness"]
            for value in reversed(executions)
            if isinstance(value.get("harness"), dict)
        ),
        None,
    )

    finalized = finalize_coding_attempt(
        task,
        attempt_id=attempt_id,
        sessions=tuple(sessions),
        initial_workspace=base._clean_snapshot(task.baseline.commit_sha),
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
            "phase_link": "manual-cli-split" if len(sessions) > 1 else None,
            "build_started": build_started,
            "evidence_layer": layer,
            "evidence_attempts": list(dict.fromkeys(p.raw_slug for p in phases)),
            "budget_integrity": "excluded" if budget_issue else "valid",
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
                    "raw_attempt": phase.raw_slug,
                    "cli_file": phase.cli_name,
                    **execution,
                }
                for phase, execution in zip(phases, executions, strict=True)
            ],
        },
        repeat=spec.repeat,
    )
    finalized.store.write_json("output/independent-functional-score.json", frozen_score)
    finalized.store.write_json("output/adjudicated-functional-score.json", score)
    finalized.store.write_json(
        "output/provider-turn-diagnoses.json", {"phases": diagnoses}
    )
    finalized.store.write_json("output/workflow-end.json", workflow_end)
    finalized.store.write_json(
        "output/evidence-resolution.json",
        {
            "canonical_attempt": spec.run_slug,
            "evidence_attempt": terminal_slug,
            "layer": layer,
            "phases": [
                {
                    "raw_attempt": phase.raw_slug,
                    "cli_file": phase.cli_name,
                    "role": phase.role,
                }
                for phase in phases
            ],
            "budget_integrity_exclusion": budget_issue,
        },
    )
    if frozen_gate is not None:
        finalized.store.write_json("output/plan-gate-score.json", frozen_gate)
    if gate is not None:
        finalized.store.write_json("output/adjudicated-plan-gate-score.json", gate)
    trace_bytes = sum(
        (RAW_ROOT / phase.raw_slug / f"{row['session_id']}.jsonl").stat().st_size
        for phase, row in zip(phases, cli_rows, strict=True)
    )
    return {
        "attempt_id": attempt_id,
        "canonical_attempt_id": spec.run_slug,
        "evidence_attempt_id": terminal_slug,
        "evidence_layer": layer,
        "task": spec.task.short_id,
        "task_version": task.version,
        "model": spec.model,
        "workflow": spec.workflow,
        "repeat": spec.repeat,
        "run_id": finalized.store.run_id,
        "end_reason": end_reason,
        "execution_reason": final_execution["reason"],
        "build_started": build_started,
        "plan_score": frozen_gate["earned"] if frozen_gate else None,
        "adjudicated_plan_score": gate["earned"] if gate else None,
        "analysis_plan_score": gate["earned"] if gate else None,
        "functional_score": frozen_score["earned"],
        "functional_possible": frozen_score["possible"],
        "adjudicated_functional_score": score["earned"],
        "analysis_functional_score": score["earned"],
        "analysis_functional_possible": score["possible"],
        "capability_analysis_eligible": capability_eligible,
        "provider_transport_valid": provider_valid,
        "budget_integrity_valid": budget_issue is None,
        "budget_integrity_exclusion": budget_issue,
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
    resolution = _resolution()
    specs = {row.run_slug: row for row in experiment.matrix()}
    attempts = []
    for spec in experiment.matrix():
        print(f"IMPORT {spec.run_slug}", flush=True)
        terminal, phases = _primary_phases(spec, resolution)
        attempts.append(
            _import_attempt(
                spec,
                attempt_id=spec.run_slug,
                terminal_slug=terminal,
                phases=phases,
                resolution=resolution,
                layer="primary",
            )
        )
    gate_replays = []
    for row in resolution["gate_replays"]:
        print(f"IMPORT {row['attempt_id']}", flush=True)
        source_spec = specs[row["source_attempt"]]
        terminal, phases = _gate_replay_phases(
            source_spec, row["attempt_id"], resolution
        )
        gate_replays.append(
            _import_attempt(
                source_spec,
                attempt_id=row["attempt_id"],
                terminal_slug=terminal,
                phases=phases,
                resolution=resolution,
                layer="gate-replay",
            )
        )
    result = {
        "schema_version": "coding-eval-day3/v0.2",
        "experiment": "TraceLane x OpenCode Day 3 cross-task matrix",
        "provider": "ark",
        "models": list(experiment.MODELS),
        "harness": H0_ID,
        "automatic_attempt_retries": 0,
        "claim_scope": (
            "Strictly serial paired descriptive evidence across three BeRicher "
            "tasks and three models. Gate replays are separate, provider failures "
            "remain reliability evidence, and budget-integrity exclusions are not "
            "used in paired capability comparisons."
        ),
        "attempts": attempts,
        "layers": {"gate_replay": gate_replays},
        "audit": {
            "resolution_manifest": str(RESOLUTION_PATH.relative_to(ROOT)),
            "frozen_slots": len(attempts),
            "gate_replays": len(gate_replays),
            "budget_integrity_exclusions": len(
                resolution["budget_integrity_exclusions"]
            ),
            "provider_failures": len(resolution["provider_failures"]),
        },
    }
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    (ARTIFACT_ROOT / "results.json").write_text(
        canonical_json(result) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "attempts": len(attempts),
                "gate_replays": len(gate_replays),
                "output": str(ARTIFACT_ROOT),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
