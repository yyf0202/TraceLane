from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from importlib.resources import files
from types import MappingProxyType
from typing import Literal

from jsonschema import Draft202012Validator

ContextPolicy = Literal["raw", "pit_budgeted"]
DebatePolicy = Literal["always", "conditional"]
RecoveryPolicy = Literal["restart", "checkpoint"]
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"timestamp is not valid ISO-8601: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _json_value(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must include a timezone")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_json_value(item) for item in value), key=str)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite numbers are not canonical JSON")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"value of type {type(value).__name__} is not canonical JSON")


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not canonical JSON: {exc}") from exc


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _unique_strings(value: tuple[str, ...], label: str, *, required: bool = False) -> None:
    if required and not value:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must contain unique strings")


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    available_at: datetime
    source: str
    text: str
    fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _non_empty(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "source", _non_empty(self.source, "source"))
        object.__setattr__(self, "text", _non_empty(self.text, "evidence text"))
        if self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise ValueError("available_at must include a timezone")
        object.__setattr__(self, "available_at", self.available_at.astimezone(UTC))
        object.__setattr__(self, "fact_ids", tuple(self.fact_ids))
        _unique_strings(self.fact_ids, "fact_ids", required=True)


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    question: str
    cutoff_at: datetime
    expected_facts: Mapping[str, str]
    completion_facts: tuple[str, ...]
    evidence: tuple[EvidenceRecord, ...]
    future_evidence_ids: tuple[str, ...]
    fault_scenario: str | None
    license: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _non_empty(self.task_id, "task_id"))
        object.__setattr__(self, "question", _non_empty(self.question, "question"))
        object.__setattr__(self, "license", _non_empty(self.license, "license"))
        if self.cutoff_at.tzinfo is None or self.cutoff_at.utcoffset() is None:
            raise ValueError("cutoff_at must include a timezone")
        object.__setattr__(self, "cutoff_at", self.cutoff_at.astimezone(UTC))
        frozen_facts = {
            _non_empty(key, "expected fact ID"): _non_empty(text, f"expected fact {key}")
            for key, text in self.expected_facts.items()
        }
        if not frozen_facts:
            raise ValueError("expected_facts must not be empty")
        object.__setattr__(self, "expected_facts", MappingProxyType(frozen_facts))
        object.__setattr__(self, "completion_facts", tuple(self.completion_facts))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "future_evidence_ids", tuple(self.future_evidence_ids))
        _unique_strings(self.completion_facts, "completion_facts", required=True)
        _unique_strings(self.future_evidence_ids, "future_evidence_ids")
        if self.fault_scenario is not None:
            object.__setattr__(
                self,
                "fault_scenario",
                _non_empty(self.fault_scenario, "fault_scenario"),
            )


@dataclass(frozen=True)
class FrozenBundle:
    task_id: str
    cutoff_at: datetime
    records: tuple[EvidenceRecord, ...]
    rejected_future_ids: tuple[str, ...]
    bundle_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _non_empty(self.task_id, "task_id"))
        if self.cutoff_at.tzinfo is None or self.cutoff_at.utcoffset() is None:
            raise ValueError("cutoff_at must include a timezone")
        object.__setattr__(self, "cutoff_at", self.cutoff_at.astimezone(UTC))
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "rejected_future_ids", tuple(self.rejected_future_ids))
        _unique_strings(self.rejected_future_ids, "rejected_future_ids")
        if not _SHA256.fullmatch(self.bundle_sha256):
            raise ValueError("bundle_sha256 must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: tuple[str, ...]
    fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _non_empty(self.text, "claim text"))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "fact_ids", tuple(self.fact_ids))
        _unique_strings(self.evidence_ids, "evidence_ids", required=True)
        _unique_strings(self.fact_ids, "fact_ids", required=True)


@dataclass(frozen=True)
class AgentAnswer:
    answer: str
    claims: tuple[Claim, ...]
    missing_information: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "answer", _non_empty(self.answer, "answer"))
        object.__setattr__(self, "claims", tuple(self.claims))
        object.__setattr__(self, "missing_information", tuple(self.missing_information))
        _unique_strings(self.missing_information, "missing_information")


@dataclass(frozen=True)
class HarnessConfig:
    context_policy: ContextPolicy = "pit_budgeted"
    context_budget_chars: int = 8000
    debate_policy: DebatePolicy = "conditional"
    debate_conflict_threshold: int = 1
    recovery_policy: RecoveryPolicy = "checkpoint"
    seed: int = 7

    def __post_init__(self) -> None:
        if self.context_policy not in {"raw", "pit_budgeted"}:
            raise ValueError("context_policy is invalid")
        if not isinstance(self.context_budget_chars, int) or self.context_budget_chars <= 0:
            raise ValueError("context_budget_chars must be a positive integer")
        if self.debate_policy not in {"always", "conditional"}:
            raise ValueError("debate_policy is invalid")
        if (
            not isinstance(self.debate_conflict_threshold, int)
            or self.debate_conflict_threshold < 0
        ):
            raise ValueError("debate_conflict_threshold must be a non-negative integer")
        if self.recovery_policy not in {"restart", "checkpoint"}:
            raise ValueError("recovery_policy is invalid")
        if not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")


def _validate_schema(name: str, value: Mapping[str, object]) -> None:
    schema_path = files("tracelane").joinpath("schemas", f"{name}.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    normalized = json.loads(canonical_json(value))
    errors = sorted(validator.iter_errors(normalized), key=lambda error: list(error.path))
    if not errors:
        return
    first = errors[0]
    location = ".".join(str(part) for part in first.path) or "$"
    raise ValueError(f"{name} schema validation failed at {location}: {first.message}")


def load_task(value: Mapping[str, object]) -> TaskSpec:
    _validate_schema("task", value)
    cutoff_at = parse_utc(str(value["cutoff_at"]))
    evidence = tuple(
        EvidenceRecord(
            evidence_id=str(item["evidence_id"]),
            available_at=parse_utc(str(item["available_at"])),
            source=str(item["source"]),
            text=str(item["text"]),
            fact_ids=tuple(str(fact_id) for fact_id in item["fact_ids"]),
        )
        for item in value["evidence"]
    )
    evidence_ids = [record.evidence_id for record in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("task contains duplicate evidence_id values")
    expected_facts = {str(fact_id): str(text) for fact_id, text in value["expected_facts"].items()}
    completion_facts = tuple(str(item) for item in value["completion_facts"])
    unknown_completion = set(completion_facts) - set(expected_facts)
    if unknown_completion:
        raise ValueError(f"completion_facts reference unknown facts: {sorted(unknown_completion)}")
    unknown_evidence_facts = {
        fact_id
        for record in evidence
        for fact_id in record.fact_ids
        if fact_id not in expected_facts
    }
    if unknown_evidence_facts:
        raise ValueError(f"evidence references unknown facts: {sorted(unknown_evidence_facts)}")
    declared_future = tuple(str(item) for item in value["future_evidence_ids"])
    computed_future = {record.evidence_id for record in evidence if record.available_at > cutoff_at}
    if set(declared_future) != computed_future:
        raise ValueError("future_evidence_ids must exactly match evidence available after cutoff")
    return TaskSpec(
        task_id=str(value["task_id"]),
        question=str(value["question"]),
        cutoff_at=cutoff_at,
        expected_facts=expected_facts,
        completion_facts=completion_facts,
        evidence=evidence,
        future_evidence_ids=declared_future,
        fault_scenario=(
            str(value["fault_scenario"]) if value["fault_scenario"] is not None else None
        ),
        license=str(value["license"]),
    )


def load_answer(value: Mapping[str, object]) -> AgentAnswer:
    _validate_schema("answer", value)
    claims = tuple(
        Claim(
            text=str(item["text"]),
            evidence_ids=tuple(str(evidence_id) for evidence_id in item["evidence_ids"]),
            fact_ids=tuple(str(fact_id) for fact_id in item["fact_ids"]),
        )
        for item in value["claims"]
    )
    return AgentAnswer(
        answer=str(value["answer"]),
        claims=claims,
        missing_information=tuple(str(item) for item in value["missing_information"]),
    )
