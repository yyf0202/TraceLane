from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracelane.artifacts import RunStore
from tracelane.spine import (
    DecisionRecord,
    FeedbackRecord,
    Ledger,
    OutcomeRecord,
    TypedSignal,
)
from tracelane.spine.ledger import LEDGER_NAME


def make_store(tmp_path: Path) -> RunStore:
    return RunStore.create(tmp_path, "a" * 64)


def bullish_signal(**overrides: object) -> TypedSignal:
    kwargs: dict[str, object] = {
        "analyst_id": "fundamentals",
        "subject": "SUBJ",
        "direction": "bullish",
        "confidence": 0.7,
        "evidence_ids": ("ev_1",),
        "model_id": "model-x",
    }
    kwargs.update(overrides)
    return TypedSignal.create(**kwargs)  # type: ignore[arg-type]


def append_signal(ledger: Ledger, **overrides: object) -> TypedSignal:
    signal = bullish_signal(**overrides)
    ledger.append("signal", signal)
    return signal


def test_signal_ids_are_content_derived_and_deterministic() -> None:
    first = bullish_signal()
    second = bullish_signal()
    assert first.signal_id == second.signal_id
    assert first.signal_id.startswith("sig_")
    different = bullish_signal(confidence=0.8)
    assert different.signal_id != first.signal_id


def test_non_abstaining_signal_requires_evidence() -> None:
    with pytest.raises(ValueError, match="evidence_ids"):
        bullish_signal(evidence_ids=())


def test_non_abstaining_signal_requires_positive_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        bullish_signal(confidence=0.0)


def test_abstaining_signal_requires_reason_and_no_direction() -> None:
    with pytest.raises(ValueError, match="abstain_reason"):
        TypedSignal.create(
            analyst_id="news",
            subject="SUBJ",
            direction="abstain",
            confidence=0.0,
            abstained=True,
        )
    with pytest.raises(ValueError, match="direction='abstain'"):
        TypedSignal.create(
            analyst_id="news",
            subject="SUBJ",
            direction="bullish",
            confidence=0.0,
            abstained=True,
            abstain_reason="no data",
        )
    ok = TypedSignal.create(
        analyst_id="news",
        subject="SUBJ",
        direction="abstain",
        confidence=0.0,
        abstained=True,
        abstain_reason="no data",
    )
    assert ok.abstained


def test_outcome_observed_requires_metric(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="metric_value"):
        OutcomeRecord.create(
            decision_id="dec_" + "0" * 32,
            subject="SUBJ",
            status="observed",
            metric_name="net_alpha",
        )


def test_outcome_invalid_requires_reason_and_no_metric() -> None:
    with pytest.raises(ValueError, match="invalid_reason"):
        OutcomeRecord.create(decision_id="dec_" + "0" * 32, subject="SUBJ", status="invalid")
    with pytest.raises(ValueError, match="cannot carry a metric"):
        OutcomeRecord.create(
            decision_id="dec_" + "0" * 32,
            subject="SUBJ",
            status="invalid",
            invalid_reason="no resolution",
            metric_name="net_alpha",
            metric_value=0.01,
        )


def _full_chain(
    ledger: Ledger,
) -> tuple[TypedSignal, DecisionRecord, OutcomeRecord, FeedbackRecord]:
    signal = append_signal(ledger)
    decision = DecisionRecord.create(
        subject="SUBJ",
        final_decision="buy",
        fusion={"score": 0.7},
        signal_ids=(signal.signal_id,),
        evidence_ids=("ev_1",),
    )
    ledger.append("decision", decision)
    outcome = OutcomeRecord.create(
        decision_id=decision.decision_id,
        subject="SUBJ",
        status="observed",
        metric_name="net_alpha",
        metric_value=0.02,
    )
    ledger.append("outcome", outcome)
    feedback = FeedbackRecord.create(
        decision_id=decision.decision_id,
        outcome_id=outcome.outcome_id,
        decision_correct=True,
        per_signal_verdicts={signal.signal_id: "correct"},
        failure_modes=("none",),
    )
    ledger.append("feedback", feedback)
    return signal, decision, outcome, feedback


def test_ledger_appends_and_chains(tmp_path: Path) -> None:
    ledger = Ledger(make_store(tmp_path))
    ledger.entries()
    _full_chain(ledger)
    entries = ledger.entries()
    assert [entry.kind for entry in entries] == ["signal", "decision", "outcome", "feedback"]
    assert entries[0].previous_sha256 is None
    for previous, current in zip(entries, entries[1:], strict=False):
        assert current.previous_sha256 == previous.entry_sha256
    assert [entry.sequence for entry in entries] == [1, 2, 3, 4]


def test_ledger_records_round_trip(tmp_path: Path) -> None:
    ledger = Ledger(make_store(tmp_path))
    signal, decision, outcome, feedback = _full_chain(ledger)
    assert ledger.records("signal")[0]["signal_id"] == signal.signal_id
    assert ledger.records("decision")[0]["decision_id"] == decision.decision_id
    assert ledger.records("outcome")[0]["outcome_id"] == outcome.outcome_id
    assert ledger.records("feedback")[0]["feedback_id"] == feedback.feedback_id


def test_ledger_rejects_unknown_record_kind(tmp_path: Path) -> None:
    ledger = Ledger(make_store(tmp_path))
    with pytest.raises(ValueError, match="unknown record kind"):
        ledger.append("prediction", bullish_signal())


def test_decision_references_must_exist(tmp_path: Path) -> None:
    ledger = Ledger(make_store(tmp_path))
    decision = DecisionRecord.create(
        subject="SUBJ",
        final_decision="buy",
        fusion={},
        signal_ids=("sig_" + "f" * 32,),
        evidence_ids=("ev_1",),
    )
    with pytest.raises(ValueError, match="unknown signals"):
        ledger.append("decision", decision)


def test_outcome_references_must_exist(tmp_path: Path) -> None:
    ledger = Ledger(make_store(tmp_path))
    outcome = OutcomeRecord.create(
        decision_id="dec_" + "0" * 32,
        subject="SUBJ",
        status="observed",
        metric_name="net_alpha",
        metric_value=0.01,
    )
    with pytest.raises(ValueError, match="unknown decision"):
        ledger.append("outcome", outcome)


def test_feedback_references_must_exist(tmp_path: Path) -> None:
    ledger = Ledger(make_store(tmp_path))
    signal = append_signal(ledger)
    decision = DecisionRecord.create(
        subject="SUBJ",
        final_decision="buy",
        fusion={},
        signal_ids=(signal.signal_id,),
        evidence_ids=("ev_1",),
    )
    ledger.append("decision", decision)
    feedback = FeedbackRecord.create(
        decision_id=decision.decision_id,
        outcome_id="out_" + "0" * 32,
        decision_correct=True,
        per_signal_verdicts={signal.signal_id: "correct"},
    )
    with pytest.raises(ValueError, match="unknown outcome"):
        ledger.append("feedback", feedback)


def test_feedback_signal_verdicts_must_reference_journaled_signals(tmp_path: Path) -> None:
    ledger = Ledger(make_store(tmp_path))
    signal, decision, outcome, _ = _full_chain(ledger)
    bad = FeedbackRecord.create(
        decision_id=decision.decision_id,
        outcome_id=outcome.outcome_id,
        decision_correct=True,
        per_signal_verdicts={"sig_" + "9" * 32: "correct"},
    )
    with pytest.raises(ValueError, match="unknown signals"):
        ledger.append("feedback", bad)


def test_ledger_detects_tampering(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    ledger = Ledger(store)
    _full_chain(ledger)
    path = store.path_for(LEDGER_NAME)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[1]["record"]["final_decision"] = "sell"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        ledger.entries()
