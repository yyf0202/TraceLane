from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

from tracelane.artifacts import RunStore
from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.security import classify_and_redact
from tracelane.v2.locking import exclusive_file_lock
from tracelane.v2.schema import validate_document
from tracelane.v2.storage import secure_open_append, secure_read_bytes, validate_open_file

_REGISTERED_EVENT_TYPES = (
    "run.started",
    "run.completed",
    "evidence.collected",
    "evidence.rejected",
    "context.selected",
    "plan.created",
    "model.called",
    "model.observed",
    "tool.called",
    "tool.observed",
    "claim.created",
    "assumption.created",
    "scenario.branched",
    "checkpoint.saved",
    "constraint.checked",
    "violation.detected",
    "stage.started",
    "stage.completed",
    "stage.failed",
    "answer.finalized",
    "grade.completed",
    "diagnosis.completed",
    "repair.proposed",
    "repair.approved",
    "replay.started",
    "replay.completed",
)
_EVENT_TYPES = frozenset(_REGISTERED_EVENT_TYPES)
_MODEL_TOKEN_METRICS = ("input_tokens", "output_tokens", "cached_tokens")
_RUN_ID = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SEMANTIC_PAYLOAD_KEYS = frozenset(
    {
        "tool_name",
        "runtime_id",
        "stage_id",
        "call_id",
        "error_code",
        "status",
    }
)


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _contains_configured_secret(value: object, secrets: Sequence[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_configured_secret(str(key), secrets)
            or _contains_configured_secret(item, secrets)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_configured_secret(item, secrets) for item in value)
    if isinstance(value, str):
        return any(secret and secret in value for secret in secrets)
    return False


def _is_semantic_payload_key(key: str) -> bool:
    return (
        key in _SEMANTIC_PAYLOAD_KEYS
        or key.endswith("_id")
        or key.endswith("_ids")
        or key.endswith("_sha256")
    )


def _redact_trace_payload(
    value: object,
    *,
    secrets: Sequence[str],
    semantic_key: str | None = None,
) -> object:
    if semantic_key is not None and _is_semantic_payload_key(semantic_key):
        if _contains_configured_secret(value, secrets):
            raise ValueError("trace payload contains restricted semantic identity")
        generic = classify_and_redact(value)
        if generic.redaction_applied and not (
            semantic_key.endswith("_sha256") and isinstance(value, str) and _SHA256.fullmatch(value)
        ):
            raise ValueError("trace payload contains restricted semantic identity")
        return json.loads(canonical_json(value))
    if isinstance(value, Mapping):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            original_key = str(key)
            baseline_key_probe = classify_and_redact({original_key: None})
            key_probe = classify_and_redact({original_key: None}, secrets=secrets)
            if (
                not isinstance(baseline_key_probe.value, Mapping)
                or len(baseline_key_probe.value) != 1
                or not isinstance(key_probe.value, Mapping)
                or len(key_probe.value) != 1
            ):
                raise ValueError("trace payload redaction is invalid")
            baseline_key = str(next(iter(baseline_key_probe.value)))
            sanitized_key = str(next(iter(key_probe.value)))
            if sanitized_key in redacted:
                raise ValueError("redacted mapping key collision")
            if baseline_key != original_key:
                redacted[sanitized_key] = "[REDACTED]"
            else:
                redacted[sanitized_key] = _redact_trace_payload(
                    item,
                    secrets=secrets,
                    semantic_key=original_key,
                )
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_redact_trace_payload(item, secrets=secrets) for item in value]
    return classify_and_redact(value, secrets=secrets).value


def registered_event_types() -> tuple[str, ...]:
    return _REGISTERED_EVENT_TYPES


def _trace_id(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]


def _span_id(run_id: str, sequence: int) -> str:
    return hashlib.sha256(f"{run_id}:{sequence}".encode()).hexdigest()[:16]


def event_content_sha256(value: Mapping[str, object]) -> str:
    projection = {
        str(key): item for key, item in value.items() if key not in {"event_id", "content_sha256"}
    }
    return sha256_json(projection)


def _event_id(content_sha256: str) -> str:
    return f"evt_{content_sha256}"


def _canonical_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))


def _validate_store(store: RunStore) -> tuple[Path, Path]:
    if not isinstance(store.run_id, str) or not _RUN_ID.fullmatch(store.run_id):
        raise ValueError("run_id must be a lowercase SHA-256 digest")

    artifact_root = Path(store.artifact_root)
    if _is_link_or_reparse(artifact_root):
        raise ValueError("trace artifact root must not be a link or reparse point")
    try:
        root_metadata = artifact_root.lstat()
        resolved_root = artifact_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError("trace artifact root is unavailable") from exc
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise ValueError("trace artifact root must be a directory")
    absolute_root = Path(os.path.abspath(artifact_root))
    if not _same_path(absolute_root, resolved_root):
        raise ValueError("trace artifact root must not contain a link or reparse point")

    expected_run_dir = resolved_root / "runs" / store.run_id
    supplied_run_dir = Path(os.path.abspath(store.run_dir))
    if not _same_path(supplied_run_dir, expected_run_dir):
        raise ValueError("trace run directory does not match the artifact root and run_id")

    runs_dir = resolved_root / "runs"
    if _is_link_or_reparse(runs_dir):
        raise ValueError("trace run directory must not contain a link or reparse point")
    if _is_link_or_reparse(expected_run_dir):
        raise ValueError("trace run directory must not be a link or reparse point")
    try:
        runs_metadata = runs_dir.lstat()
        run_metadata = expected_run_dir.lstat()
        resolved_run_dir = expected_run_dir.resolve(strict=True)
    except OSError as exc:
        raise ValueError("trace run directory is unavailable") from exc
    if not stat.S_ISDIR(runs_metadata.st_mode) or not stat.S_ISDIR(run_metadata.st_mode):
        raise ValueError("trace run directory must be a directory")
    if not _same_path(resolved_run_dir, expected_run_dir):
        raise ValueError("trace run directory must not contain a link or reparse point")
    try:
        resolved_run_dir.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("trace run directory escapes the artifact root") from exc
    return resolved_root, resolved_run_dir


def _trace_lock_path(artifact_root: Path, run_id: str) -> Path:
    lock_dir = artifact_root / ".locks"
    lock_path = lock_dir / f"{run_id}.trace.lock"
    try:
        lock_path.relative_to(artifact_root)
    except ValueError as exc:
        raise ValueError("trace lock path escapes the artifact root") from exc
    if lock_path.parent != lock_dir:
        raise ValueError("trace lock path escapes the artifact root")
    return lock_path


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str | None


@dataclass(frozen=True)
class TraceEventV2:
    schema_id: str
    schema_version: str
    event_id: str
    previous_event_sha256: str | None
    content_sha256: str
    sequence: int
    event_type: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    correlation_id: str | None
    causation_id: str | None
    run_id: str
    stage: str | None
    recorded_at: datetime
    attributes: Mapping[str, object]
    payload: Mapping[str, object]
    payload_classification: str
    redaction_applied: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> TraceEventV2:
        validate_document("trace-event", value)
        if type(value["sequence"]) is not int:
            raise ValueError("trace sequence must be an integer")
        recorded_at_value = value["recorded_at"]
        if not isinstance(recorded_at_value, str):
            raise ValueError("trace recorded_at must be a canonical UTC timestamp")
        recorded_at = parse_utc(recorded_at_value)
        if recorded_at_value != _canonical_utc(recorded_at):
            raise ValueError("trace recorded_at must use canonical UTC serialization")
        expected_content_sha256 = event_content_sha256(value)
        if value["content_sha256"] != expected_content_sha256:
            raise ValueError("trace event content hash is invalid")
        if value["event_id"] != _event_id(expected_content_sha256):
            raise ValueError("trace event identity is invalid")
        attributes = value["attributes"]
        if not isinstance(attributes, Mapping):
            raise ValueError("trace attributes must be a mapping")
        payload = value["payload"]
        if not isinstance(payload, Mapping):
            raise ValueError("trace payload must be a mapping")
        event = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            event_id=str(value["event_id"]),
            previous_event_sha256=(
                str(value["previous_event_sha256"])
                if value["previous_event_sha256"] is not None
                else None
            ),
            content_sha256=str(value["content_sha256"]),
            sequence=int(value["sequence"]),
            event_type=str(value["event_type"]),
            trace_id=str(value["trace_id"]),
            span_id=str(value["span_id"]),
            parent_span_id=(
                str(value["parent_span_id"]) if value["parent_span_id"] is not None else None
            ),
            correlation_id=(
                str(value["correlation_id"]) if value["correlation_id"] is not None else None
            ),
            causation_id=(
                str(value["causation_id"]) if value["causation_id"] is not None else None
            ),
            run_id=str(value["run_id"]),
            stage=str(value["stage"]) if value["stage"] is not None else None,
            recorded_at=recorded_at,
            attributes=MappingProxyType(json.loads(canonical_json(attributes))),
            payload=MappingProxyType(json.loads(canonical_json(payload))),
            payload_classification=str(value["payload_classification"]),
            redaction_applied=bool(value["redaction_applied"]),
        )
        return event

    def to_dict(self) -> dict[str, object]:
        value = json.loads(canonical_json(self))
        type(self).from_dict(value)
        return value


def _validate_trace_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_run_id: str | None,
) -> tuple[TraceEventV2, ...]:
    events: list[TraceEventV2] = []
    prior_event_ids: set[str] = set()
    prior_span_ids: set[str] = set()
    previous_sha256: str | None = None
    inferred_run_id = expected_run_id
    for sequence, row in enumerate(rows, start=1):
        event = TraceEventV2.from_dict(row)
        inferred_run_id = inferred_run_id or event.run_id
        if event.sequence != sequence:
            raise ValueError("trace sequence is not contiguous")
        if event.run_id != inferred_run_id:
            raise ValueError("trace run identity is inconsistent")
        if event.trace_id != _trace_id(inferred_run_id):
            raise ValueError("trace identity is invalid")
        if event.span_id != _span_id(inferred_run_id, sequence):
            raise ValueError("trace span identity is invalid")
        if event.previous_event_sha256 != previous_sha256:
            raise ValueError("trace event hash chain is invalid")
        if event.causation_id is not None and event.causation_id not in prior_event_ids:
            raise ValueError("trace causation reference is invalid")
        if event.parent_span_id is not None and event.parent_span_id not in prior_span_ids:
            raise ValueError("trace parent span reference is invalid")
        previous_sha256 = event.content_sha256
        prior_event_ids.add(event.event_id)
        prior_span_ids.add(event.span_id)
        events.append(event)
    return tuple(events)


def _read_trace_rows(
    path: Path,
    *,
    missing_ok: bool = False,
) -> list[dict[str, object]]:
    if not path.exists():
        if missing_ok:
            return []
        raise ValueError("trace contains invalid JSONL")
    try:
        data = secure_read_bytes(path, label="trace file")
        rows = _decode_trace_rows(data)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("trace contains invalid JSONL") from exc
    return rows


def _decode_trace_rows(data: bytes) -> list[dict[str, object]]:
    if data and not data.endswith(b"\n"):
        raise ValueError("trace contains invalid JSONL")
    try:
        lines = data.decode("utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("trace contains invalid JSONL") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("trace event must be a JSON object")
    return rows


class TraceRecorderV2:
    def __init__(
        self,
        store: RunStore,
        *,
        clock: Callable[[], datetime] | None = None,
        secrets: Sequence[str] = (),
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._secrets = tuple(secrets)
        artifact_root, run_dir = _validate_store(self._store)
        self._artifact_root = artifact_root
        self._run_dir = run_dir
        self._lock_path = _trace_lock_path(artifact_root, self.run_id)
        with exclusive_file_lock(self._lock_path):
            events = self._validate_history()
        self._tail_sequence = len(events)
        self._tail_content_sha256 = events[-1].content_sha256 if events else None

    @property
    def run_id(self) -> str:
        return self._store.run_id

    @property
    def store(self) -> RunStore:
        return self._store

    def _validate_history(self) -> tuple[TraceEventV2, ...]:
        path = self._store.path_for("trace/events.jsonl")
        return _validate_trace_rows(
            _read_trace_rows(path, missing_ok=True),
            expected_run_id=self.run_id,
        )

    def emit(
        self,
        event_type: str,
        payload: Mapping[str, object],
        *,
        stage: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> TraceEventV2:
        if event_type not in _EVENT_TYPES:
            raise ValueError("event_type is not registered")
        if not isinstance(payload, Mapping):
            raise ValueError("trace payload must be a mapping")
        if stage is not None:
            stage = _non_empty(stage, "stage")
        if correlation_id is not None:
            correlation_id = _non_empty(correlation_id, "correlation_id")
        if causation_id is not None:
            causation_id = _non_empty(causation_id, "causation_id")
        if parent_span_id is not None:
            parent_span_id = _non_empty(parent_span_id, "parent_span_id")
        if any(
            classify_and_redact(item).redaction_applied
            for item in (stage, correlation_id)
            if item is not None
        ):
            raise ValueError("trace event contains restricted metadata")
        recorded_at = self._clock()
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("trace clock must return a timezone-aware datetime")

        payload_for_redaction = dict(payload)
        protected_metrics: dict[str, object] = {}
        if event_type == "model.observed":
            protected_metrics = {
                name: payload_for_redaction.pop(name)
                for name in _MODEL_TOKEN_METRICS
                if name in payload_for_redaction
            }
        redacted_payload = _redact_trace_payload(
            payload_for_redaction,
            secrets=self._secrets,
        )
        redaction_applied = canonical_json(redacted_payload) != canonical_json(
            payload_for_redaction
        )
        payload_classification = "restricted" if redaction_applied else "internal"
        if protected_metrics:
            if not isinstance(redacted_payload, Mapping):
                raise ValueError("trace payload must be a mapping")
            redacted_payload = dict(redacted_payload) | protected_metrics
        trace_path = self._store.path_for("trace/events.jsonl")
        with exclusive_file_lock(self._lock_path):
            rows = _read_trace_rows(trace_path, missing_ok=True)
            events = _validate_trace_rows(rows, expected_run_id=self.run_id)
            current_tail_sequence = len(events)
            current_tail_sha256 = events[-1].content_sha256 if events else None
            if (
                current_tail_sequence != self._tail_sequence
                or current_tail_sha256 != self._tail_content_sha256
            ):
                raise ValueError("stale trace recorder")

            sequence = current_tail_sequence + 1
            value: dict[str, object] = {
                "schema_id": "tracelane://schemas/trace-event/v2",
                "schema_version": "2.0.0",
                "event_id": "",
                "previous_event_sha256": current_tail_sha256,
                "content_sha256": "",
                "sequence": sequence,
                "event_type": event_type,
                "trace_id": _trace_id(self.run_id),
                "span_id": _span_id(self.run_id, sequence),
                "parent_span_id": parent_span_id,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "run_id": self.run_id,
                "stage": stage,
                "recorded_at": _canonical_utc(recorded_at),
                "attributes": {},
                "payload": redacted_payload,
                "payload_classification": payload_classification,
                "redaction_applied": redaction_applied,
            }
            digest = event_content_sha256(value)
            value["content_sha256"] = digest
            value["event_id"] = _event_id(digest)
            event = _validate_trace_rows(
                [*rows, value],
                expected_run_id=self.run_id,
            )[-1]

            event_value = event.to_dict()
            if self._secrets:
                baseline = classify_and_redact(event_value)
                restricted = classify_and_redact(
                    event_value,
                    secrets=self._secrets,
                )
                if (
                    _contains_configured_secret(
                        event_value,
                        self._secrets,
                    )
                    or restricted.value != baseline.value
                ):
                    raise ValueError("trace event contains restricted metadata")
            serialized = (canonical_json(event_value) + "\n").encode("utf-8")
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            with secure_open_append(
                trace_path,
                root=self._artifact_root,
                label="trace file",
            ) as handle:
                handle.seek(0)
                append_rows = _decode_trace_rows(handle.read())
                append_events = _validate_trace_rows(
                    append_rows,
                    expected_run_id=self.run_id,
                )
                append_tail_sequence = len(append_events)
                append_tail_sha256 = append_events[-1].content_sha256 if append_events else None
                if (
                    append_tail_sequence != current_tail_sequence
                    or append_tail_sha256 != current_tail_sha256
                ):
                    raise ValueError("stale trace recorder")
                validate_open_file(
                    handle,
                    trace_path,
                    root=self._artifact_root,
                    label="trace file",
                )
                handle.seek(0, os.SEEK_END)
                validate_open_file(
                    handle,
                    trace_path,
                    root=self._artifact_root,
                    label="trace file",
                )
                written = handle.write(serialized)
                if written != len(serialized):
                    raise OSError("trace append was incomplete")
                handle.flush()
                os.fsync(handle.fileno())
                validate_open_file(
                    handle,
                    trace_path,
                    root=self._artifact_root,
                    label="trace file",
                )

            self._tail_sequence = sequence
            self._tail_content_sha256 = digest
            return event


def read_trace(
    path: str | Path,
    *,
    expected_run_id: str | None = None,
) -> tuple[TraceEventV2, ...]:
    return _validate_trace_rows(
        _read_trace_rows(Path(path)),
        expected_run_id=expected_run_id,
    )


def read_trace_bytes(
    data: bytes,
    *,
    expected_run_id: str | None = None,
) -> tuple[TraceEventV2, ...]:
    if not isinstance(data, bytes):
        raise ValueError("trace data must be bytes")
    return _validate_trace_rows(
        _decode_trace_rows(data),
        expected_run_id=expected_run_id,
    )
