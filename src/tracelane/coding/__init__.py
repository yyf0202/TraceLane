"""Contracts and evidence handling for reproducible coding-agent evaluations."""

from tracelane.coding.contracts import (
    AcceptanceSpec,
    AttemptEnd,
    CodingTask,
    DiffPolicy,
    InteractionScript,
    RepositoryBaseline,
    SessionRef,
)
from tracelane.coding.orchestrator import FinalizedCodingAttempt, finalize_coding_attempt

__all__ = [
    "AcceptanceSpec",
    "AttemptEnd",
    "CodingTask",
    "DiffPolicy",
    "InteractionScript",
    "RepositoryBaseline",
    "SessionRef",
    "FinalizedCodingAttempt",
    "finalize_coding_attempt",
]
