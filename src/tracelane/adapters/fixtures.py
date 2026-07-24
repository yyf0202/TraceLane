from __future__ import annotations

from tracelane.contracts import EvidenceRecord, TaskSpec


class FixtureToolAdapter:
    """Return the immutable evidence committed with a synthetic task."""

    def collect(self, task: TaskSpec) -> tuple[EvidenceRecord, ...]:
        return task.evidence
