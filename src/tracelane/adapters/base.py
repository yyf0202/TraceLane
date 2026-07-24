from __future__ import annotations

from typing import Protocol

from tracelane.contracts import EvidenceRecord, TaskSpec


class EvidenceAdapter(Protocol):
    def collect(self, task: TaskSpec) -> tuple[EvidenceRecord, ...]: ...
