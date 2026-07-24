from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tracelane.contracts import AgentAnswer, TaskSpec
from tracelane.graders._claims import claim_views


@dataclass(frozen=True)
class CompletionGrade:
    covered_required_facts: int
    required_facts: int
    coverage: float

    def to_dict(self) -> dict[str, object]:
        return {
            "covered_required_facts": self.covered_required_facts,
            "required_facts": self.required_facts,
            "coverage": self.coverage,
        }


def grade_completion(
    answer: AgentAnswer | Mapping[str, object],
    task: TaskSpec,
) -> CompletionGrade:
    required = set(task.completion_facts)
    claimed = {fact_id for claim in claim_views(answer) for fact_id in claim.fact_ids}
    covered = len(required & claimed)
    total = len(required)
    return CompletionGrade(
        covered_required_facts=covered,
        required_facts=total,
        coverage=covered / total if total else 0.0,
    )
