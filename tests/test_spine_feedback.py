from __future__ import annotations

import pytest

from tracelane.spine import (
    DecisionRecord,
    Resolution,
    TypedSignal,
    attribute_feedback,
    fuse_signals,
    propose_reliability_updates,
    resolve_decision,
)


def signal(analyst: str, direction: str, confidence: float, evidence: tuple[str, ...]) -> TypedSignal:
    return TypedSignal.create(
        analyst_id=analyst,
        subject="SUBJ",
        direction=direction,  # type: ignore[arg-type]
        confidence=confidence,
        evidence_ids=evidence,
    )


def decided(signals: tuple[TypedSignal, ...], final: str = "buy"):
    fusion = fuse_signals(signals)
    decision = DecisionRecord.create(
        subject="SUBJ",
        final_decision=final,
        fusion=fusion.to_dict(),
        signal_ids=tuple(s.signal_id for s in signals),
        evidence_ids=tuple(sorted({e for s in signals for e in s.evidence_ids})),
        abstained_analysts=fusion.abstained_analysts,
    )
    return decision, fusion


def test_resolve_requires_matching_subject() -> None:
    signals = (signal("a", "bullish", 0.9, ("ev_1",)),)
    decision, fusion = decided(signals)
    bad = Resolution(subject="OTHER", actual_direction="bullish", metric_name="r", metric_value=0.1)
    with pytest.raises(ValueError, match="does not belong"):
        resolve_decision(decision, fusion, bad)


def test_resolve_observed_produces_factual_outcome() -> None:
    signals = (signal("a", "bullish", 0.9, ("ev_1",)),)
    decision, fusion = decided(signals)
    resolution = Resolution(subject="SUBJ", actual_direction="bullish", metric_name="net_alpha", metric_value=0.03)
    outcome = resolve_decision(decision, fusion, resolution)
    assert outcome.status == "observed"
    assert outcome.metric_name == "net_alpha"
    assert outcome.metric_value == 0.03
    assert outcome.decision_id == decision.decision_id


def test_resolve_unresolvable_produces_invalid_outcome() -> None:
    signals = (signal("a", "bullish", 0.9, ("ev_1",)),)
    decision, fusion = decided(signals)
    resolution = Resolution(subject="SUBJ", actual_direction=None, invalid_reason="delisted")
    outcome = resolve_decision(decision, fusion, resolution)
    assert outcome.status == "invalid"
    assert outcome.invalid_reason == "delisted"


def test_feedback_marks_correct_direction() -> None:
    signals = (
        signal("a", "bullish", 0.9, ("ev_1",)),
        signal("b", "bullish", 0.8, ("ev_2",)),
    )
    decision, fusion = decided(signals)
    resolution = Resolution(subject="SUBJ", actual_direction="bullish", metric_name="net_alpha", metric_value=0.02)
    outcome = resolve_decision(decision, fusion, resolution)
    feedback = attribute_feedback(decision, fusion, signals, outcome, resolution)
    assert feedback.decision_correct is True
    assert all(verdict == "correct" for verdict in feedback.per_signal_verdicts.values())
    assert feedback.failure_modes == ()


def test_feedback_attributes_signal_level_blame() -> None:
    signals = (
        signal("a", "bullish", 0.9, ("ev_1",)),
        signal("b", "bearish", 0.8, ("ev_2",)),
    )
    decision, fusion = decided(signals)
    # World goes up: the bullish analyst is right, the bearish one wrong.
    resolution = Resolution(subject="SUBJ", actual_direction="bullish", metric_name="net_alpha", metric_value=0.02)
    outcome = resolve_decision(decision, fusion, resolution)
    feedback = attribute_feedback(decision, fusion, signals, outcome, resolution)
    by_analyst = {s.analyst_id: s.signal_id for s in signals}
    assert feedback.per_signal_verdicts[by_analyst["a"]] == "correct"
    assert feedback.per_signal_verdicts[by_analyst["b"]] == "incorrect"


def test_feedback_flags_direction_miss_failure_mode() -> None:
    signals = (signal("a", "bullish", 0.95, ("ev_1",)),)
    decision, fusion = decided(signals)
    resolution = Resolution(subject="SUBJ", actual_direction="bearish", metric_name="net_alpha", metric_value=-0.04)
    outcome = resolve_decision(decision, fusion, resolution)
    feedback = attribute_feedback(decision, fusion, signals, outcome, resolution)
    assert feedback.decision_correct is False
    assert "direction_miss" in feedback.failure_modes


def test_feedback_on_unresolved_outcome_is_neutral() -> None:
    signals = (signal("a", "bullish", 0.9, ("ev_1",)),)
    decision, fusion = decided(signals)
    resolution = Resolution(subject="SUBJ", actual_direction=None, invalid_reason="no data")
    outcome = resolve_decision(decision, fusion, resolution)
    feedback = attribute_feedback(decision, fusion, signals, outcome, resolution)
    assert feedback.decision_correct is None
    assert "unresolved" in feedback.failure_modes
    assert "outcome could not be resolved" in feedback.data_quality_gaps


def test_feedback_requires_matching_decision() -> None:
    signals = (signal("a", "bullish", 0.9, ("ev_1",)),)
    decision, fusion = decided(signals)
    other_signals = (signal("a", "bearish", 0.9, ("ev_9",)),)
    other_decision, _ = decided(other_signals, final="sell")
    resolution = Resolution(subject="SUBJ", actual_direction="bullish", metric_name="r", metric_value=0.01)
    outcome = resolve_decision(other_decision, fusion, resolution)
    with pytest.raises(ValueError, match="does not belong"):
        attribute_feedback(decision, fusion, signals, outcome, resolution)


def test_reliability_requires_min_samples() -> None:
    signals = tuple(signal("a", "bullish", 0.9, (f"ev_{i}",)) for i in range(5))
    attributions = tuple((s, True) for s in signals)
    proposals = propose_reliability_updates(attributions, min_samples=30)
    assert len(proposals) == 1
    assert proposals[0].status == "insufficient_sample"
    assert proposals[0].candidate_value is None
    assert proposals[0].sample_size == 5


def test_reliability_shrinks_toward_prior() -> None:
    # 40/40 correct would be 1.0 without a prior; shrinkage pulls it below.
    attributions = tuple(
        (signal("a", "bullish", 0.9, (f"ev_{i}",)), True) for i in range(40)
    )
    proposals = propose_reliability_updates(attributions, min_samples=30, prior_strength=20)
    assert proposals[0].status == "requires_walk_forward"
    assert proposals[0].candidate_value is not None
    assert 0.5 < proposals[0].candidate_value < 1.0


def test_reliability_skips_abstained_signals() -> None:
    abstained = TypedSignal.create(
        analyst_id="lazy", subject="SUBJ", direction="abstain", confidence=0.0,
        abstained=True, abstain_reason="no data",
    )
    proposals = propose_reliability_updates(((abstained, True),), min_samples=1)
    assert proposals == []


def test_reliability_is_deterministic() -> None:
    attributions = tuple(
        (signal("a", "bullish", 0.9, (f"ev_{i}",)), i % 3 != 0) for i in range(35)
    )
    first = propose_reliability_updates(attributions, min_samples=30)
    second = propose_reliability_updates(attributions, min_samples=30)
    assert [p.to_dict() for p in first] == [p.to_dict() for p in second]
