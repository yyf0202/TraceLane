from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tracelane.contracts import TaskSpec


@dataclass(frozen=True)
class RecoveryGrade:
    recovery_applicable: bool
    recovery_success: bool | None
    resume_position: str | None
    repeated_completed_stages: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "recovery_applicable": self.recovery_applicable,
            "recovery_success": self.recovery_success,
            "resume_position": self.resume_position,
            "repeated_completed_stages": self.repeated_completed_stages,
        }


def grade_recovery(
    trace: Sequence[Mapping[str, object]],
    task: TaskSpec,
) -> RecoveryGrade:
    resume_index: int | None = None
    resume_position: str | None = None
    for index, event in enumerate(trace):
        if event.get("event_type") != "run.resumed":
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping) and isinstance(payload.get("checkpoint_stage"), str):
            resume_index = index
            resume_position = payload["checkpoint_stage"]

    repeated: set[str] = set()
    if resume_index is not None:
        completed_before = {
            event.get("stage")
            for event in trace[:resume_index]
            if event.get("event_type") == "stage.completed" and isinstance(event.get("stage"), str)
        }
        started_after = {
            event.get("stage")
            for event in trace[resume_index + 1 :]
            if event.get("event_type") == "stage.started" and isinstance(event.get("stage"), str)
        }
        repeated = completed_before & started_after

    applicable = task.fault_scenario is not None
    success = bool(resume_position) and not repeated if applicable else None
    return RecoveryGrade(
        recovery_applicable=applicable,
        recovery_success=success,
        resume_position=resume_position,
        repeated_completed_stages=tuple(sorted(repeated)),
    )
