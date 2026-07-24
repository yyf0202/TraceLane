from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from tracelane.contracts import (
    AgentAnswer,
    FrozenBundle,
    TaskSpec,
    canonical_json,
    load_answer,
    sha256_json,
)


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    schema_valid: bool
    errors: tuple[str, ...]
    answer_sha256: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "schema_valid": self.schema_valid,
            "errors": self.errors,
            "answer_sha256": self.answer_sha256,
        }


def _schema_checked_answer(value: AgentAnswer | Mapping[str, object]) -> AgentAnswer:
    if isinstance(value, AgentAnswer):
        normalized = json.loads(canonical_json(value))
        return load_answer(normalized)
    return load_answer(value)


def validate_answer(
    answer: AgentAnswer | Mapping[str, object],
    task: TaskSpec,
    bundle: FrozenBundle,
    *,
    expected_sha256: str | None = None,
) -> ValidationReport:
    try:
        checked = _schema_checked_answer(answer)
        answer_sha256 = sha256_json(checked)
    except ValueError as exc:
        return ValidationReport(
            valid=False,
            schema_valid=False,
            errors=(f"answer schema or canonical JSON is invalid: {exc}",),
            answer_sha256=None,
        )

    errors: list[str] = []
    known_evidence = {record.evidence_id for record in task.evidence}
    admitted_evidence = {record.evidence_id: record for record in bundle.records}
    known_facts = set(task.expected_facts)

    cited_evidence = {evidence_id for claim in checked.claims for evidence_id in claim.evidence_ids}
    unknown_evidence = cited_evidence - known_evidence
    if unknown_evidence:
        errors.append(f"answer references unknown evidence: {sorted(unknown_evidence)}")
    future_evidence = (cited_evidence & known_evidence) - set(admitted_evidence)
    if future_evidence:
        errors.append(f"answer references future evidence: {sorted(future_evidence)}")

    claimed_facts = {fact_id for claim in checked.claims for fact_id in claim.fact_ids}
    unknown_facts = claimed_facts - known_facts
    if unknown_facts:
        errors.append(f"answer references unknown facts: {sorted(unknown_facts)}")

    for claim_index, claim in enumerate(checked.claims):
        cited_records = [
            admitted_evidence[evidence_id]
            for evidence_id in claim.evidence_ids
            if evidence_id in admitted_evidence
        ]
        for fact_id in claim.fact_ids:
            if fact_id in known_facts and not any(
                fact_id in record.fact_ids for record in cited_records
            ):
                errors.append(
                    f"claim {claim_index} fact {fact_id} is not supported by cited evidence"
                )

    if expected_sha256 is not None and answer_sha256 != expected_sha256:
        errors.append("published answer hash does not match the expected hash")

    return ValidationReport(
        valid=not errors,
        schema_valid=True,
        errors=tuple(errors),
        answer_sha256=answer_sha256,
    )
