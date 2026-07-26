"""Typed contracts for the decision → outcome → feedback spine.

The research loop may produce prose; the spine does not.  It consumes and
emits only these typed, fully deterministic records so that every run yields
an auditable, replayable chain: analysts emit :class:`TypedSignal` values that
must cite retained evidence or explicitly abstain, a deterministic fusion and
finalize step commits a :class:`DecisionRecord`, the world resolves that
decision into an :class:`OutcomeRecord` of facts (never LLM reflections), and
an attribution step writes a :class:`FeedbackRecord`.

These contracts are domain-agnostic.  A particular domain (for example an
A-share research agent) supplies its own evidence, signal vocabulary, and
outcome metric, but the admissibility rules are enforced here:

* a non-abstaining signal must cite at least one evidence id and carry a
  directional view with ``confidence > 0``;
* an abstaining signal must say why and cannot carry a direction;
* every record id is derived from its canonical content, never from wall
  clock or randomness, so identical inputs reproduce identical records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from tracelane.contracts import sha256_json

SignalDirection = Literal["bullish", "bearish", "neutral", "abstain"]
_DIRECTIONS = ("bullish", "bearish", "neutral", "abstain")

OutcomeStatus = Literal["observed", "invalid"]

_ID_PREFIX = re.compile(r"^(sig|dec|out|fb)_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _bounded(value: float, label: str, *, low: float = 0.0, high: float = 1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    numeric = float(value)
    if not (low <= numeric <= high):
        raise ValueError(f"{label} must be within [{low}, {high}]")
    return numeric


def _unique_strings(value: tuple[str, ...], label: str, *, required: bool = False) -> tuple[str, ...]:
    items = tuple(value)
    if required and not items:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(set(items)) != len(items):
        raise ValueError(f"{label} must contain unique strings")
    return items


def _record_id(prefix: str, content: object) -> str:
    return f"{prefix}_{sha256_json(content)[:32]}"


@dataclass(frozen=True)
class TypedSignal:
    """The only analyst artifact eligible for deterministic aggregation.

    A non-abstaining signal carries a directional view, bounded confidence and
    at least one evidence id; this rejects the failure mode where fluent LLM
    prose is mistaken for verified signal.  An abstaining signal must carry an
    ``abstain_reason`` and no direction.
    """

    signal_id: str
    analyst_id: str
    subject: str
    direction: SignalDirection
    confidence: float
    evidence_ids: tuple[str, ...]
    abstained: bool
    abstain_reason: str | None
    model_id: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "analyst_id", _non_empty(self.analyst_id, "analyst_id"))
        object.__setattr__(self, "subject", _non_empty(self.subject, "subject"))
        if self.direction not in _DIRECTIONS:
            raise ValueError("direction is invalid")
        object.__setattr__(self, "confidence", _bounded(self.confidence, "confidence"))
        object.__setattr__(
            self, "evidence_ids", _unique_strings(self.evidence_ids, "evidence_ids")
        )
        if self.model_id is not None:
            object.__setattr__(self, "model_id", _non_empty(self.model_id, "model_id"))
        if self.abstained:
            if self.direction != "abstain":
                raise ValueError("an abstained signal must use direction='abstain'")
            object.__setattr__(
                self, "abstain_reason", _non_empty(self.abstain_reason, "abstain_reason")
            )
        else:
            if self.direction == "abstain":
                raise ValueError("a non-abstaining signal requires a directional view")
            if not self.evidence_ids:
                raise ValueError("a non-abstaining signal requires evidence_ids")
            if self.confidence <= 0:
                raise ValueError("a non-abstaining signal requires confidence > 0")
            if self.abstain_reason is not None:
                raise ValueError("a non-abstaining signal cannot carry abstain_reason")

    def content_dict(self) -> dict[str, object]:
        return {
            "abstain_reason": self.abstain_reason,
            "abstained": self.abstained,
            "analyst_id": self.analyst_id,
            "confidence": self.confidence,
            "direction": self.direction,
            "evidence_ids": list(self.evidence_ids),
            "model_id": self.model_id,
            "subject": self.subject,
        }

    def to_dict(self) -> dict[str, object]:
        return {"signal_id": self.signal_id, **self.content_dict()}

    @classmethod
    def create(
        cls,
        *,
        analyst_id: str,
        subject: str,
        direction: SignalDirection,
        confidence: float,
        evidence_ids: tuple[str, ...] = (),
        abstained: bool = False,
        abstain_reason: str | None = None,
        model_id: str | None = None,
    ) -> TypedSignal:
        content = {
            "abstain_reason": abstain_reason,
            "abstained": abstained,
            "analyst_id": analyst_id,
            "confidence": confidence,
            "direction": direction,
            "evidence_ids": list(evidence_ids),
            "model_id": model_id,
            "subject": subject,
        }
        return cls(
            signal_id=_record_id("sig", content),
            analyst_id=analyst_id,
            subject=subject,
            direction=direction,
            confidence=confidence,
            evidence_ids=tuple(evidence_ids),
            abstained=abstained,
            abstain_reason=abstain_reason,
            model_id=model_id,
        )


@dataclass(frozen=True)
class DecisionRecord:
    """Immutable committed decision for one run.

    The record references the signals and evidence it rests on by id (those
    records already appear earlier in the ledger), plus the deterministic
    fusion output, so the finalize step stays auditable without duplicating
    the analyst payloads.
    """

    decision_id: str
    subject: str
    final_decision: str
    fusion: object
    signal_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    abstained_analysts: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _non_empty(self.subject, "subject"))
        object.__setattr__(self, "final_decision", _non_empty(self.final_decision, "final_decision"))
        object.__setattr__(self, "signal_ids", _unique_strings(self.signal_ids, "signal_ids"))
        object.__setattr__(
            self, "evidence_ids", _unique_strings(self.evidence_ids, "evidence_ids")
        )
        object.__setattr__(
            self,
            "abstained_analysts",
            _unique_strings(self.abstained_analysts, "abstained_analysts"),
        )

    def content_dict(self) -> dict[str, object]:
        return {
            "abstained_analysts": list(self.abstained_analysts),
            "evidence_ids": list(self.evidence_ids),
            "final_decision": self.final_decision,
            "fusion": self.fusion,
            "signal_ids": list(self.signal_ids),
            "subject": self.subject,
        }

    def to_dict(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, **self.content_dict()}

    @classmethod
    def create(
        cls,
        *,
        subject: str,
        final_decision: str,
        fusion: object,
        signal_ids: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        abstained_analysts: tuple[str, ...] = (),
    ) -> DecisionRecord:
        content = {
            "abstained_analysts": list(abstained_analysts),
            "evidence_ids": list(evidence_ids),
            "final_decision": final_decision,
            "fusion": fusion,
            "signal_ids": list(signal_ids),
            "subject": subject,
        }
        return cls(
            decision_id=_record_id("dec", content),
            subject=subject,
            final_decision=final_decision,
            fusion=fusion,
            signal_ids=tuple(signal_ids),
            evidence_ids=tuple(evidence_ids),
            abstained_analysts=tuple(abstained_analysts),
        )


@dataclass(frozen=True)
class OutcomeRecord:
    """Observed world result of a decision; values are facts, not reflections.

    ``metric_name``/``metric_value`` carry the domain-specific resolution
    (for example a cost-adjusted alpha) without coupling the spine to any
    particular domain vocabulary.
    """

    outcome_id: str
    decision_id: str
    subject: str
    status: OutcomeStatus
    metric_name: str | None
    metric_value: float | None
    invalid_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _non_empty(self.decision_id, "decision_id"))
        object.__setattr__(self, "subject", _non_empty(self.subject, "subject"))
        if self.status not in ("observed", "invalid"):
            raise ValueError("status is invalid")
        if self.status == "observed":
            object.__setattr__(self, "metric_name", _non_empty(self.metric_name, "metric_name"))
            if isinstance(self.metric_value, bool) or not isinstance(
                self.metric_value, (int, float)
            ):
                raise ValueError("an observed outcome requires a numeric metric_value")
            object.__setattr__(self, "metric_value", float(self.metric_value))
            if self.invalid_reason is not None:
                raise ValueError("an observed outcome cannot carry invalid_reason")
        else:
            object.__setattr__(
                self, "invalid_reason", _non_empty(self.invalid_reason, "invalid_reason")
            )
            if self.metric_name is not None or self.metric_value is not None:
                raise ValueError("an invalid outcome cannot carry a metric")

    def content_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "invalid_reason": self.invalid_reason,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "status": self.status,
            "subject": self.subject,
        }

    def to_dict(self) -> dict[str, object]:
        return {"outcome_id": self.outcome_id, **self.content_dict()}

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        subject: str,
        status: OutcomeStatus,
        metric_name: str | None = None,
        metric_value: float | None = None,
        invalid_reason: str | None = None,
    ) -> OutcomeRecord:
        content = {
            "decision_id": decision_id,
            "invalid_reason": invalid_reason,
            "metric_name": metric_name,
            "metric_value": metric_value,
            "status": status,
            "subject": subject,
        }
        return cls(
            outcome_id=_record_id("out", content),
            decision_id=decision_id,
            subject=subject,
            status=status,
            metric_name=metric_name,
            metric_value=metric_value,
            invalid_reason=invalid_reason,
        )


@dataclass(frozen=True)
class FeedbackRecord:
    """Deterministic attribution from a decision/outcome pair.

    ``per_signal_verdicts`` maps each contributing signal id to a short verdict
    label (for example ``"correct"``, ``"incorrect"``, ``"abstained"``), and
    ``failure_modes`` records the classified failure categories so later study
    can turn noisy agent failures into verifiable hypotheses.
    """

    feedback_id: str
    decision_id: str
    outcome_id: str
    decision_correct: bool | None
    per_signal_verdicts: object
    failure_modes: tuple[str, ...]
    data_quality_gaps: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _non_empty(self.decision_id, "decision_id"))
        object.__setattr__(self, "outcome_id", _non_empty(self.outcome_id, "outcome_id"))
        if self.decision_correct is not None and not isinstance(self.decision_correct, bool):
            raise ValueError("decision_correct must be a boolean or None")
        if not isinstance(self.per_signal_verdicts, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in self.per_signal_verdicts.items()
        ):
            raise ValueError("per_signal_verdicts must map signal ids to verdict strings")
        object.__setattr__(
            self, "failure_modes", _unique_strings(self.failure_modes, "failure_modes")
        )
        object.__setattr__(
            self,
            "data_quality_gaps",
            _unique_strings(self.data_quality_gaps, "data_quality_gaps"),
        )

    def content_dict(self) -> dict[str, object]:
        return {
            "data_quality_gaps": list(self.data_quality_gaps),
            "decision_correct": self.decision_correct,
            "decision_id": self.decision_id,
            "failure_modes": list(self.failure_modes),
            "outcome_id": self.outcome_id,
            "per_signal_verdicts": dict(self.per_signal_verdicts),
        }

    def to_dict(self) -> dict[str, object]:
        return {"feedback_id": self.feedback_id, **self.content_dict()}

    @classmethod
    def create(
        cls,
        *,
        decision_id: str,
        outcome_id: str,
        decision_correct: bool | None,
        per_signal_verdicts: object,
        failure_modes: tuple[str, ...] = (),
        data_quality_gaps: tuple[str, ...] = (),
    ) -> FeedbackRecord:
        content = {
            "data_quality_gaps": list(data_quality_gaps),
            "decision_correct": decision_correct,
            "decision_id": decision_id,
            "failure_modes": list(failure_modes),
            "outcome_id": outcome_id,
            "per_signal_verdicts": dict(per_signal_verdicts),
        }
        return cls(
            feedback_id=_record_id("fb", content),
            decision_id=decision_id,
            outcome_id=outcome_id,
            decision_correct=decision_correct,
            per_signal_verdicts=per_signal_verdicts,
            failure_modes=tuple(failure_modes),
            data_quality_gaps=tuple(data_quality_gaps),
        )


# Map ledger record kinds to their id field for chain verification.
RECORD_ID_FIELD = {
    "signal": "signal_id",
    "decision": "decision_id",
    "outcome": "outcome_id",
    "feedback": "feedback_id",
}


def validate_record_id(kind: str, record_id: str) -> str:
    """Validate a content-derived record id for a given ledger kind."""
    if kind not in RECORD_ID_FIELD:
        raise ValueError(f"unknown record kind: {kind}")
    if not isinstance(record_id, str) or not _ID_PREFIX.fullmatch(record_id):
        raise ValueError(f"{kind} id must match {_ID_PREFIX.pattern}")
    prefix = record_id.split("_", 1)[0]
    if prefix != {"signal": "sig", "decision": "dec", "outcome": "out", "feedback": "fb"}[kind]:
        raise ValueError(f"{kind} id has the wrong prefix")
    return record_id
