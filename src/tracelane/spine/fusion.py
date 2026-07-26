"""Deterministic fusion of admissible analyst signals; never parses LLM prose.

The fusion layer is a pure, seed-free function over :class:`TypedSignal`
values.  It is the one place where multiple analyst views become a single
bounded stance, so it must be reproducible: identical signals always fuse to
an identical :class:`FusionResult`.

Admissibility rules enforced here:

* abstained signals, and signals without evidence, are *excluded* rather than
  coerced to neutral, so data gaps cannot falsely dilute a real view;
* analysts citing the same evidence do not get to multiply-count it — each
  signal's weight is scaled by an independence factor derived from the Jaccard
  overlap of its evidence set against the other eligible signals, so one
  announcement echoed by several roles counts closer to once;
* ``disagreement`` and ``coverage`` are exposed so a downstream gate (or a
  debate policy) can decide whether the fused stance is trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracelane.spine.contracts import SignalDirection, TypedSignal

FUSION_POLICY_VERSION = "fusion-v1"

_DIRECTION_VALUE: dict[str, float] = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
}


def _mean_jaccard_overlap(target: set[str], others: list[set[str]]) -> float:
    """Mean Jaccard overlap of one signal's evidence set against the others."""
    if not others or not target:
        return 0.0
    total = 0.0
    for other in others:
        union = target | other
        total += (len(target & other) / len(union)) if union else 0.0
    return total / len(others)


@dataclass(frozen=True)
class FusionResult:
    """The bounded deterministic stance produced by fusing analyst signals."""

    subject: str
    direction: SignalDirection
    score: float
    confidence: float
    eligible_signal_ids: tuple[str, ...]
    excluded_signal_ids: tuple[str, ...]
    abstained_analysts: tuple[str, ...]
    disagreement: float
    coverage: float
    independence: tuple[tuple[str, float], ...]
    reason: str | None = None
    policy_version: str = FUSION_POLICY_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "abstained_analysts": list(self.abstained_analysts),
            "confidence": self.confidence,
            "coverage": self.coverage,
            "direction": self.direction,
            "disagreement": self.disagreement,
            "eligible_signal_ids": list(self.eligible_signal_ids),
            "excluded_signal_ids": list(self.excluded_signal_ids),
            "independence": dict(self.independence),
            "policy_version": self.policy_version,
            "reason": self.reason,
            "score": self.score,
            "subject": self.subject,
        }


def fuse_signals(
    signals: tuple[TypedSignal, ...] | list[TypedSignal],
    *,
    decision_threshold: float = 0.20,
    expected_analysts: int | None = None,
    independence_lambda: float = 1.0,
) -> FusionResult:
    """Fuse typed views into one bounded deterministic stance.

    The function is pure and deterministic.  All signals must share the same
    ``subject`` (the spine's domain-agnostic analogue of an instrument + as-of
    pair), otherwise fusion would blend incompatible views.
    """
    signals = tuple(signals)
    if not signals:
        raise ValueError("fuse_signals requires at least one signal")
    subject = signals[0].subject
    if any(signal.subject != subject for signal in signals):
        raise ValueError("all fused signals must share the same subject")
    if not (0.0 <= decision_threshold <= 1.0):
        raise ValueError("decision_threshold must be within [0, 1]")
    if independence_lambda < 0:
        raise ValueError("independence_lambda must be non-negative")

    eligible: list[TypedSignal] = []
    excluded: list[str] = []
    abstained: list[str] = []
    for signal in signals:
        if signal.abstained or not signal.evidence_ids or signal.direction == "abstain":
            excluded.append(signal.signal_id)
            if signal.abstained:
                abstained.append(signal.analyst_id)
            continue
        eligible.append(signal)

    expected = max(1, int(expected_analysts) if expected_analysts else len(signals))
    coverage = round(len(eligible) / expected, 6)

    if not eligible:
        return FusionResult(
            subject=subject,
            direction="abstain",
            score=0.0,
            confidence=0.0,
            eligible_signal_ids=(),
            excluded_signal_ids=tuple(excluded),
            abstained_analysts=tuple(abstained),
            disagreement=0.0,
            coverage=coverage,
            independence=(),
            reason="no evidence-bound non-abstaining analyst signals",
        )

    evidence_sets = {signal.signal_id: set(signal.evidence_ids) for signal in eligible}
    independence: dict[str, float] = {}
    numerator = 0.0
    denominator = 0.0
    for signal in eligible:
        others = [
            evidence_sets[other.signal_id]
            for other in eligible
            if other.signal_id != signal.signal_id
        ]
        overlap = _mean_jaccard_overlap(evidence_sets[signal.signal_id], others)
        indep = 1.0 / (1.0 + independence_lambda * overlap)
        independence[signal.analyst_id] = round(indep, 6)
        weight = signal.confidence * indep
        numerator += weight * _DIRECTION_VALUE[signal.direction]
        denominator += weight

    score = max(-1.0, min(1.0, numerator / denominator))
    disagreement = round(1.0 - abs(score), 6)
    direction: SignalDirection = (
        "bullish"
        if score >= decision_threshold
        else "bearish"
        if score <= -decision_threshold
        else "neutral"
    )
    raw_weight_sum = sum(independence[signal.analyst_id] for signal in eligible)
    return FusionResult(
        subject=subject,
        direction=direction,
        score=round(score, 6),
        confidence=round(denominator / raw_weight_sum, 6) if raw_weight_sum else 0.0,
        eligible_signal_ids=tuple(signal.signal_id for signal in eligible),
        excluded_signal_ids=tuple(excluded),
        abstained_analysts=tuple(abstained),
        disagreement=disagreement,
        coverage=coverage,
        independence=tuple(sorted(independence.items())),
    )
