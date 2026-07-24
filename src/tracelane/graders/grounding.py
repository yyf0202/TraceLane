from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tracelane.contracts import AgentAnswer, FrozenBundle, TaskSpec
from tracelane.graders._claims import claim_views


@dataclass(frozen=True)
class GroundingGrade:
    cited_claims: int
    supported_cited_claims: int
    citation_precision: float
    citation_recall: float
    unsupported_claims: int

    def to_dict(self) -> dict[str, object]:
        return {
            "cited_claims": self.cited_claims,
            "supported_cited_claims": self.supported_cited_claims,
            "citation_precision": self.citation_precision,
            "citation_recall": self.citation_recall,
            "unsupported_claims": self.unsupported_claims,
        }


def grade_grounding(
    answer: AgentAnswer | Mapping[str, object],
    task: TaskSpec,
    bundle: FrozenBundle,
) -> GroundingGrade:
    evidence_by_id = {record.evidence_id: record for record in bundle.records}
    required = set(task.completion_facts)
    cited_claims = 0
    supported_cited_claims = 0
    unsupported_claims = 0
    supported_required_facts: set[str] = set()

    for claim in claim_views(answer):
        if claim.evidence_ids:
            cited_claims += 1
        cited_records = [
            evidence_by_id[evidence_id]
            for evidence_id in claim.evidence_ids
            if evidence_id in evidence_by_id
        ]
        supported_facts = {
            fact_id
            for fact_id in claim.fact_ids
            if any(fact_id in record.fact_ids for record in cited_records)
        }
        supported = (
            bool(claim.evidence_ids)
            and bool(claim.fact_ids)
            and (len(supported_facts) == len(set(claim.fact_ids)))
        )
        if supported:
            supported_cited_claims += 1
            supported_required_facts.update(supported_facts & required)
        else:
            unsupported_claims += 1

    required_count = len(required)
    return GroundingGrade(
        cited_claims=cited_claims,
        supported_cited_claims=supported_cited_claims,
        citation_precision=(supported_cited_claims / cited_claims if cited_claims else 0.0),
        citation_recall=(len(supported_required_facts) / required_count if required_count else 0.0),
        unsupported_claims=unsupported_claims,
    )
