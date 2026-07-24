from __future__ import annotations

from copy import deepcopy

from tests.test_contracts import ANSWER, TASK
from tracelane.contracts import AgentAnswer, Claim, load_answer, load_task, sha256_json
from tracelane.evidence import freeze_evidence
from tracelane.validation import validate_answer


def validation_inputs():
    task = load_task(deepcopy(TASK))
    bundle = freeze_evidence(task, task.evidence)
    return task, bundle


def test_valid_answer_passes_all_contract_checks() -> None:
    task, bundle = validation_inputs()
    answer = load_answer(deepcopy(ANSWER))
    report = validate_answer(
        answer,
        task,
        bundle,
        expected_sha256=sha256_json(answer),
    )
    assert report.valid
    assert report.schema_valid
    assert report.errors == ()


def test_future_evidence_is_rejected_even_when_model_saw_raw_context() -> None:
    value = deepcopy(TASK)
    value["evidence"].append(
        {
            "evidence_id": "ev-future",
            "available_at": "2026-01-10T00:00:01Z",
            "source": "synthetic-field-note",
            "text": "Future habitat claim.",
            "fact_ids": ["fact-habitat"],
        }
    )
    value["future_evidence_ids"] = ["ev-future"]
    task = load_task(value)
    bundle = freeze_evidence(task, task.evidence)
    answer = AgentAnswer(
        answer="Future habitat claim.",
        claims=(
            Claim(
                text="Future habitat claim.",
                evidence_ids=("ev-future",),
                fact_ids=("fact-habitat",),
            ),
        ),
        missing_information=(),
    )
    report = validate_answer(answer, task, bundle)
    assert not report.valid
    assert "future evidence" in " ".join(report.errors)


def test_unknown_fact_and_unsupported_fact_are_rejected() -> None:
    task, bundle = validation_inputs()
    unknown = AgentAnswer(
        answer="Unknown.",
        claims=(
            Claim(
                text="Unknown.",
                evidence_ids=("ev-001",),
                fact_ids=("fact-unknown",),
            ),
        ),
        missing_information=(),
    )
    report = validate_answer(unknown, task, bundle)
    assert not report.valid
    assert any("unknown fact" in error for error in report.errors)


def test_published_hash_mismatch_is_rejected() -> None:
    task, bundle = validation_inputs()
    report = validate_answer(
        load_answer(deepcopy(ANSWER)),
        task,
        bundle,
        expected_sha256="0" * 64,
    )
    assert not report.valid
    assert any("hash" in error for error in report.errors)


def test_schema_invalid_mapping_returns_report_instead_of_raising() -> None:
    task, bundle = validation_inputs()
    report = validate_answer({"answer": "", "claims": []}, task, bundle)
    assert not report.valid
    assert not report.schema_valid
    assert report.errors
