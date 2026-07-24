from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from tests.test_contracts import TASK
from tracelane.adapters.fixtures import FixtureToolAdapter
from tracelane.contracts import EvidenceRecord, load_task
from tracelane.evidence import freeze_evidence


def task_with_cutoff_edges():
    value = deepcopy(TASK)
    value["evidence"] = [
        {
            "evidence_id": "ev-earlier",
            "available_at": "2026-01-09T23:59:59Z",
            "source": "synthetic-note",
            "text": "Wetland is preferred.",
            "fact_ids": ["fact-habitat"],
        },
        {
            "evidence_id": "ev-cutoff",
            "available_at": "2026-01-10T00:00:00Z",
            "source": "synthetic-note",
            "text": "Wetland is preferred.",
            "fact_ids": ["fact-habitat"],
        },
        {
            "evidence_id": "ev-future",
            "available_at": "2026-01-10T00:00:01Z",
            "source": "synthetic-note",
            "text": "A future observation.",
            "fact_ids": ["fact-habitat"],
        },
    ]
    value["future_evidence_ids"] = ["ev-future"]
    return load_task(value)


def test_fixture_adapter_returns_committed_task_records() -> None:
    task = task_with_cutoff_edges()
    assert FixtureToolAdapter().collect(task) == task.evidence


def test_evidence_at_cutoff_is_allowed_and_future_is_rejected() -> None:
    task = task_with_cutoff_edges()
    bundle = freeze_evidence(task, FixtureToolAdapter().collect(task))
    assert tuple(record.evidence_id for record in bundle.records) == (
        "ev-earlier",
        "ev-cutoff",
    )
    assert bundle.rejected_future_ids == ("ev-future",)


def test_bundle_hash_is_independent_of_adapter_return_order() -> None:
    task = task_with_cutoff_edges()
    forward = freeze_evidence(task, task.evidence)
    reversed_bundle = freeze_evidence(task, reversed(task.evidence))
    assert forward == reversed_bundle


def test_freezer_rejects_unknown_evidence_id() -> None:
    task = task_with_cutoff_edges()
    unknown = EvidenceRecord(
        evidence_id="ev-unknown",
        available_at=datetime(2026, 1, 9, tzinfo=UTC),
        source="synthetic-note",
        text="Unknown evidence.",
        fact_ids=("fact-habitat",),
    )
    with pytest.raises(ValueError, match="unknown evidence"):
        freeze_evidence(task, (*task.evidence, unknown))


def test_freezer_rejects_duplicate_evidence_id() -> None:
    task = task_with_cutoff_edges()
    with pytest.raises(ValueError, match="duplicate evidence"):
        freeze_evidence(task, (*task.evidence, task.evidence[0]))


def test_freezer_rejects_mutated_record_with_known_id() -> None:
    task = task_with_cutoff_edges()
    altered = EvidenceRecord(
        evidence_id=task.evidence[0].evidence_id,
        available_at=task.evidence[0].available_at,
        source=task.evidence[0].source,
        text="Mutated after task identity was fixed.",
        fact_ids=task.evidence[0].fact_ids,
    )
    with pytest.raises(ValueError, match="does not match task"):
        freeze_evidence(task, (altered, *task.evidence[1:]))


def test_freezer_rejects_missing_declared_future_record() -> None:
    task = task_with_cutoff_edges()
    without_future = tuple(record for record in task.evidence if record.evidence_id != "ev-future")
    with pytest.raises(ValueError, match="future evidence"):
        freeze_evidence(task, without_future)
