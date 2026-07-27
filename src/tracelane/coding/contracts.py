from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from tracelane.contracts import canonical_json, sha256_json

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_ATTEMPT_END_REASONS = frozenset(
    {"completed", "budget_exhausted", "blocked", "cancelled", "superseded", "crashed"}
)


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _unique_strings(
    values: tuple[str, ...], label: str, *, required: bool = False
) -> tuple[str, ...]:
    normalized = tuple(_non_empty(value, label) for value in values)
    if required and not normalized:
        raise ValueError(f"{label} must not be empty")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must contain unique strings")
    return normalized


def _relative_path(value: str, label: str) -> str:
    path = _non_empty(value, label)
    if path.startswith("/") or path.startswith("\\") or _DRIVE_PATH.match(path):
        raise ValueError(f"{label} must be relative")
    if "\\" in path or any(part in {"", ".", ".."} for part in path.split("/")):
        raise ValueError(f"{label} must be a normalized relative path")
    return path


def _path_scope(path: str) -> str:
    return path[:-3].rstrip("/") if path.endswith("/**") else path


def _paths_overlap(left: str, right: str) -> bool:
    left_scope = _path_scope(left)
    right_scope = _path_scope(right)
    return (
        left_scope == right_scope
        or left_scope.startswith(f"{right_scope}/")
        or right_scope.startswith(f"{left_scope}/")
    )


@dataclass(frozen=True)
class RepositoryBaseline:
    repository_id: str
    commit_sha: str
    tree_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository_id", _non_empty(self.repository_id, "repository_id"))
        if not isinstance(self.commit_sha, str) or not _GIT_SHA.fullmatch(self.commit_sha):
            raise ValueError("commit_sha must be a lowercase 40-character Git SHA")
        if not isinstance(self.tree_sha256, str) or not _SHA256.fullmatch(self.tree_sha256):
            raise ValueError("tree_sha256 must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "repository_id": self.repository_id,
            "commit_sha": self.commit_sha,
            "tree_sha256": self.tree_sha256,
        }


@dataclass(frozen=True)
class AcceptanceSpec:
    public_commands: tuple[str, ...]
    hidden_commands: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "public_commands",
            _unique_strings(tuple(self.public_commands), "public_commands", required=True),
        )
        object.__setattr__(
            self,
            "hidden_commands",
            _unique_strings(tuple(self.hidden_commands), "hidden_commands"),
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "public_commands": list(self.public_commands),
            "hidden_commands": list(self.hidden_commands),
        }


@dataclass(frozen=True)
class DiffPolicy:
    editable_paths: tuple[str, ...]
    protected_paths: tuple[str, ...]
    ignored_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        editable = tuple(_relative_path(path, "editable_paths") for path in self.editable_paths)
        protected = tuple(_relative_path(path, "protected_paths") for path in self.protected_paths)
        ignored = tuple(_relative_path(path, "ignored_paths") for path in self.ignored_paths)
        _unique_strings(editable, "editable_paths", required=True)
        _unique_strings(protected, "protected_paths")
        _unique_strings(ignored, "ignored_paths")
        if any(_paths_overlap(left, right) for left in editable for right in protected):
            raise ValueError("editable_paths and protected_paths must not overlap")
        if any(_paths_overlap(left, right) for left in ignored for right in protected):
            raise ValueError("ignored_paths and protected_paths must not overlap")
        object.__setattr__(self, "editable_paths", editable)
        object.__setattr__(self, "protected_paths", protected)
        object.__setattr__(self, "ignored_paths", ignored)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "editable_paths": list(self.editable_paths),
            "protected_paths": list(self.protected_paths),
            "ignored_paths": list(self.ignored_paths),
        }


@dataclass(frozen=True)
class InteractionScript:
    mode: Literal["scripted_optional", "live"]
    requires_approval: bool

    def __post_init__(self) -> None:
        if self.mode not in {"scripted_optional", "live"}:
            raise ValueError("interaction mode is invalid")
        if not isinstance(self.requires_approval, bool):
            raise ValueError("requires_approval must be a boolean")

    @property
    def replayable(self) -> bool:
        return self.mode == "scripted_optional"

    def to_dict(self) -> dict[str, object]:
        return {"mode": self.mode, "requires_approval": self.requires_approval}


@dataclass(frozen=True)
class CodingTask:
    task_id: str
    version: int
    baseline: RepositoryBaseline
    objective: str
    acceptance: AcceptanceSpec
    diff_policy: DiffPolicy
    interaction: InteractionScript
    allowed_commands: tuple[str, ...]
    max_wall_seconds: int
    max_tool_calls: int
    max_model_tokens: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _non_empty(self.task_id, "task_id"))
        object.__setattr__(self, "objective", _non_empty(self.objective, "objective"))
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(self.baseline, RepositoryBaseline):
            raise ValueError("baseline must be a RepositoryBaseline")
        if not isinstance(self.acceptance, AcceptanceSpec):
            raise ValueError("acceptance must be an AcceptanceSpec")
        if not isinstance(self.diff_policy, DiffPolicy):
            raise ValueError("diff_policy must be a DiffPolicy")
        if not isinstance(self.interaction, InteractionScript):
            raise ValueError("interaction must be an InteractionScript")
        object.__setattr__(
            self,
            "allowed_commands",
            _unique_strings(tuple(self.allowed_commands), "allowed_commands", required=True),
        )
        for name in ("max_wall_seconds", "max_tool_calls", "max_model_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "coding-task/v0.1",
            "task_id": self.task_id,
            "version": self.version,
            "baseline": self.baseline.to_dict(),
            "objective": self.objective,
            "acceptance": self.acceptance.to_dict(),
            "diff_policy": self.diff_policy.to_dict(),
            "interaction": self.interaction.to_dict(),
            "allowed_commands": list(self.allowed_commands),
            "budget": {
                "max_wall_seconds": self.max_wall_seconds,
                "max_tool_calls": self.max_tool_calls,
                "max_model_tokens": self.max_model_tokens,
            },
        }

    @property
    def task_sha256(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class SessionRef:
    session_id: str
    root_session_id: str
    parent_session_id: str | None
    agent_kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _non_empty(self.session_id, "session_id"))
        object.__setattr__(
            self,
            "root_session_id",
            _non_empty(self.root_session_id, "root_session_id"),
        )
        object.__setattr__(self, "agent_kind", _non_empty(self.agent_kind, "agent_kind"))
        if self.parent_session_id is not None:
            object.__setattr__(
                self,
                "parent_session_id",
                _non_empty(self.parent_session_id, "parent_session_id"),
            )
        if self.session_id == self.root_session_id and self.parent_session_id is not None:
            raise ValueError("root session must not have a parent")

    @property
    def relationship(self) -> str:
        return "root" if self.session_id == self.root_session_id else "child"


@dataclass(frozen=True)
class AttemptEnd:
    reason: str
    final_answer: str | None

    def __post_init__(self) -> None:
        if self.reason not in _ATTEMPT_END_REASONS:
            raise ValueError("attempt end reason is invalid")
        if self.final_answer is not None:
            object.__setattr__(self, "final_answer", _non_empty(self.final_answer, "final_answer"))

    def to_dict(self) -> dict[str, object]:
        return {"reason": self.reason, "final_answer": self.final_answer}


def canonical_task_json(task: CodingTask) -> str:
    return canonical_json(task.to_dict())


def load_coding_task(value: Mapping[str, object]) -> CodingTask:
    """Load the canonical ``coding-task/v0.1`` mapping into validated contracts."""
    if not isinstance(value, Mapping):
        raise ValueError("coding task must be a mapping")
    expected = {
        "schema_version",
        "task_id",
        "version",
        "baseline",
        "objective",
        "acceptance",
        "diff_policy",
        "interaction",
        "allowed_commands",
        "budget",
    }
    if set(value) != expected or value.get("schema_version") != "coding-task/v0.1":
        raise ValueError("coding task fields or schema_version are invalid")
    baseline = value.get("baseline")
    acceptance = value.get("acceptance")
    diff_policy = value.get("diff_policy")
    interaction = value.get("interaction")
    budget = value.get("budget")
    if not all(
        isinstance(item, Mapping)
        for item in (baseline, acceptance, diff_policy, interaction, budget)
    ):
        raise ValueError("coding task nested fields must be mappings")
    assert isinstance(baseline, Mapping)
    assert isinstance(acceptance, Mapping)
    assert isinstance(diff_policy, Mapping)
    assert isinstance(interaction, Mapping)
    assert isinstance(budget, Mapping)
    nested_fields = (
        (baseline, {"repository_id", "commit_sha", "tree_sha256"}),
        (acceptance, {"public_commands", "hidden_commands"}),
        (
            diff_policy,
            {"editable_paths", "protected_paths", "ignored_paths"},
        ),
        (interaction, {"mode", "requires_approval"}),
        (
            budget,
            {"max_wall_seconds", "max_tool_calls", "max_model_tokens"},
        ),
    )
    if any(set(item) != expected for item, expected in nested_fields):
        raise ValueError("coding task nested fields are invalid")

    sequence_fields = (
        acceptance["public_commands"],
        acceptance["hidden_commands"],
        diff_policy["editable_paths"],
        diff_policy["protected_paths"],
        diff_policy["ignored_paths"],
        value["allowed_commands"],
    )
    if any(
        not isinstance(item, (list, tuple)) or any(not isinstance(member, str) for member in item)
        for item in sequence_fields
    ):
        raise ValueError("coding task command and path fields must be string arrays")
    try:
        return CodingTask(
            task_id=value["task_id"],
            version=value["version"],
            baseline=RepositoryBaseline(
                repository_id=baseline["repository_id"],
                commit_sha=baseline["commit_sha"],
                tree_sha256=baseline["tree_sha256"],
            ),
            objective=value["objective"],
            acceptance=AcceptanceSpec(
                public_commands=tuple(acceptance["public_commands"]),
                hidden_commands=tuple(acceptance["hidden_commands"]),
            ),
            diff_policy=DiffPolicy(
                editable_paths=tuple(diff_policy["editable_paths"]),
                protected_paths=tuple(diff_policy["protected_paths"]),
                ignored_paths=tuple(diff_policy.get("ignored_paths", ())),
            ),
            interaction=InteractionScript(
                mode=interaction["mode"],
                requires_approval=interaction["requires_approval"],
            ),
            allowed_commands=tuple(value["allowed_commands"]),
            max_wall_seconds=budget["max_wall_seconds"],
            max_tool_calls=budget["max_tool_calls"],
            max_model_tokens=budget["max_model_tokens"],
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("coding task nested fields are invalid") from exc
