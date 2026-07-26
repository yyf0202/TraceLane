"""Deterministic outcome resolution and feedback attribution.

This module closes the loop.  A committed :class:`DecisionRecord` stays a
*claim about the world* until the world resolves it.  ``resolve_decision``
turns a point-in-time-true :class:`Resolution` (what actually became
available at the horizon) into a factual :class:`OutcomeRecord` — never an LLM
reflection.  ``attribute_feedback`` then attributes that outcome back to the
contributing signals as a deterministic :class:`FeedbackRecord`.

The self-improvement hook is :func:`propose_reliability_updates`: it
accumulates resolved outcomes and emits *reviewable candidates* for analyst
reliability weights, never an instruction to mutate live config.  A single
good or bad result cannot retune the system — proposals require a minimum
sample and shrink toward a prior so a few lucky calls do not dominate.

All functions here are pure and deterministic; wall-clock and randomness are
injected by the caller through the records themselves.
"""

from __future__ import annotations

from dataclasses import dataclass

from tracelane.spine.contracts import (
    DecisionRecord,
    FeedbackRecord,
    OutcomeRecord,
    SignalDirection,
    TypedSignal,
)
from tracelane.spine.fusion import FusionResult

_DIRECTION_VALUE: dict[str, float] = {
    "bullish": 1.0,
    "bearish": -1.0,
    "neutral": 0.0,
}

DEFAULT_CORRECT_THRESHOLD = 0.0
DEFAULT_MIN_SAMPLES = 30
DEFAULT_PRIOR_STRENGTH = 20


@dataclass(frozen=True)
class Resolution:
    """The point-in-time-true world result for one decided subject.

    ``actual_direction`` is the direction the world actually took (for example
    derived from realized return against a deadband); ``metric_name`` /
    ``metric_value`` carry the domain-specific resolution number.  ``None``
    fields mark the subject as unresolvable (for example delisted or missing
    data), which yields an ``invalid`` outcome rather than a silent drop.
    """

    subject: str
    actual_direction: SignalDirection | None
    metric_name: str | None = None
    metric_value: float | None = None
    invalid_reason: str | None = None


def stance_from_score(score: float, threshold: float = 0.0) -> SignalDirection:
    """Map a fused score to a discrete stance using an explicit deadband."""
    if score > threshold:
        return "bullish"
    if score < -threshold:
        return "bearish"
    return "neutral"


def resolve_decision(
    decision: DecisionRecord,
    fusion: FusionResult,
    resolution: Resolution,
    *,
    correct_threshold: float = DEFAULT_CORRECT_THRESHOLD,
) -> OutcomeRecord:
    """Resolve one committed decision into a factual outcome.

    The resolution must describe the same subject as the decision.  A
    resolution with no usable result (``actual_direction is None``) produces an
    ``invalid`` outcome carrying the reason, so unresolved decisions are
    visible rather than silently dropped.
    """
    if resolution.subject != decision.subject:
        raise ValueError("resolution does not belong to this decision")
    if resolution.actual_direction is None:
        reason = resolution.invalid_reason or "no resolution available"
        return OutcomeRecord.create(
            decision_id=decision.decision_id,
            subject=decision.subject,
            status="invalid",
            invalid_reason=reason,
        )
    metric_name = resolution.metric_name or "score"
    metric_value = (
        float(resolution.metric_value)
        if resolution.metric_value is not None
        else _DIRECTION_VALUE[resolution.actual_direction]
    )
    return OutcomeRecord.create(
        decision_id=decision.decision_id,
        subject=decision.subject,
        status="observed",
        metric_name=metric_name,
        metric_value=metric_value,
    )


def _verdict(
    signal_direction: SignalDirection,
    outcome_direction: SignalDirection,
) -> str:
    if signal_direction == "neutral":
        return "neutral"
    return "correct" if signal_direction == outcome_direction else "incorrect"


def attribute_feedback(
    decision: DecisionRecord,
    fusion: FusionResult,
    signals: tuple[TypedSignal, ...] | list[TypedSignal],
    outcome: OutcomeRecord,
    resolution: Resolution,
    *,
    correct_threshold: float = DEFAULT_CORRECT_THRESHOLD,
) -> FeedbackRecord:
    """Attribute one resolved outcome back to its contributing signals.

    ``decision_correct`` judges the committed stance against the actual world
    direction using an explicit deadband (``correct_threshold``).  Per-signal
    verdicts compare each non-abstaining signal's direction to the actual
    direction, so attribution stays at the signal level rather than blaming
    the whole graph.  Missing or invalid resolutions leave ``decision_correct``
    as ``None``.
    """
    if outcome.decision_id != decision.decision_id:
        raise ValueError("outcome does not belong to this decision")
    signals = tuple(signals)
    signal_directions = {
        signal.signal_id: signal.direction
        for signal in signals
        if not signal.abstained and signal.direction != "abstain"
    }

    decision_direction = stance_from_score(fusion.score, correct_threshold)

    per_signal: dict[str, str] = {}
    if outcome.status == "observed" and resolution.actual_direction is not None:
        actual = resolution.actual_direction
        decision_correct: bool | None = decision_direction == actual
        for signal_id, direction in signal_directions.items():
            per_signal[signal_id] = _verdict(direction, actual)
        failure_modes: tuple[str, ...] = ()
        if decision_direction != "neutral" and not decision_correct:
            failure_modes = ("direction_miss",)
        elif fusion.coverage < 1.0:
            failure_modes = ("partial_coverage",)
        else:
            failure_modes = ()
    else:
        decision_correct = None
        failure_modes = ("unresolved",)

    data_quality_gaps: list[str] = []
    if not signal_directions:
        data_quality_gaps.append("no analyst signal had evidence references")
    if fusion.abstained_analysts:
        data_quality_gaps.append(f"{len(fusion.abstained_analysts)} analyst(s) abstained")
    if outcome.status == "invalid":
        data_quality_gaps.append("outcome could not be resolved")

    return FeedbackRecord.create(
        decision_id=decision.decision_id,
        outcome_id=outcome.outcome_id,
        decision_correct=decision_correct,
        per_signal_verdicts=per_signal,
        failure_modes=failure_modes,
        data_quality_gaps=tuple(data_quality_gaps),
    )


@dataclass(frozen=True)
class ReliabilityProposal:
    """A reviewable candidate reliability weight; never a live-config change."""

    analyst_id: str
    candidate_value: float | None
    sample_size: int
    status: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "analyst_id": self.analyst_id,
            "candidate_value": self.candidate_value,
            "reason": self.reason,
            "sample_size": self.sample_size,
            "status": self.status,
        }


def propose_reliability_updates(
    attributions: tuple[tuple[TypedSignal, bool], ...] | list[tuple[TypedSignal, bool]],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    prior_strength: int = DEFAULT_PRIOR_STRENGTH,
) -> list[ReliabilityProposal]:
    """Propose shrunk analyst-reliability weights after a sufficient sample.

    Each item in ``attributions`` pairs a non-abstaining signal with whether
    its direction matched the resolved world direction.  Proposals shrink the
    observed accuracy toward a 50% prior (Beta-style) so a few lucky outcomes
    cannot dominate fusion, and require at least ``min_samples`` independent
    resolved outcomes before a candidate value is emitted.  Every proposal is
    marked ``requires_walk_forward`` or ``insufficient_sample`` — it cannot
    alter a current or future run until a separate frozen validation accepts
    the candidate.
    """
    if min_samples < 1:
        raise ValueError("min_samples must be a positive integer")
    if prior_strength < 0:
        raise ValueError("prior_strength must be non-negative")

    calls: dict[str, list[bool]] = {}
    for signal, correct in attributions:
        if signal.abstained or signal.direction == "abstain":
            continue
        calls.setdefault(signal.analyst_id, []).append(bool(correct))

    def shrunk(wins: int, n: int) -> float:
        return (wins + prior_strength * 0.5) / (n + prior_strength)

    proposals: list[ReliabilityProposal] = []
    for analyst_id in sorted(calls):
        outcomes_for_analyst = calls[analyst_id]
        n = len(outcomes_for_analyst)
        if n < min_samples:
            proposals.append(
                ReliabilityProposal(
                    analyst_id=analyst_id,
                    candidate_value=None,
                    sample_size=n,
                    status="insufficient_sample",
                    reason=f"requires at least {min_samples} independent resolved outcomes",
                )
            )
            continue
        wins = sum(1 for correct in outcomes_for_analyst if correct)
        proposals.append(
            ReliabilityProposal(
                analyst_id=analyst_id,
                candidate_value=round(shrunk(wins, n), 6),
                sample_size=n,
                status="requires_walk_forward",
                reason=(
                    "shrunk directional reliability; candidate only until frozen validation passes"
                ),
            )
        )
    return proposals
