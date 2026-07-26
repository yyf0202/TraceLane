"""Ablations over the decision chain: debate and the feedback loop.

These experiments turn the spine into measurable evidence.  Each one runs the
decision orchestrator across a deterministic suite of decision tasks under two
or more arms and reports per-arm metrics, so a reviewer can see whether a
harness mechanism is actually load-bearing:

* ``ablate_debate`` — debate on (``always``) vs off (``never``), measuring
  decision accuracy, cost (model calls / tokens) and how often debate fires.
* ``ablate_feedback_loop`` — a static fusion policy vs a self-improving one
  that feeds shrinkage-reliability proposals (derived from resolved feedback)
  back into the next round's fusion.  This is the smallest runnable
  demonstration of harness–model co-evolution: the harness changes its own
  aggregation as outcomes accumulate.

Everything is deterministic; identical suites and seeds reproduce identical
metrics.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from tracelane.artifacts import RunIdentity, RunStore
from tracelane.contracts import (
    FrozenBundle,
    HarnessConfig,
    TaskSpec,
    canonical_json,
    sha256_json,
)
from tracelane.decision_orchestrator import AnalystSpec, DecisionOrchestrator
from tracelane.runtime.stub import DeterministicStubRuntime
from tracelane.spine import Resolution, propose_reliability_updates
from tracelane.spine.contracts import TypedSignal
from tracelane.spine.debate import DebatePolicy
from tracelane.spine.feedback import stance_from_score  # deterministic stance label
from tracelane.spine.fusion import fuse_signals


@dataclass(frozen=True)
class DecisionTaskSpec:
    """A decision task plus its deterministic analyst roster and resolution."""

    task: TaskSpec
    analysts: tuple[AnalystSpec, ...]
    resolution: Resolution


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (canonical_json(value) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _freeze(task: TaskSpec) -> FrozenBundle:
    records = tuple(sorted(task.evidence, key=lambda r: (r.available_at, r.evidence_id)))
    return FrozenBundle(
        task_id=task.task_id,
        cutoff_at=task.cutoff_at,
        records=records,
        rejected_future_ids=tuple(task.future_evidence_ids),
        bundle_sha256=sha256_json({"records": [r.evidence_id for r in records]}),
    )


def _run_decision(
    spec: DecisionTaskSpec,
    config: HarnessConfig,
    root: Path,
    *,
    debate_policy: DebatePolicy,
) -> dict[str, object]:
    task, bundle = spec.task, _freeze(spec.task)
    identity = RunIdentity(
        task_sha256=sha256_json(task),
        bundle_sha256=bundle.bundle_sha256,
        config_sha256=sha256_json({"config": config, "debate_policy": debate_policy}),
        model_id=DeterministicStubRuntime.model_id,
        repeat=1,
    )
    store = RunStore.create(root, identity.run_id)
    runtime = DeterministicStubRuntime()
    orchestrator = DecisionOrchestrator(identity)
    result = orchestrator.run(
        task,
        bundle,
        config,
        runtime,
        store,
        analysts=spec.analysts,
        resolution=spec.resolution,
        debate_policy=debate_policy,
    )
    decision_direction = result.fusion.direction
    correct = (
        spec.resolution.actual_direction is not None
        and decision_direction == spec.resolution.actual_direction
    )
    # Operational cost is read back from the append-only trace, which is the
    # authoritative record of model activity for the run.
    model_calls = 0
    total_tokens = 0
    trace_path = store.path_for("trace/events.jsonl")
    if trace_path.exists():
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("event_type") == "model.completed":
                model_calls += 1
                payload = row.get("payload", {})
                total_tokens += int(payload.get("input_tokens", 0)) + int(
                    payload.get("output_tokens", 0)
                )
    return {
        "task_id": task.task_id,
        "correct": correct,
        "decision_direction": decision_direction,
        "actual_direction": spec.resolution.actual_direction,
        "debated": result.debated,
        "model_calls": model_calls,
        "total_tokens": total_tokens,
        "fusion_score": result.fusion.score,
        "coverage": result.fusion.coverage,
    }


def _summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    count = len(rows)
    resolved = [row for row in rows if row["actual_direction"] is not None]
    correct = sum(1 for row in resolved if row["correct"])
    return {
        "task_count": count,
        "resolved_count": len(resolved),
        "accuracy": (correct / len(resolved)) if resolved else 0.0,
        "debate_rate": (sum(1 for row in rows if row["debated"]) / count) if count else 0.0,
        "mean_model_calls": (sum(int(r["model_calls"]) for r in rows) / count) if count else 0.0,
        "mean_total_tokens": (sum(int(r["total_tokens"]) for r in rows) / count) if count else 0.0,
        "tasks": rows,
    }


def ablate_debate(
    specs: tuple[DecisionTaskSpec, ...],
    artifacts_root: str | Path,
    *,
    seed: int = 7,
) -> tuple[Path, dict[str, object]]:
    """Compare debate on (``always``) vs off (``never``) over a decision suite."""
    root = Path(artifacts_root).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    config = HarnessConfig(seed=seed)
    arms: dict[str, DebatePolicy] = {"debate_on": "always", "debate_off": "never"}
    suite_sha256 = sha256_json([spec.task for spec in specs])
    experiment_id = sha256_json(
        {"variable": "debate_policy", "suite_sha256": suite_sha256, "seed": seed}
    )[:16]
    experiment_root = root / "experiments" / f"debate-{experiment_id}"
    experiment_root.mkdir(parents=True, exist_ok=True)

    summaries = {}
    for arm, policy in arms.items():
        rows = [
            _run_decision(spec, config, experiment_root / arm, debate_policy=policy)
            for spec in specs
        ]
        summaries[arm] = _summarize(rows)

    summary = {
        "experiment_id": experiment_id,
        "variable": "debate_policy",
        "seed": seed,
        "arms": summaries,
        "deltas": {
            "accuracy": summaries["debate_on"]["accuracy"] - summaries["debate_off"]["accuracy"],
            "mean_model_calls": (
                summaries["debate_on"]["mean_model_calls"]
                - summaries["debate_off"]["mean_model_calls"]
            ),
            "mean_total_tokens": (
                summaries["debate_on"]["mean_total_tokens"]
                - summaries["debate_off"]["mean_total_tokens"]
            ),
        },
    }
    _write_json(experiment_root / "summary.json", summary)
    return experiment_root, summary


def _fusion_with_reliability(
    signals: tuple[TypedSignal, ...],
    reliability: dict[str, float],
    *,
    expected_analysts: int,
):
    """Re-fuse signals with each analyst's confidence scaled by reliability.

    The reliability weights come from shrinkage proposals over past resolved
    feedback — they are the harness's learned aggregation, not a model change.
    """
    adjusted = tuple(
        TypedSignal.create(
            analyst_id=signal.analyst_id,
            subject=signal.subject,
            direction=signal.direction,
            confidence=signal.confidence * reliability.get(signal.analyst_id, 1.0)
            if not signal.abstained
            else signal.confidence,
            evidence_ids=signal.evidence_ids,
            abstained=signal.abstained,
            abstain_reason=signal.abstain_reason,
            model_id=signal.model_id,
        )
        for signal in signals
    )
    return fuse_signals(adjusted, expected_analysts=expected_analysts)


def ablate_feedback_loop(
    specs: tuple[DecisionTaskSpec, ...],
    *,
    rounds: int = 3,
    min_samples: int = 2,
) -> dict[str, object]:
    """Demonstrate self-improvement: reliability proposals feed the next round.

    Each round runs every task once.  In the ``self_improving`` arm, the
    shrinkage-reliability proposals computed from all resolved feedback so far
    scale each analyst's confidence in the next round's fusion; in the
    ``static`` arm fusion always uses unit weights.  Accuracy per round is
    reported so the trajectory (not just the endpoint) is visible.
    """
    if rounds < 1:
        raise ValueError("rounds must be a positive integer")

    def run_round(arm: str, reliability: dict[str, float]) -> tuple[float, list]:
        attributions = []
        correct = 0
        resolved = 0
        for spec in specs:
            bundle = _freeze(spec.task)
            # Deterministic per-task signals from the analyst roster.
            signals = tuple(
                TypedSignal.create(
                    analyst_id=a.analyst_id,
                    subject=spec.task.task_id,
                    direction=("abstain" if a.abstains else a.direction_hint),  # type: ignore[arg-type]
                    confidence=(0.0 if a.abstains else a.confidence_hint),
                    evidence_ids=(() if a.abstains else tuple(r.evidence_id for r in bundle.records)),
                    abstained=a.abstains,
                    abstain_reason="no data" if a.abstains else None,
                    model_id=DeterministicStubRuntime.model_id,
                )
                for a in spec.analysts
            )
            if arm == "self_improving":
                fusion = _fusion_with_reliability(
                    signals, reliability, expected_analysts=len(spec.analysts)
                )
            else:
                fusion = fuse_signals(signals, expected_analysts=len(spec.analysts))
            actual = spec.resolution.actual_direction
            if actual is None:
                continue
            resolved += 1
            stance = fusion.direction
            if stance == actual:
                correct += 1
            for signal in signals:
                if not signal.abstained and signal.direction != "abstain":
                    attributions.append((signal, signal.direction == actual))
        accuracy = (correct / resolved) if resolved else 0.0
        return accuracy, attributions

    result: dict[str, object] = {"rounds": rounds, "arms": {}}
    for arm in ("static", "self_improving"):
        reliability: dict[str, float] = {}
        all_attributions: list = []
        per_round: list[float] = []
        for _ in range(rounds):
            accuracy, attributions = run_round(arm, reliability)
            per_round.append(accuracy)
            all_attributions.extend(attributions)
            if arm == "self_improving":
                proposals = propose_reliability_updates(
                    all_attributions, min_samples=min_samples
                )
                reliability = {
                    proposal.analyst_id: proposal.candidate_value
                    for proposal in proposals
                    if proposal.candidate_value is not None
                }
        result["arms"][arm] = {
            "accuracy_per_round": per_round,
            "final_reliability": reliability,
        }
    result["delta_final_round"] = (
        result["arms"]["self_improving"]["accuracy_per_round"][-1]
        - result["arms"]["static"]["accuracy_per_round"][-1]
    )
    return result
