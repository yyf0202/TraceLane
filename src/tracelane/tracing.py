from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType

from tracelane.artifacts import RunStore
from tracelane.contracts import canonical_json


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    normalized = json.loads(canonical_json(value))
    return MappingProxyType(normalized)


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class TraceEvent:
    sequence: int
    event_type: str
    stage: str | None
    run_id: str
    recorded_at: datetime
    payload: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "stage": self.stage,
            "run_id": self.run_id,
            "recorded_at": self.recorded_at,
            "payload": self.payload,
        }


class TraceRecorder:
    def __init__(
        self,
        store: RunStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._next_sequence = self._read_next_sequence()

    def _read_next_sequence(self) -> int:
        path = self._store.path_for("trace/events.jsonl")
        if not path.exists():
            return 1
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            rows = [json.loads(line) for line in lines]
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("trace contains invalid JSONL") from exc
        for expected_sequence, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                raise ValueError("trace event must be a JSON object")
            if row.get("sequence") != expected_sequence:
                raise ValueError("trace sequence is not contiguous")
            if row.get("run_id") != self._store.run_id:
                raise ValueError("trace run identity does not match the run directory")
        return len(rows) + 1

    def emit(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        stage: str | None = None,
    ) -> TraceEvent:
        event_type = _non_empty(event_type, "event_type")
        if stage is not None:
            stage = _non_empty(stage, "stage")
        recorded_at = self._clock()
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("trace clock must return a timezone-aware datetime")
        event = TraceEvent(
            sequence=self._next_sequence,
            event_type=event_type,
            stage=stage,
            run_id=self._store.run_id,
            recorded_at=recorded_at.astimezone(UTC),
            payload=_freeze_mapping(payload),
        )
        self._store.append_jsonl("trace/events.jsonl", event.to_dict())
        self._next_sequence += 1
        return event
