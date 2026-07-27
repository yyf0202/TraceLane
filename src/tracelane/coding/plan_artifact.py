from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from tracelane.coding.contracts import CodingTask
from tracelane.contracts import canonical_json


@dataclass(frozen=True)
class PlanArtifact:
    task_sha256: str
    plan_session_id: str
    content: str
    content_sha256: str
    source_cli_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.plan_session_id, str) or not self.plan_session_id.strip():
            raise ValueError("plan_session_id must be non-empty")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("plan content must be non-empty")
        expected_content = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        if self.content_sha256 != expected_content:
            raise ValueError("plan content digest does not match")
        for label, value in (
            ("task_sha256", self.task_sha256),
            ("source_cli_sha256", self.source_cli_sha256),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{label} must be a lowercase SHA-256 digest")

    def to_dict(self) -> dict[str, str]:
        return {
            "schema_version": "coding-plan/v0.1",
            "task_sha256": self.task_sha256,
            "plan_session_id": self.plan_session_id,
            "content": self.content,
            "content_sha256": self.content_sha256,
            "source_cli_sha256": self.source_cli_sha256,
        }


def extract_plan_artifact(task: CodingTask, cli_jsonl: str | Path) -> PlanArtifact:
    """Freeze the final plan text and bind it to the task and source CLI transcript."""
    source = Path(cli_jsonl).read_bytes()
    session_ids: set[str] = set()
    text_parts: list[str] = []
    for raw_line in source.splitlines():
        row = json.loads(raw_line)
        session_id = row.get("sessionID")
        if isinstance(session_id, str):
            session_ids.add(session_id)
        part = row.get("part")
        if (
            row.get("type") == "text"
            and isinstance(part, dict)
            and isinstance(part.get("text"), str)
        ):
            text_parts.append(part["text"])
    if len(session_ids) != 1:
        raise ValueError("plan CLI transcript must contain exactly one session")
    if not text_parts:
        raise ValueError("plan CLI transcript has no final plan text")
    content = text_parts[-1].strip()
    return PlanArtifact(
        task_sha256=task.task_sha256,
        plan_session_id=next(iter(session_ids)),
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        source_cli_sha256=hashlib.sha256(source).hexdigest(),
    )


def load_plan_artifact(value: object) -> PlanArtifact:
    if not isinstance(value, dict):
        raise ValueError("plan artifact must be a JSON object")
    expected = {
        "schema_version",
        "task_sha256",
        "plan_session_id",
        "content",
        "content_sha256",
        "source_cli_sha256",
    }
    if set(value) != expected or value.get("schema_version") != "coding-plan/v0.1":
        raise ValueError("plan artifact fields or schema_version are invalid")
    try:
        return PlanArtifact(
            task_sha256=value["task_sha256"],
            plan_session_id=value["plan_session_id"],
            content=value["content"],
            content_sha256=value["content_sha256"],
            source_cli_sha256=value["source_cli_sha256"],
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("plan artifact fields are invalid") from exc


def build_handoff_prompt(task: CodingTask, plan: PlanArtifact) -> str:
    """Create a build prompt that carries the exact frozen plan, not a session hint."""
    if plan.task_sha256 != task.task_sha256:
        raise ValueError("plan artifact belongs to a different coding task")
    return (
        "Implement the frozen plan below for the stated CodingTask. "
        "Treat the plan as guidance, but preserve the task objective and acceptance criteria. "
        "If repository evidence contradicts the plan, explain and make the smallest safe "
        "correction.\n\n"
        f"CodingTask objective:\n{task.objective}\n\n"
        f"Frozen plan SHA-256: {plan.content_sha256}\n"
        "<frozen-plan>\n"
        f"{plan.content}\n"
        "</frozen-plan>\n"
    )


def write_plan_artifact(path: str | Path, plan: PlanArtifact) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_json(plan.to_dict()) + "\n", encoding="utf-8")
    return target
