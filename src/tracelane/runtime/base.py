from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol

from tracelane.contracts import EvidenceRecord, canonical_json


def freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    normalized = json.loads(canonical_json(value))
    return MappingProxyType(normalized)


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _non_negative(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class ModelRequest:
    run_id: str
    stage: str
    role: str
    question: str
    evidence: tuple[EvidenceRecord, ...]
    prior_output: Mapping[str, object]
    seed: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _non_empty(self.run_id, "run_id"))
        object.__setattr__(self, "stage", _non_empty(self.stage, "stage"))
        object.__setattr__(self, "role", _non_empty(self.role, "role"))
        object.__setattr__(self, "question", _non_empty(self.question, "question"))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not all(isinstance(record, EvidenceRecord) for record in self.evidence):
            raise ValueError("evidence must contain EvidenceRecord values")
        object.__setattr__(self, "prior_output", freeze_mapping(self.prior_output))
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")


@dataclass(frozen=True)
class ModelResponse:
    content: Mapping[str, object]
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int
    attempt: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", freeze_mapping(self.content))
        object.__setattr__(
            self,
            "input_tokens",
            _non_negative(self.input_tokens, "input_tokens"),
        )
        object.__setattr__(
            self,
            "output_tokens",
            _non_negative(self.output_tokens, "output_tokens"),
        )
        object.__setattr__(
            self,
            "cached_tokens",
            _non_negative(self.cached_tokens, "cached_tokens"),
        )
        object.__setattr__(self, "latency_ms", _non_negative(self.latency_ms, "latency_ms"))
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")


class ModelRuntime(Protocol):
    @property
    def model_id(self) -> str: ...

    def complete(self, request: ModelRequest) -> ModelResponse: ...
