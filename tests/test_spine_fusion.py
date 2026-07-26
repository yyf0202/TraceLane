from __future__ import annotations

import pytest

from tracelane.spine import (
    TypedSignal,
    fuse_signals,
    should_debate,
)


def signal(
    analyst: str,
    direction: str,
    confidence: float,
    evidence: tuple[str, ...],
    *,
    abstained: bool = False,
) -> TypedSignal:
    return TypedSignal.create(
        analyst_id=analyst,
        subject="SUBJ",
        direction=direction,  # type: ignore[arg-type]
        confidence=confidence,
        evidence_ids=evidence,
        abstained=abstained,
        abstain_reason="no data" if abstained else None,
    )


def test_fuse_requires_signals() -> None:
    with pytest.raises(ValueError, match="at least one signal"):
        fuse_signals(())


def test_fuse_requires_shared_subject() -> None:
    a = signal("a", "bullish", 0.8, ("ev_1",))
    b = TypedSignal.create(
        analyst_id="b",
        subject="OTHER",
        direction="bullish",
        confidence=0.8,
        evidence_ids=("ev_1",),
    )
    with pytest.raises(ValueError, match="same subject"):
        fuse_signals((a, b))


def test_fuse_is_deterministic() -> None:
    signals = (
        signal("a", "bullish", 0.8, ("ev_1",)),
        signal("b", "bearish", 0.6, ("ev_2",)),
    )
    first = fuse_signals(signals)
    second = fuse_signals(tuple(reversed(signals)))
    # Order-independent in score/direction; eligible ids follow input order,
    # so compare the canonical stance fields.
    assert first.score == second.score
    assert first.direction == second.direction
    assert first.to_dict()["independence"] == second.to_dict()["independence"]


def test_abstained_and_evidence_free_signals_are_excluded_not_neutral() -> None:
    real = signal("real", "bullish", 0.9, ("ev_1",))
    abstained = signal("lazy", "abstain", 0.0, (), abstained=True)
    result = fuse_signals((real, abstained))
    assert abstained.signal_id in result.excluded_signal_ids
    assert "lazy" in result.abstained_analysts
    assert result.eligible_signal_ids == (real.signal_id,)
    # The abstention must not dilute the real bullish stance.
    assert result.direction == "bullish"
    assert result.score > 0


def test_all_abstained_yields_abstain_stance() -> None:
    result = fuse_signals(
        (
            signal("a", "abstain", 0.0, (), abstained=True),
            signal("b", "abstain", 0.0, (), abstained=True),
        )
    )
    assert result.direction == "abstain"
    assert result.eligible_signal_ids == ()
    assert result.reason is not None
    assert result.coverage == 0.0


def test_shared_evidence_is_not_double_counted() -> None:
    # Two analysts echoing the same evidence should not outweigh one
    # independent analyst as much as two independent ones would.
    shared = (
        signal("a", "bullish", 0.8, ("ev_1",)),
        signal("b", "bullish", 0.8, ("ev_1",)),
        signal("c", "bearish", 0.8, ("ev_2",)),
    )
    independent = (
        signal("a", "bullish", 0.8, ("ev_1",)),
        signal("b", "bullish", 0.8, ("ev_3",)),
        signal("c", "bearish", 0.8, ("ev_2",)),
    )
    shared_result = fuse_signals(shared)
    independent_result = fuse_signals(independent)
    assert shared_result.score < independent_result.score
    # Independence factor for the shared-evidence analysts is below one.
    assert shared_result.to_dict()["independence"]["a"] < 1.0


def test_strong_consensus_yields_direction_and_low_disagreement() -> None:
    result = fuse_signals(
        (
            signal("a", "bullish", 0.9, ("ev_1",)),
            signal("b", "bullish", 0.8, ("ev_2",)),
        )
    )
    assert result.direction == "bullish"
    assert result.disagreement < 0.2
    assert result.coverage == 1.0


def test_weak_score_yields_neutral() -> None:
    result = fuse_signals(
        (
            signal("a", "bullish", 0.55, ("ev_1",)),
            signal("b", "bearish", 0.5, ("ev_2",)),
        ),
        decision_threshold=0.2,
    )
    assert result.direction == "neutral"


def test_fusion_result_serializes_to_canonical_dict() -> None:
    result = fuse_signals((signal("a", "bullish", 0.9, ("ev_1",)),))
    as_dict = result.to_dict()
    assert as_dict["subject"] == "SUBJ"
    assert as_dict["policy_version"]
    assert isinstance(as_dict["independence"], dict)


# --- debate policy -------------------------------------------------------


def consensus_fusion():
    return fuse_signals(
        (
            signal("a", "bullish", 0.9, ("ev_1",)),
            signal("b", "bullish", 0.8, ("ev_2",)),
        )
    )


def test_debate_always_and_never() -> None:
    fusion = consensus_fusion()
    assert should_debate(fusion, policy="always").should_debate is True
    assert should_debate(fusion, policy="never").should_debate is False


def test_debate_conditional_skips_on_consensus() -> None:
    decision = should_debate(consensus_fusion(), policy="conditional")
    assert decision.should_debate is False
    assert decision.reason == "fusion_consensus"


def test_debate_conditional_triggers_on_abstain() -> None:
    fusion = fuse_signals((signal("a", "abstain", 0.0, (), abstained=True),))
    decision = should_debate(fusion, policy="conditional")
    assert decision.should_debate is True
    assert decision.reason == "fusion_abstained"


def test_debate_conditional_triggers_on_disagreement() -> None:
    fusion = fuse_signals(
        (
            signal("a", "bullish", 0.9, ("ev_1",)),
            signal("b", "bearish", 0.9, ("ev_2",)),
        )
    )
    decision = should_debate(fusion, policy="conditional", disagreement_threshold=0.5)
    assert fusion.disagreement >= 0.5
    assert decision.should_debate is True


def test_debate_conditional_triggers_on_low_coverage() -> None:
    fusion = fuse_signals(
        (
            signal("a", "bullish", 0.9, ("ev_1",)),
            signal("b", "abstain", 0.0, (), abstained=True),
            signal("c", "abstain", 0.0, (), abstained=True),
            signal("d", "abstain", 0.0, (), abstained=True),
        ),
        expected_analysts=4,
    )
    assert fusion.coverage == 0.25
    decision = should_debate(fusion, policy="conditional", coverage_threshold=0.5)
    assert decision.should_debate is True


def test_debate_rejects_invalid_policy() -> None:
    with pytest.raises(ValueError, match="policy"):
        should_debate(consensus_fusion(), policy="sometimes")  # type: ignore[arg-type]
