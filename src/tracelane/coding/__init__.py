"""Contracts and evidence handling for reproducible coding-agent evaluations."""

from tracelane.coding.contracts import (
    AcceptanceSpec,
    AttemptEnd,
    CodingTask,
    DiffPolicy,
    InteractionScript,
    RepositoryBaseline,
    SessionRef,
    load_coding_task,
)
from tracelane.coding.orchestrator import FinalizedCodingAttempt, finalize_coding_attempt
from tracelane.coding.plan_artifact import (
    PlanArtifact,
    build_handoff_prompt,
    extract_plan_artifact,
    load_plan_artifact,
)

__all__ = [
    "AcceptanceSpec",
    "AttemptEnd",
    "CodingTask",
    "DiffPolicy",
    "InteractionScript",
    "RepositoryBaseline",
    "SessionRef",
    "load_coding_task",
    "FinalizedCodingAttempt",
    "finalize_coding_attempt",
    "PlanArtifact",
    "build_handoff_prompt",
    "extract_plan_artifact",
    "load_plan_artifact",
]
