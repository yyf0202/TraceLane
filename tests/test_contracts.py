from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from tracelane.contracts import (
    HarnessConfig,
    canonical_json,
    load_answer,
    load_task,
    parse_utc,
    sha256_json,
)

TASK = {
    "task_id": "summary-001",
    "question": "Which habitat is preferred?",
    "cutoff_at": "2026-01-10T00:00:00Z",
    "expected_facts": {"fact-habitat": "Wetland is preferred."},
    "completion_facts": ["fact-habitat"],
    "evidence": [
        {
            "evidence_id": "ev-001",
            "available_at": "2026-01-09T00:00:00Z",
            "source": "synthetic-field-note",
            "text": "Wetland is preferred.",
            "fact_ids": ["fact-habitat"],
        }
    ],
    "future_evidence_ids": [],
    "fault_scenario": None,
    "license": "CC0-1.0 synthetic",
}


ANSWER = {
    "answer": "Wetland is preferred.",
    "claims": [
        {
            "text": "Wetland is preferred.",
            "evidence_ids": ["ev-001"],
            "fact_ids": ["fact-habitat"],
        }
    ],
    "missing_information": [],
}


def test_answer_loader_accepts_documented_read_only_mapping() -> None:
    assert load_answer(MappingProxyType(deepcopy(ANSWER))).answer == "Wetland is preferred."


def test_canonical_hash_is_key_order_independent() -> None:
    left = {"b": 2, "a": 1}
    right = {"a": 1, "b": 2}
    assert canonical_json(left) == canonical_json(right)
    assert sha256_json(left) == sha256_json(right)


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="canonical JSON"):
        canonical_json({"score": float("nan")})


def test_parse_utc_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        parse_utc("2026-01-10T00:00:00")


def test_parse_utc_normalizes_offset_to_utc() -> None:
    assert parse_utc("2026-01-10T08:00:00+08:00") == datetime(
        2026,
        1,
        10,
        tzinfo=UTC,
    )


def test_task_loads_into_immutable_normalized_contract() -> None:
    task = load_task(TASK)
    assert task.task_id == "summary-001"
    assert task.cutoff_at == datetime(2026, 1, 10, tzinfo=UTC)
    assert task.evidence[0].fact_ids == ("fact-habitat",)
    with pytest.raises(TypeError):
        task.expected_facts["fact-extra"] = "Mutation is forbidden."


def test_task_rejects_duplicate_evidence_ids() -> None:
    value = deepcopy(TASK)
    value["evidence"].append(deepcopy(value["evidence"][0]))
    with pytest.raises(ValueError, match="duplicate evidence_id"):
        load_task(value)


def test_task_rejects_unknown_completion_fact() -> None:
    value = deepcopy(TASK)
    value["completion_facts"] = ["fact-unknown"]
    with pytest.raises(ValueError, match="completion_facts"):
        load_task(value)


def test_task_rejects_unknown_fields() -> None:
    value = {**TASK, "hidden_answer": "Wetland"}
    with pytest.raises(ValueError, match="schema"):
        load_task(value)


def test_task_requires_future_evidence_declaration_to_match_cutoff() -> None:
    value = deepcopy(TASK)
    value["evidence"].append(
        {
            "evidence_id": "ev-future",
            "available_at": "2026-01-11T00:00:00Z",
            "source": "synthetic-future-note",
            "text": "A future observation.",
            "fact_ids": ["fact-habitat"],
        }
    )
    with pytest.raises(ValueError, match="future_evidence_ids"):
        load_task(value)


def test_answer_loads_and_rejects_unknown_fields() -> None:
    answer = load_answer(ANSWER)
    assert answer.claims[0].evidence_ids == ("ev-001",)
    with pytest.raises(ValueError, match="schema"):
        load_answer({**ANSWER, "score": 1.0})


def test_harness_config_identity_changes_with_one_policy() -> None:
    control = HarnessConfig(context_policy="raw")
    treatment = HarnessConfig(context_policy="pit_budgeted")
    assert sha256_json(control) != sha256_json(treatment)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("context_budget_chars", 0),
        ("debate_conflict_threshold", -1),
    ],
)
def test_harness_config_rejects_invalid_bounds(field: str, value: int) -> None:
    kwargs = {field: value}
    with pytest.raises(ValueError, match=field):
        HarnessConfig(**kwargs)
