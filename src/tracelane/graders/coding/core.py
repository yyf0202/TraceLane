from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from tracelane.coding.commands import CommandEvidence, capture_command
from tracelane.coding.contracts import CodingTask
from tracelane.coding.workspace import WorkspaceSnapshot, capture_workspace


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


@dataclass(frozen=True)
class AcceptanceGrade:
    status: str
    commands: tuple[CommandEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "commands": [item.to_dict() for item in self.commands]}


@dataclass(frozen=True)
class DiffGrade:
    status: str
    protected_changes: tuple[str, ...]
    out_of_scope_changes: tuple[str, ...]
    suspicious_patterns: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "protected_changes": list(self.protected_changes),
            "out_of_scope_changes": list(self.out_of_scope_changes),
            "suspicious_patterns": list(self.suspicious_patterns),
        }


@dataclass(frozen=True)
class RecoveryGrade:
    status: str
    failed_command_count: int
    wasted_retry_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "failed_command_count": self.failed_command_count,
            "wasted_retry_count": self.wasted_retry_count,
        }


@dataclass(frozen=True)
class CostGrade:
    command_count: int
    failed_command_count: int
    duration_ms: int
    input_tokens: int
    output_tokens: int

    def to_dict(self) -> dict[str, int]:
        return {
            "command_count": self.command_count,
            "failed_command_count": self.failed_command_count,
            "duration_ms": self.duration_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass(frozen=True)
class FunctionalCriterionGrade:
    name: str
    points: int
    earned: int
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "points": self.points,
            "earned": self.earned,
            "error": self.error,
        }


@dataclass(frozen=True)
class FunctionalGrade:
    status: str
    earned: int
    possible: int
    criteria: tuple[FunctionalCriterionGrade, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "earned": self.earned,
            "possible": self.possible,
            "criteria": [item.to_dict() for item in self.criteria],
        }


@dataclass(frozen=True)
class CodingGradeReport:
    acceptance: AcceptanceGrade
    diff: DiffGrade
    recovery: RecoveryGrade
    cost: CostGrade
    functional: FunctionalGrade
    overall: str

    def to_dict(self) -> dict[str, object]:
        return {
            "acceptance": self.acceptance.to_dict(),
            "diff": self.diff.to_dict(),
            "recovery": self.recovery.to_dict(),
            "cost": self.cost.to_dict(),
            "functional": self.functional.to_dict(),
            "overall": self.overall,
        }


def grade_acceptance(repository: str | Path, task: CodingTask) -> AcceptanceGrade:
    commands: list[CommandEvidence] = []
    for command in (*task.acceptance.public_commands, *task.acceptance.hidden_commands):
        commands.append(capture_command(repository, task, command, enforce_allowed=False))
    status = "pass" if all(item.status == "passed" for item in commands) else "fail"
    return AcceptanceGrade(status=status, commands=tuple(commands))


def grade_diff(snapshot: WorkspaceSnapshot, task: CodingTask) -> DiffGrade:
    relevant_paths = tuple(
        path
        for path in snapshot.changed_paths
        if not _matches(path, task.diff_policy.ignored_paths)
    )
    protected = tuple(
        path for path in relevant_paths if _matches(path, task.diff_policy.protected_paths)
    )
    out_of_scope = tuple(
        path
        for path in relevant_paths
        if not _matches(path, task.diff_policy.editable_paths)
        and not _matches(path, task.diff_policy.protected_paths)
    )
    suspicious = tuple(
        marker
        for marker in ("pytest.skip", "@pytest.mark.skip", "pytest.xfail", "@pytest.mark.xfail")
        if marker in snapshot.patch
    )
    status = "invalid" if protected or out_of_scope else "pass"
    return DiffGrade(
        status=status,
        protected_changes=protected,
        out_of_scope_changes=out_of_scope,
        suspicious_patterns=suspicious,
    )


def grade_recovery(commands: Sequence[CommandEvidence]) -> RecoveryGrade:
    failures = [item for item in commands if item.status in {"failed", "timed_out"}]
    if not failures:
        return RecoveryGrade(status="not_applicable", failed_command_count=0, wasted_retry_count=0)
    recovered = False
    wasted = 0
    for index, item in enumerate(commands):
        if item.status not in {"failed", "timed_out"}:
            continue
        remaining = commands[index + 1 :]
        same_command = [candidate for candidate in remaining if candidate.command == item.command]
        if same_command and same_command[0].before_workspace_sha256 == item.after_workspace_sha256:
            wasted += 1
        if any(
            candidate.status == "passed"
            and candidate.classification == item.classification
            and candidate.before_workspace_sha256 != item.after_workspace_sha256
            for candidate in remaining
        ):
            recovered = True
    return RecoveryGrade(
        status="recovered" if recovered else "unrecovered",
        failed_command_count=len(failures),
        wasted_retry_count=wasted,
    )


def grade_cost(
    commands: Sequence[CommandEvidence], *, input_tokens: int = 0, output_tokens: int = 0
) -> CostGrade:
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    return CostGrade(
        command_count=len(commands),
        failed_command_count=sum(item.status != "passed" for item in commands),
        duration_ms=sum(item.duration_ms for item in commands),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def grade_functional(commands: Sequence[CommandEvidence]) -> FunctionalGrade:
    """Parse the last validated ``TRACELANE_SCORE`` emitted by an acceptance command."""
    marker = "TRACELANE_SCORE="
    payload: object | None = None
    for command in commands:
        for line in command.stdout_preview.splitlines():
            if line.startswith(marker):
                try:
                    payload = json.loads(line.removeprefix(marker))
                except json.JSONDecodeError:
                    continue
    if not isinstance(payload, dict):
        return FunctionalGrade(status="not_scored", earned=0, possible=0, criteria=())

    earned = payload.get("earned")
    possible = payload.get("possible")
    raw_criteria = payload.get("criteria")
    if (
        isinstance(earned, bool)
        or not isinstance(earned, int)
        or isinstance(possible, bool)
        or not isinstance(possible, int)
        or possible <= 0
        or earned < 0
        or earned > possible
        or not isinstance(raw_criteria, list)
    ):
        return FunctionalGrade(status="invalid_score", earned=0, possible=0, criteria=())

    criteria: list[FunctionalCriterionGrade] = []
    for raw in raw_criteria:
        if not isinstance(raw, dict):
            return FunctionalGrade(status="invalid_score", earned=0, possible=0, criteria=())
        name = raw.get("name")
        points = raw.get("points")
        criterion_earned = raw.get("earned")
        error = raw.get("error")
        if (
            not isinstance(name, str)
            or not name.strip()
            or isinstance(points, bool)
            or not isinstance(points, int)
            or points <= 0
            or isinstance(criterion_earned, bool)
            or not isinstance(criterion_earned, int)
            or criterion_earned < 0
            or criterion_earned > points
            or (error is not None and not isinstance(error, str))
        ):
            return FunctionalGrade(status="invalid_score", earned=0, possible=0, criteria=())
        criteria.append(
            FunctionalCriterionGrade(
                name=name.strip(),
                points=points,
                earned=criterion_earned,
                error=error,
            )
        )

    if (
        len({criterion.name for criterion in criteria}) != len(criteria)
        or sum(criterion.points for criterion in criteria) != possible
        or sum(criterion.earned for criterion in criteria) != earned
    ):
        return FunctionalGrade(status="invalid_score", earned=0, possible=0, criteria=())
    return FunctionalGrade(
        status="pass" if earned == possible else "partial",
        earned=earned,
        possible=possible,
        criteria=tuple(criteria),
    )


def grade_attempt(
    repository: str | Path,
    task: CodingTask,
    *,
    command_history: Sequence[CommandEvidence] = (),
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> CodingGradeReport:
    final_workspace = capture_workspace(repository, task.baseline.commit_sha)
    acceptance = grade_acceptance(repository, task)
    diff = grade_diff(final_workspace, task)
    all_commands = (*command_history, *acceptance.commands)
    recovery = grade_recovery(all_commands)
    cost = grade_cost(all_commands, input_tokens=input_tokens, output_tokens=output_tokens)
    functional = grade_functional(acceptance.commands)
    if diff.status == "invalid":
        overall = "invalid"
    elif acceptance.status != "pass" or functional.status == "invalid_score":
        overall = "fail"
    else:
        overall = "pass"
    return CodingGradeReport(
        acceptance=acceptance,
        diff=diff,
        recovery=recovery,
        cost=cost,
        functional=functional,
        overall=overall,
    )
