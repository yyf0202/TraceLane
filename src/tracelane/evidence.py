from __future__ import annotations

from collections.abc import Iterable

from tracelane.contracts import (
    EvidenceRecord,
    FrozenBundle,
    TaskSpec,
    canonical_json,
    sha256_json,
)


def freeze_evidence(
    task: TaskSpec,
    records: Iterable[EvidenceRecord],
) -> FrozenBundle:
    collected = tuple(records)
    if not all(isinstance(record, EvidenceRecord) for record in collected):
        raise ValueError("adapter returned a value that is not an EvidenceRecord")
    collected_ids = [record.evidence_id for record in collected]
    if len(collected_ids) != len(set(collected_ids)):
        raise ValueError("adapter returned duplicate evidence IDs")

    expected_by_id = {record.evidence_id: record for record in task.evidence}
    unknown = set(collected_ids) - set(expected_by_id)
    if unknown:
        raise ValueError(f"adapter returned unknown evidence IDs: {sorted(unknown)}")
    missing = set(expected_by_id) - set(collected_ids)
    missing_future = missing & set(task.future_evidence_ids)
    if missing_future:
        raise ValueError(f"adapter omitted declared future evidence: {sorted(missing_future)}")
    if missing:
        raise ValueError(f"adapter omitted task evidence: {sorted(missing)}")

    for record in collected:
        if canonical_json(record) != canonical_json(expected_by_id[record.evidence_id]):
            raise ValueError(f"adapter evidence {record.evidence_id} does not match task content")

    ordered = tuple(sorted(collected, key=lambda record: (record.available_at, record.evidence_id)))
    admitted = tuple(record for record in ordered if record.available_at <= task.cutoff_at)
    rejected = tuple(
        sorted(record.evidence_id for record in ordered if record.available_at > task.cutoff_at)
    )
    if set(rejected) != set(task.future_evidence_ids):
        raise ValueError("frozen future evidence does not match task declaration")
    payload = {
        "task_id": task.task_id,
        "cutoff_at": task.cutoff_at,
        "records": admitted,
        "rejected_future_ids": rejected,
    }
    return FrozenBundle(
        task_id=task.task_id,
        cutoff_at=task.cutoff_at,
        records=admitted,
        rejected_future_ids=rejected,
        bundle_sha256=sha256_json(payload),
    )
