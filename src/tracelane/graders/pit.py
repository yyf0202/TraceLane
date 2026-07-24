from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from tracelane.contracts import AgentAnswer, TaskSpec
from tracelane.graders._claims import claim_views


@dataclass(frozen=True)
class PitGrade:
    pit_violations: int
    pit_safe: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "pit_violations": self.pit_violations,
            "pit_safe": self.pit_safe,
        }


def grade_pit(
    answer: AgentAnswer | Mapping[str, object],
    task: TaskSpec,
) -> PitGrade:
    future_ids = {
        record.evidence_id for record in task.evidence if record.available_at > task.cutoff_at
    }
    cited_ids = {evidence_id for claim in claim_views(answer) for evidence_id in claim.evidence_ids}
    violations = len(future_ids & cited_ids)
    return PitGrade(pit_violations=violations, pit_safe=violations == 0)
