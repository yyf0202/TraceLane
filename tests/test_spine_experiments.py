from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tracelane.contracts import EvidenceRecord, TaskSpec
from tracelane.decision_orchestrator import AnalystSpec
from tracelane.spine import Resolution
from tracelane.spine.experiments import (
    DecisionTaskSpec,
    ablate_debate,
    ablate_feedback_loop,
)


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def make_decision_task(task_id: str, actual: str, *, bullish_conf: float = 0.9) -> DecisionTaskSpec:
    evidence = (
        EvidenceRecord(
            evidence_id=f"{task_id}_ev1",
            available_at=_ts(1),
            source="news",
            text=f"{task_id} positive signal.",
            fact_ids=("f1",),
        ),
        EvidenceRecord(
            evidence_id=f"{task_id}_ev2",
            available_at=_ts(2),
            source="filing",
            text=f"{task_id} risk note.",
            fact_ids=("f2",),
        ),
    )
    task = TaskSpec(
        task_id=task_id,
        question=f"Research {task_id}.",
        cutoff_at=_ts(3),
        expected_facts={"f1": "pos", "f2": "risk"},
        completion_facts=("f1",),
        evidence=evidence,
        future_evidence_ids=(),
        fault_scenario=None,
        license="test",
    )
    analysts = (
        AnalystSpec("fund", "fundamentals-analyst", "bullish", bullish_conf),
        AnalystSpec("risk", "risk-analyst", "bearish", 0.6),
    )
    resolution = Resolution(
        subject=task_id,
        actual_direction=actual,  # type: ignore[arg-type]
        metric_name="net_alpha",
        metric_value=0.02 if actual == "bullish" else -0.02,
    )
    return DecisionTaskSpec(task=task, analysts=analysts, resolution=resolution)


def suite() -> tuple[DecisionTaskSpec, ...]:
    return (
        make_decision_task("T1", "bullish"),
        make_decision_task("T2", "bullish"),
        make_decision_task("T3", "bearish"),
        make_decision_task("T4", "bullish"),
    )


def test_ablate_debate_produces_per_arm_metrics(tmp_path: Path) -> None:
    experiment_root, summary = ablate_debate(suite(), tmp_path)
    assert set(summary["arms"]) == {"debate_on", "debate_off"}
    for arm in ("debate_on", "debate_off"):
        assert 0.0 <= summary["arms"][arm]["accuracy"] <= 1.0
        assert summary["arms"][arm]["task_count"] == 4
    # Debate adds a model call per task in the on-arm.
    assert (
        summary["arms"]["debate_on"]["mean_model_calls"]
        > summary["arms"]["debate_off"]["mean_model_calls"]
    )
    assert summary["arms"]["debate_on"]["debate_rate"] == 1.0
    assert summary["arms"]["debate_off"]["debate_rate"] == 0.0
    assert (experiment_root / "summary.json").exists()


def test_ablate_debate_is_deterministic(tmp_path: Path) -> None:
    _, first = ablate_debate(suite(), tmp_path / "a")
    _, second = ablate_debate(suite(), tmp_path / "b")
    assert first["arms"]["debate_on"]["accuracy"] == second["arms"]["debate_on"]["accuracy"]
    assert first["deltas"] == second["deltas"]


def test_ablate_feedback_loop_runs_both_arms() -> None:
    result = ablate_feedback_loop(suite(), rounds=3, min_samples=2)
    assert set(result["arms"]) == {"static", "self_improving"}
    assert len(result["arms"]["static"]["accuracy_per_round"]) == 3
    assert len(result["arms"]["self_improving"]["accuracy_per_round"]) == 3
    # Static arm never changes its aggregation, so its accuracy is flat.
    static_acc = result["arms"]["static"]["accuracy_per_round"]
    assert static_acc == [static_acc[0]] * 3


def test_feedback_loop_learns_reliability_weights() -> None:
    # One analyst is always right, the other always wrong; the self-improving
    # arm should down-weight the wrong analyst over rounds.
    tasks = (
        DecisionTaskSpec(
            task=make_decision_task("X1", "bullish").task,
            analysts=(
                AnalystSpec("good", "good-analyst", "bullish", 0.9),
                AnalystSpec("bad", "bad-analyst", "bearish", 0.85),
            ),
            resolution=Resolution(
                subject="X1", actual_direction="bullish", metric_name="r", metric_value=0.02
            ),
        ),
        DecisionTaskSpec(
            task=make_decision_task("X2", "bullish").task,
            analysts=(
                AnalystSpec("good", "good-analyst", "bullish", 0.9),
                AnalystSpec("bad", "bad-analyst", "bearish", 0.85),
            ),
            resolution=Resolution(
                subject="X2", actual_direction="bullish", metric_name="r", metric_value=0.02
            ),
        ),
        DecisionTaskSpec(
            task=make_decision_task("X3", "bullish").task,
            analysts=(
                AnalystSpec("good", "good-analyst", "bullish", 0.9),
                AnalystSpec("bad", "bad-analyst", "bearish", 0.85),
            ),
            resolution=Resolution(
                subject="X3", actual_direction="bullish", metric_name="r", metric_value=0.02
            ),
        ),
    )
    result = ablate_feedback_loop(tasks, rounds=3, min_samples=2)
    reliability = result["arms"]["self_improving"]["final_reliability"]
    # The always-right analyst earns higher reliability than the always-wrong one.
    assert reliability.get("good", 0) > reliability.get("bad", 0)
    # Self-improving should be at least as accurate as static by the final round.
    assert (
        result["arms"]["self_improving"]["accuracy_per_round"][-1]
        >= result["arms"]["static"]["accuracy_per_round"][-1]
    )


def test_feedback_loop_is_deterministic() -> None:
    first = ablate_feedback_loop(suite(), rounds=2, min_samples=2)
    second = ablate_feedback_loop(suite(), rounds=2, min_samples=2)
    assert first == second
