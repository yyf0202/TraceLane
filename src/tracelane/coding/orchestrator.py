from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tracelane.artifacts import RunStore
from tracelane.coding.commands import CommandEvidence
from tracelane.coding.contracts import AttemptEnd, CodingTask
from tracelane.coding.session_importer import AttemptSession, import_coding_attempt
from tracelane.coding.workspace import WorkspaceSnapshot
from tracelane.graders.coding import CodingGradeReport, grade_attempt


@dataclass(frozen=True)
class FinalizedCodingAttempt:
    store: RunStore
    grades: CodingGradeReport


def finalize_coding_attempt(
    task: CodingTask,
    *,
    attempt_id: str,
    sessions: tuple[AttemptSession, ...],
    initial_workspace: WorkspaceSnapshot,
    final_workspace: WorkspaceSnapshot,
    end: AttemptEnd,
    repository: str | Path,
    artifact_root: str | Path,
    harness_config: Mapping[str, object],
    command_history: Sequence[CommandEvidence] = (),
    input_tokens: int = 0,
    output_tokens: int = 0,
    provider_cost: Mapping[str, object] | None = None,
    repeat: int = 1,
) -> FinalizedCodingAttempt:
    """Import one completed coding attempt and independently grade its final workspace."""
    store = import_coding_attempt(
        task,
        attempt_id=attempt_id,
        sessions=sessions,
        initial_workspace=initial_workspace,
        final_workspace=final_workspace,
        end=end,
        artifact_root=artifact_root,
        harness_config=harness_config,
        repeat=repeat,
    )
    grades = grade_attempt(
        repository,
        task,
        command_history=command_history,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    store.write_json("output/coding-grades.json", grades.to_dict())
    if provider_cost is not None:
        store.write_json("output/provider-cost.json", dict(provider_cost))
    return FinalizedCodingAttempt(store=store, grades=grades)
