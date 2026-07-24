from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracelane.artifacts import RunStore
from tracelane.contracts import canonical_json, sha256_json
from tracelane.v2 import locking as locking_module
from tracelane.v2 import tracing as tracing_module
from tracelane.v2.locking import exclusive_file_lock
from tracelane.v2.schema import SchemaValidationError, validate_document
from tracelane.v2.tracing import (
    TraceContext,
    TraceEventV2,
    TraceRecorderV2,
    event_content_sha256,
    read_trace,
)

RUN_ID = "1" * 64
RUN_STARTED = {"status": "running"}
RUN_COMPLETED = {"status": "completed"}
STAGE_STARTED = {"stage_id": "research"}
STAGE_COMPLETED = {"stage_id": "research"}
TOOL_CALLED = {
    "call_id": "call_001",
    "tool_name": "read_evidence",
    "arguments": {"query": "Napoleon"},
}
TOOL_OBSERVED = {
    "call_id": "call_001",
    "tool_name": "read_evidence",
    "output": {"result": "ok"},
    "is_error": False,
    "error_code": None,
}
VALID_EVENT_PAYLOADS: dict[str, dict[str, object]] = {
    "model.called": {"turn": 0, "runtime_id": "runtime_001"},
    "model.observed": {
        "turn": 0,
        "tool_call_count": 0,
        "has_output": True,
        "input_tokens": 1,
        "output_tokens": 1,
        "cached_tokens": 0,
        "latency_ms": 1.0,
    },
    "tool.called": TOOL_CALLED,
    "tool.observed": TOOL_OBSERVED,
    "run.started": RUN_STARTED,
    "run.completed": RUN_COMPLETED,
    "stage.started": STAGE_STARTED,
    "stage.completed": STAGE_COMPLETED,
    "stage.failed": {"stage_id": "research", "error_code": "timeout"},
}
TRACE_CORRUPTIONS = (
    "event_edit",
    "event_delete",
    "event_insert",
    "event_reorder",
    "suffix_truncation",
    "broken_causation",
    "broken_parent_span",
    "invalid_payload",
    "stale_writer",
)
TRACE_CORRUPTION_ERRORS: dict[str, type[ValueError]] = {
    corruption: ValueError for corruption in TRACE_CORRUPTIONS
}
TRACE_CORRUPTION_ERRORS["invalid_payload"] = SchemaValidationError


class IncrementingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 24, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def recorder_v2(root: Path) -> TraceRecorderV2:
    return TraceRecorderV2(
        RunStore.create(root, RUN_ID),
        clock=IncrementingClock(),
    )


def rows(root: Path) -> list[dict[str, object]]:
    path = trace_path(root)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def trace_path(root: Path) -> Path:
    return root / "runs" / RUN_ID / "trace" / "events.jsonl"


def write_three_event_trace(root: Path) -> Path:
    recorder = recorder_v2(root)
    started = recorder.emit("run.started", RUN_STARTED)
    stage = recorder.emit(
        "stage.started",
        STAGE_STARTED,
        causation_id=started.event_id,
        parent_span_id=started.span_id,
    )
    recorder.emit(
        "stage.completed",
        STAGE_COMPLETED,
        causation_id=stage.event_id,
        parent_span_id=stage.span_id,
    )
    return trace_path(root)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(canonical_json(value) + "\n" for value in values),
        encoding="utf-8",
    )


def content_digest(value: dict[str, object]) -> str:
    return sha256_json(
        {key: item for key, item in value.items() if key not in {"event_id", "content_sha256"}}
    )


def rehash(value: dict[str, object]) -> None:
    digest = content_digest(value)
    value["content_sha256"] = digest
    value["event_id"] = f"evt_{digest}"


def mutate_rows(
    values: list[dict[str, object]],
    mutation: str,
) -> list[dict[str, object]]:
    changed = deepcopy(values)
    if mutation == "edit":
        payload = changed[1]["payload"]
        assert isinstance(payload, dict)
        payload["stage_id"] = "changed"
    elif mutation == "delete":
        del changed[1]
    elif mutation == "insert":
        changed.insert(1, deepcopy(changed[0]))
    elif mutation == "reorder":
        changed[1], changed[2] = changed[2], changed[1]
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    return changed


def test_trace_event_has_stable_causation_span_and_redaction_fields(tmp_path: Path) -> None:
    recorder = recorder_v2(tmp_path)

    called = recorder.emit(
        "tool.called",
        TOOL_CALLED | {"authorization": "Bearer secret"},
        stage="research",
        correlation_id="call_001",
    )
    observed = recorder.emit(
        "tool.observed",
        TOOL_OBSERVED,
        stage="research",
        correlation_id="call_001",
        causation_id=called.event_id,
        parent_span_id=called.span_id,
    )

    assert observed.sequence == 2
    assert observed.causation_id == called.event_id
    assert observed.parent_span_id == called.span_id
    assert called.trace_id == observed.trace_id
    assert (
        TraceContext(
            trace_id=observed.trace_id,
            span_id=observed.span_id,
            parent_span_id=observed.parent_span_id,
        ).parent_span_id
        == called.span_id
    )
    first_row = rows(tmp_path)[0]
    assert first_row["payload"]["[REDACTED]"] == "[REDACTED]"
    assert first_row["payload"]["arguments"] == {"query": "Napoleon"}
    assert first_row["payload_classification"] == "restricted"
    assert first_row["redaction_applied"] is True
    assert first_row["previous_event_sha256"] is None
    assert len(first_row["content_sha256"]) == 64
    assert first_row["event_id"] == f"evt_{first_row['content_sha256']}"


def test_trace_rejects_unknown_event_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="event_type"):
        recorder_v2(tmp_path).emit("anything.happened", {})


def test_reopened_trace_validates_history_and_continues_sequence(tmp_path: Path) -> None:
    first = recorder_v2(tmp_path).emit("run.started", RUN_STARTED)

    reopened = recorder_v2(tmp_path)
    second = reopened.emit(
        "stage.started",
        STAGE_STARTED,
        causation_id=first.event_id,
        parent_span_id=first.span_id,
    )

    assert second.sequence == 2
    assert len(rows(tmp_path)) == 2


def test_trace_rejects_naive_clock(tmp_path: Path) -> None:
    recorder = TraceRecorderV2(
        RunStore.create(tmp_path, RUN_ID),
        clock=lambda: datetime(2026, 7, 24),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        recorder.emit("run.started", RUN_STARTED)


@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [(item, TRACE_CORRUPTION_ERRORS[item]) for item in TRACE_CORRUPTIONS],
)
def test_public_trace_surfaces_reject_adversarial_matrix(
    tmp_path: Path,
    corruption: str,
    expected_error: type[ValueError],
) -> None:
    if corruption == "stale_writer":
        store = RunStore.create(tmp_path, RUN_ID)
        first = TraceRecorderV2(store, clock=IncrementingClock())
        stale = TraceRecorderV2(store, clock=IncrementingClock())
        first.emit("run.started", RUN_STARTED)
        original = trace_path(tmp_path).read_bytes()

        with pytest.raises(expected_error) as captured:
            stale.emit("run.started", RUN_STARTED)
        assert trace_path(tmp_path).read_bytes() == original
    elif corruption == "suffix_truncation":
        recorder = recorder_v2(tmp_path)
        started = recorder.emit("run.started", RUN_STARTED)
        stage = recorder.emit(
            "stage.started",
            STAGE_STARTED,
            causation_id=started.event_id,
            parent_span_id=started.span_id,
        )
        recorder.emit(
            "stage.completed",
            STAGE_COMPLETED,
            causation_id=stage.event_id,
            parent_span_id=stage.span_id,
        )
        path = trace_path(tmp_path)
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:-1]))
        truncated = path.read_bytes()

        with pytest.raises(expected_error) as captured:
            recorder.emit("run.completed", RUN_COMPLETED)
        assert path.read_bytes() == truncated
    else:
        path = write_three_event_trace(tmp_path)
        values = read_jsonl(path)
        if corruption.startswith("event_"):
            mutation = corruption.removeprefix("event_")
            values = mutate_rows(values, mutation)
        elif corruption == "broken_causation":
            values[-1]["causation_id"] = "evt_" + "f" * 64
            rehash(values[-1])
        elif corruption == "broken_parent_span":
            values[-1]["parent_span_id"] = "f" * 16
            rehash(values[-1])
        elif corruption == "invalid_payload":
            payload = values[-1]["payload"]
            assert isinstance(payload, dict)
            payload["stage_id"] = ""
            rehash(values[-1])
        else:
            raise AssertionError(f"unknown corruption: {corruption}")
        write_jsonl(path, values)

        with pytest.raises(expected_error) as captured:
            read_trace(path, expected_run_id=RUN_ID)

    assert type(captured.value) is expected_error


def test_read_trace_rejects_explicitly_broken_previous_hash(tmp_path: Path) -> None:
    path = write_three_event_trace(tmp_path)
    values = read_jsonl(path)
    values[1]["previous_event_sha256"] = "f" * 64
    rehash(values[1])
    write_jsonl(path, values)

    with pytest.raises(ValueError, match="hash chain"):
        read_trace(path, expected_run_id=RUN_ID)


@pytest.mark.parametrize("reader", ["public", "reopen"])
def test_trace_rejects_rehashed_middle_event_with_stale_successor_link(
    tmp_path: Path,
    reader: str,
) -> None:
    path = write_three_event_trace(tmp_path)
    values = read_jsonl(path)
    payload = values[1]["payload"]
    assert isinstance(payload, dict)
    payload["stage_id"] = "changed"
    rehash(values[1])
    write_jsonl(path, values)

    with pytest.raises(ValueError, match="hash chain"):
        if reader == "public":
            read_trace(path, expected_run_id=RUN_ID)
        else:
            recorder_v2(tmp_path)


def test_registered_event_types_match_wire_schema_exactly() -> None:
    schema_path = (
        Path(__file__).parents[2]
        / "src"
        / "tracelane"
        / "schemas"
        / "v2"
        / "trace-event.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    event_types = schema["properties"]["event_type"]["enum"]

    assert tracing_module.registered_event_types() == tuple(event_types)


@pytest.mark.parametrize("event_type", tracing_module.registered_event_types())
def test_every_registered_event_rejects_scalar_payload_in_schema_python_and_reader(
    tmp_path: Path,
    event_type: str,
) -> None:
    root = tmp_path / event_type.replace(".", "-")
    event = recorder_v2(root).emit(event_type, VALID_EVENT_PAYLOADS.get(event_type, {})).to_dict()
    event["payload"] = 7
    rehash(event)

    with pytest.raises(SchemaValidationError):
        validate_document("trace-event", event)
    with pytest.raises(SchemaValidationError):
        TraceEventV2.from_dict(event)

    path = root / "scalar.jsonl"
    write_jsonl(path, [event])
    with pytest.raises(SchemaValidationError):
        read_trace(path, expected_run_id=RUN_ID)


@pytest.mark.parametrize("event_type", tracing_module.registered_event_types())
def test_every_registered_event_mapping_payload_has_schema_python_reader_parity(
    tmp_path: Path,
    event_type: str,
) -> None:
    root = tmp_path / event_type.replace(".", "-")
    event = recorder_v2(root).emit(event_type, VALID_EVENT_PAYLOADS.get(event_type, {})).to_dict()

    validate_document("trace-event", event)
    assert TraceEventV2.from_dict(event).to_dict() == event
    assert read_trace(trace_path(root), expected_run_id=RUN_ID)[0].to_dict() == event


def test_emit_rejects_scalar_payload_with_stable_value_error_before_persistence(
    tmp_path: Path,
) -> None:
    recorder = recorder_v2(tmp_path)

    with pytest.raises(ValueError, match="trace payload must be a mapping"):
        recorder.emit("claim.created", 7)  # type: ignore[arg-type]

    assert not trace_path(tmp_path).exists()


@pytest.mark.parametrize(
    ("event_type", "payload", "missing"),
    [
        ("model.called", {"turn": 0, "runtime_id": "runtime_001"}, "runtime_id"),
        (
            "model.observed",
            {
                "turn": 0,
                "tool_call_count": 1,
                "has_output": True,
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_tokens": 2,
                "latency_ms": 25.5,
            },
            "latency_ms",
        ),
        ("tool.called", TOOL_CALLED, "call_id"),
        ("tool.observed", TOOL_OBSERVED, "error_code"),
        ("run.started", RUN_STARTED, "status"),
        ("run.completed", RUN_COMPLETED, "status"),
        ("stage.started", STAGE_STARTED, "stage_id"),
        ("stage.completed", STAGE_COMPLETED, "stage_id"),
        (
            "stage.failed",
            {"stage_id": "research", "error_code": "timeout"},
            "error_code",
        ),
    ],
)
def test_trace_event_requires_payload_contract(
    tmp_path: Path,
    event_type: str,
    payload: dict[str, object],
    missing: str,
) -> None:
    invalid = dict(payload)
    invalid.pop(missing)

    with pytest.raises(ValueError, match=missing):
        recorder_v2(tmp_path).emit(event_type, invalid)

    assert not trace_path(tmp_path).exists()


@pytest.mark.parametrize(
    ("event_type", "payload", "message"),
    [
        ("model.called", {"turn": -1, "runtime_id": "runtime_001"}, "turn"),
        (
            "model.observed",
            {
                "turn": 0,
                "tool_call_count": 1,
                "has_output": True,
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_tokens": 2,
                "latency_ms": -0.1,
            },
            "latency_ms",
        ),
        (
            "tool.called",
            {"call_id": "call_001", "tool_name": "read_evidence", "arguments": []},
            "arguments",
        ),
        ("tool.observed", TOOL_OBSERVED | {"is_error": "false"}, "is_error"),
        ("run.started", {"status": "started"}, "running"),
        ("run.completed", {"status": "done"}, "completed"),
        ("stage.started", {"stage_id": ""}, "stage_id"),
        ("stage.failed", {"stage_id": "research", "error_code": ""}, "error_code"),
    ],
)
def test_trace_event_rejects_invalid_payload_values(
    tmp_path: Path,
    event_type: str,
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        recorder_v2(tmp_path).emit(event_type, payload)

    assert not trace_path(tmp_path).exists()


@pytest.mark.parametrize(
    "reference",
    [
        {"causation_id": "evt_" + "f" * 64},
        {"parent_span_id": "f" * 16},
    ],
)
def test_emit_rejects_invalid_live_reference_before_append(
    tmp_path: Path,
    reference: dict[str, str],
) -> None:
    recorder = recorder_v2(tmp_path)
    recorder.emit("run.started", RUN_STARTED)
    path = trace_path(tmp_path)
    original = path.read_bytes()

    with pytest.raises(ValueError, match="causation|parent span"):
        recorder.emit("stage.started", STAGE_STARTED, **reference)

    assert path.read_bytes() == original


def test_exclusive_trace_lock_is_non_blocking(tmp_path: Path) -> None:
    lock_path = tmp_path / ".locks" / f"{RUN_ID}.trace.lock"

    with (
        exclusive_file_lock(lock_path),
        pytest.raises(ValueError, match="lock is unavailable"),
        exclusive_file_lock(lock_path),
    ):
        pass


def test_trace_lock_normalizes_post_open_disappearance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / ".locks" / f"{RUN_ID}.trace.lock"
    original_lstat = Path.lstat
    lock_lstat_calls = 0

    def disappear_after_open(path: Path, *args: object, **kwargs: object):
        nonlocal lock_lstat_calls
        if path == lock_path:
            lock_lstat_calls += 1
            if lock_lstat_calls == 2:
                raise FileNotFoundError(str(path))
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", disappear_after_open)

    with (
        pytest.raises(ValueError, match="lock file.*unavailable"),
        exclusive_file_lock(lock_path),
    ):
        pass


def test_trace_lock_revalidates_path_identity_after_os_acquisition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / ".locks" / f"{RUN_ID}.trace.lock"
    original_lstat = Path.lstat
    acquired = False
    entered = False

    if os.name == "nt":
        import msvcrt

        original_locking = msvcrt.locking

        def acquire_then_mutate(
            descriptor: int,
            mode: int,
            length: int,
        ) -> None:
            nonlocal acquired
            original_locking(descriptor, mode, length)
            if mode != msvcrt.LK_UNLCK:
                acquired = True

        monkeypatch.setattr(msvcrt, "locking", acquire_then_mutate)
    else:
        import fcntl

        original_flock = fcntl.flock

        def acquire_then_mutate(descriptor: int, operation: int) -> None:
            nonlocal acquired
            original_flock(descriptor, operation)
            if operation & fcntl.LOCK_UN == 0:
                acquired = True

        monkeypatch.setattr(fcntl, "flock", acquire_then_mutate)

    def replaced_after_acquisition(path: Path, *args: object, **kwargs: object):
        metadata = original_lstat(path, *args, **kwargs)
        if path == lock_path and acquired:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=getattr(metadata, "st_file_attributes", 0),
                st_nlink=metadata.st_nlink,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino + 1,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", replaced_after_acquisition)

    with (
        pytest.raises(ValueError, match="lock file identity is invalid"),
        locking_module.exclusive_file_lock(lock_path),
    ):
        entered = True

    assert not entered


def test_trace_lock_revalidates_path_identity_before_unlock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / ".locks" / f"{RUN_ID}.trace.lock"
    original_lstat = Path.lstat
    replaced = False

    def replaced_before_unlock(path: Path, *args: object, **kwargs: object):
        metadata = original_lstat(path, *args, **kwargs)
        if path == lock_path and replaced:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=getattr(metadata, "st_file_attributes", 0),
                st_nlink=metadata.st_nlink,
                st_dev=metadata.st_dev,
                st_ino=metadata.st_ino + 1,
            )
        return metadata

    monkeypatch.setattr(Path, "lstat", replaced_before_unlock)

    with (
        pytest.raises(ValueError, match="lock file identity is invalid"),
        locking_module.exclusive_file_lock(lock_path),
    ):
        replaced = True


def test_trace_lock_sidecar_is_outside_run_directory(tmp_path: Path) -> None:
    recorder_v2(tmp_path).emit("run.started", RUN_STARTED)

    assert (tmp_path / ".locks" / f"{RUN_ID}.trace.lock").is_file()
    assert not (tmp_path / "runs" / RUN_ID / ".locks").exists()


@pytest.mark.parametrize("syntax", ["traversal", "absolute"])
def test_recorder_rejects_forged_run_id_before_filesystem_creation(
    tmp_path: Path,
    syntax: str,
) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    if syntax == "traversal":
        forged_run_id = "../escaped"
        escaped_lock = artifact_root / "escaped.trace.lock"
    else:
        outside = tmp_path / "outside-lock"
        forged_run_id = str(outside.resolve())
        escaped_lock = outside.with_name(f"{outside.name}.trace.lock")
    store = RunStore(
        artifact_root=artifact_root,
        run_id=forged_run_id,
        run_dir=artifact_root / "runs" / RUN_ID,
    )

    with pytest.raises(ValueError, match="run_id"):
        TraceRecorderV2(store)

    assert not (artifact_root / ".locks").exists()
    assert not escaped_lock.exists()


def test_recorder_rejects_missing_artifact_root_before_creation(tmp_path: Path) -> None:
    artifact_root = tmp_path / "missing-root"
    store = RunStore(
        artifact_root=artifact_root,
        run_id=RUN_ID,
        run_dir=artifact_root / "runs" / RUN_ID,
    )

    with pytest.raises(ValueError, match="artifact root"):
        TraceRecorderV2(store)

    assert not artifact_root.exists()


def test_recorder_rejects_forged_run_dir_before_lock_creation(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside_run_dir = tmp_path / "outside-run"
    store = RunStore(
        artifact_root=artifact_root,
        run_id=RUN_ID,
        run_dir=outside_run_dir,
    )

    with pytest.raises(ValueError, match="run directory"):
        TraceRecorderV2(store)

    assert not outside_run_dir.exists()
    assert not (artifact_root / ".locks").exists()


def test_recorder_rejects_linked_artifact_root_before_lock_creation(
    tmp_path: Path,
) -> None:
    real_root = tmp_path / "real-root"
    RunStore.create(real_root, RUN_ID)
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable on this host")
    store = RunStore(
        artifact_root=linked_root,
        run_id=RUN_ID,
        run_dir=linked_root / "runs" / RUN_ID,
    )

    with pytest.raises(ValueError, match="artifact root.*link|artifact root.*reparse"):
        TraceRecorderV2(store)

    assert not (real_root / ".locks").exists()


def test_recorder_rejects_linked_run_dir_before_lock_creation(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    runs_dir = artifact_root / "runs"
    runs_dir.mkdir(parents=True)
    outside_run_dir = tmp_path / "outside-run"
    outside_run_dir.mkdir()
    linked_run_dir = runs_dir / RUN_ID
    try:
        linked_run_dir.symlink_to(outside_run_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable on this host")
    store = RunStore(
        artifact_root=artifact_root,
        run_id=RUN_ID,
        run_dir=linked_run_dir,
    )

    with pytest.raises(ValueError, match="run directory.*link|run directory.*reparse"):
        TraceRecorderV2(store)

    assert not (artifact_root / ".locks").exists()
    assert list(outside_run_dir.iterdir()) == []


def test_recorder_rejects_linked_lock_directory_without_outside_write(
    tmp_path: Path,
) -> None:
    store = RunStore.create(tmp_path / "artifacts", RUN_ID)
    outside = tmp_path / "outside-locks"
    outside.mkdir()
    lock_dir = store.artifact_root / ".locks"
    try:
        lock_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory links are unavailable on this host")

    with pytest.raises(ValueError, match="lock directory.*link|lock directory.*reparse"):
        TraceRecorderV2(store)

    assert list(outside.iterdir()) == []


def test_recorder_rejects_linked_lock_file_without_outside_write(
    tmp_path: Path,
) -> None:
    store = RunStore.create(tmp_path / "artifacts", RUN_ID)
    lock_dir = store.artifact_root / ".locks"
    lock_dir.mkdir()
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"sentinel")
    lock_path = lock_dir / f"{RUN_ID}.trace.lock"
    try:
        lock_path.symlink_to(outside)
    except OSError:
        pytest.skip("file links are unavailable on this host")

    with pytest.raises(ValueError, match="lock file.*link|lock file.*reparse"):
        TraceRecorderV2(store)

    assert outside.read_bytes() == b"sentinel"


def test_recorder_rejects_non_regular_lock_target(tmp_path: Path) -> None:
    store = RunStore.create(tmp_path / "artifacts", RUN_ID)
    lock_path = store.artifact_root / ".locks" / f"{RUN_ID}.trace.lock"
    lock_path.mkdir(parents=True)

    with pytest.raises(ValueError, match="lock file.*regular"):
        TraceRecorderV2(store)


def test_recorder_rejects_hard_linked_trace_without_outside_write(tmp_path: Path) -> None:
    store = RunStore.create(tmp_path / "artifacts", RUN_ID)
    trace = store.run_dir / "trace" / "events.jsonl"
    trace.parent.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"")
    try:
        os.link(outside, trace)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="trace.*link"):
        TraceRecorderV2(store)

    assert outside.read_bytes() == b""


def test_emit_fails_closed_when_trace_is_hard_linked_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RunStore.create(tmp_path / "artifacts", RUN_ID)
    recorder = TraceRecorderV2(store, clock=IncrementingClock())
    trace = trace_path(store.artifact_root)
    outside = tmp_path / "outside.jsonl"
    original_validate = tracing_module.validate_open_file
    linked = False

    def link_after_validation(*args: object, **kwargs: object):
        nonlocal linked
        metadata = original_validate(*args, **kwargs)
        if not linked:
            linked = True
            os.link(trace, outside)
        return metadata

    monkeypatch.setattr(tracing_module, "validate_open_file", link_after_validation)

    with pytest.raises(ValueError, match="trace.*link|trace.*identity"):
        recorder.emit("run.started", RUN_STARTED)

    assert outside.read_bytes() == b""


def test_emit_revalidates_trace_identity_after_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RunStore.create(tmp_path / "artifacts", RUN_ID)
    recorder = TraceRecorderV2(store, clock=IncrementingClock())
    trace = trace_path(store.artifact_root)
    outside = tmp_path / "outside-after-append.jsonl"
    original_validate = tracing_module.validate_open_file
    validation_calls = 0

    def link_on_post_write_validation(*args: object, **kwargs: object):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 3:
            os.link(trace, outside)
        return original_validate(*args, **kwargs)

    monkeypatch.setattr(
        tracing_module,
        "validate_open_file",
        link_on_post_write_validation,
    )

    with pytest.raises(ValueError, match="trace.*link|trace.*identity"):
        recorder.emit("run.started", RUN_STARTED)

    assert outside.exists()
    assert outside.read_bytes() == trace.read_bytes()


def test_reopened_trace_rejects_partial_final_jsonl_record(tmp_path: Path) -> None:
    path = write_three_event_trace(tmp_path)
    value = path.read_bytes()
    assert value.endswith(b"\n")
    path.write_bytes(value[:-1])

    with pytest.raises(ValueError, match="invalid JSONL"):
        recorder_v2(tmp_path)
    with pytest.raises(ValueError, match="invalid JSONL"):
        read_trace(path, expected_run_id=RUN_ID)


def test_read_trace_enforces_expected_run_id(tmp_path: Path) -> None:
    path = write_three_event_trace(tmp_path)

    assert len(read_trace(path)) == 3
    with pytest.raises(ValueError, match="run identity"):
        read_trace(path, expected_run_id="2" * 64)


def test_read_trace_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid JSONL"):
        read_trace(tmp_path / "missing.jsonl", expected_run_id=RUN_ID)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("sequence", 1.0, "sequence"),
        ("recorded_at", "2026-07-24T00:00:00.1Z", "recorded_at"),
    ],
)
def test_trace_event_from_dict_rejects_noncanonical_wire_value(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    recorder_v2(tmp_path).emit("run.started", RUN_STARTED)
    value = rows(tmp_path)[0]
    value[field] = replacement
    rehash(value)

    with pytest.raises(ValueError, match=message):
        TraceEventV2.from_dict(value)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("sequence", 1.0, "sequence"),
        ("recorded_at", "2026-07-24T00:00:00.1Z", "recorded_at"),
    ],
)
def test_read_trace_rejects_noncanonical_wire_value(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    path = trace_path(tmp_path)
    recorder_v2(tmp_path).emit("run.started", RUN_STARTED)
    value = rows(tmp_path)[0]
    value[field] = replacement
    rehash(value)
    write_jsonl(path, [value])

    with pytest.raises(ValueError, match=message):
        read_trace(path, expected_run_id=RUN_ID)


def test_canonical_fractional_timestamp_round_trips_with_same_hash(
    tmp_path: Path,
) -> None:
    recorder = TraceRecorderV2(
        RunStore.create(tmp_path, RUN_ID),
        clock=lambda: datetime(2026, 7, 24, 0, 0, 0, 100000, tzinfo=UTC),
    )
    event = recorder.emit("run.started", RUN_STARTED)
    value = event.to_dict()

    assert value["recorded_at"] == "2026-07-24T00:00:00.100000Z"
    assert event_content_sha256(value) == event.content_sha256
    assert TraceEventV2.from_dict(value).to_dict() == value


def test_trace_schema_rejects_fractional_timestamp_without_six_digits(
    tmp_path: Path,
) -> None:
    recorder_v2(tmp_path).emit("run.started", RUN_STARTED)
    value = rows(tmp_path)[0]
    value["recorded_at"] = "2026-07-24T00:00:00.1Z"
    rehash(value)

    with pytest.raises(SchemaValidationError, match="recorded_at"):
        validate_document("trace-event", value)


def test_configured_secret_and_sensitive_mapping_key_never_reach_trace(
    tmp_path: Path,
) -> None:
    configured = "private-runtime-value"
    supplied_secrets = [configured]
    recorder = TraceRecorderV2(
        RunStore.create(tmp_path, RUN_ID),
        clock=IncrementingClock(),
        secrets=supplied_secrets,
    )
    supplied_secrets[0] = "changed-after-construction"

    recorder.emit(
        "claim.created",
        {
            f"key-{configured}": "visible",
            "note": f"contains {configured}",
        },
    )

    persisted = rows(tmp_path)[0]["payload"]
    assert persisted == {
        "[REDACTED]": "visible",
        "note": "contains [REDACTED]",
    }
    assert configured not in canonical_json(rows(tmp_path)[0])


@pytest.mark.parametrize("metadata_field", ["stage", "correlation_id"])
def test_sensitive_trace_metadata_is_rejected_before_persistence(
    tmp_path: Path,
    metadata_field: str,
) -> None:
    configured = "private-runtime-value"
    recorder = TraceRecorderV2(
        RunStore.create(tmp_path, RUN_ID),
        clock=IncrementingClock(),
        secrets=(configured,),
    )

    with pytest.raises(ValueError, match="trace event contains restricted metadata"):
        recorder.emit(
            "claim.created",
            {},
            **{metadata_field: f"prefix-{configured}"},  # type: ignore[arg-type]
        )

    assert not trace_path(tmp_path).exists()


@pytest.mark.parametrize(
    "structural_field",
    [
        "schema_id",
        "schema_version",
        "event_id",
        "previous_event_sha256",
        "content_sha256",
        "event_type",
        "trace_id",
        "span_id",
        "parent_span_id",
        "correlation_id",
        "causation_id",
        "run_id",
        "stage",
        "recorded_at",
        "payload_classification",
    ],
)
def test_configured_secret_in_structural_trace_field_rejects_before_append(
    tmp_path: Path,
    structural_field: str,
) -> None:
    control = TraceRecorderV2(
        RunStore.create(tmp_path / "control", RUN_ID),
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
    )
    control_started = control.emit("run.started", RUN_STARTED)
    prospective = control.emit(
        "claim.created",
        {},
        stage="structural-stage",
        correlation_id="structural-correlation",
        causation_id=control_started.event_id,
        parent_span_id=control_started.span_id,
    )
    configured_secret = prospective.to_dict()[structural_field]
    assert isinstance(configured_secret, str)

    artifact_root = tmp_path / "actual"
    store = RunStore.create(artifact_root, RUN_ID)
    initial = TraceRecorderV2(
        store,
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
    )
    started = initial.emit("run.started", RUN_STARTED)
    before = trace_path(artifact_root).read_bytes()
    recorder = TraceRecorderV2(
        store,
        clock=lambda: datetime(2026, 7, 24, tzinfo=UTC),
        secrets=(configured_secret,),
    )

    with pytest.raises(
        ValueError,
        match="trace event contains restricted metadata",
    ):
        recorder.emit(
            "claim.created",
            {},
            stage="structural-stage",
            correlation_id="structural-correlation",
            causation_id=started.event_id,
            parent_span_id=started.span_id,
        )

    assert trace_path(artifact_root).read_bytes() == before


def test_configured_api_key_secret_in_structural_field_rejects_before_append(
    tmp_path: Path,
) -> None:
    configured_secret = "sk-" + "abcdefghijklmnop"
    artifact_root = tmp_path / "actual"
    store = RunStore.create(artifact_root, RUN_ID)
    initial = TraceRecorderV2(store, clock=IncrementingClock())
    initial.emit("run.started", RUN_STARTED)
    before = trace_path(artifact_root).read_bytes()
    recorder = TraceRecorderV2(
        store,
        clock=IncrementingClock(),
        secrets=(configured_secret,),
    )

    with pytest.raises(
        ValueError,
        match="trace event contains restricted metadata",
    ):
        recorder.emit(
            "claim.created",
            {},
            stage=configured_secret,
        )

    assert trace_path(artifact_root).read_bytes() == before


@pytest.mark.parametrize(
    ("metadata_field", "sensitive_value"),
    [
        ("stage", "sk-" + "abcdefghijklmnop"),
        ("correlation_id", "Bearer abc.def.ghi"),
        ("correlation_id", "person@example.test"),
        ("correlation_id", "+86 17610768902"),
        ("correlation_id", "/opt/private/file"),
    ],
)
def test_generic_sensitive_trace_metadata_rejects_without_append(
    tmp_path: Path,
    metadata_field: str,
    sensitive_value: str,
) -> None:
    recorder = recorder_v2(tmp_path)
    recorder.emit("run.started", RUN_STARTED)
    before = trace_path(tmp_path).read_bytes()

    with pytest.raises(ValueError, match="trace event contains restricted metadata"):
        recorder.emit(
            "claim.created",
            {},
            **{metadata_field: sensitive_value},  # type: ignore[arg-type]
        )

    assert trace_path(tmp_path).read_bytes() == before


def test_phone_shaped_sha256_payload_identity_is_preserved_exactly(
    tmp_path: Path,
) -> None:
    digest = "a" * 10 + "17610768902" + "b" * 43

    recorder_v2(tmp_path).emit(
        "claim.created",
        {"artifact_sha256": digest},
    )

    assert rows(tmp_path)[0]["payload"]["artifact_sha256"] == digest


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("checkpoint.saved", {"checkpoint_id": "checkpoint-17610768902"}),
        (
            "tool.called",
            {
                "call_id": "call-17610768902",
                "tool_name": "read_evidence",
                "arguments": {},
            },
        ),
    ],
)
def test_generic_sensitive_semantic_payload_identity_rejects_without_append(
    tmp_path: Path,
    event_type: str,
    payload: dict[str, object],
) -> None:
    recorder = recorder_v2(tmp_path)

    with pytest.raises(
        ValueError,
        match="trace payload contains restricted semantic identity",
    ):
        recorder.emit(event_type, payload)

    assert not trace_path(tmp_path).exists()


def test_configured_secret_in_semantic_payload_identity_rejects_without_append(
    tmp_path: Path,
) -> None:
    configured_secret = "private-runtime-identity"
    recorder = TraceRecorderV2(
        RunStore.create(tmp_path, RUN_ID),
        clock=IncrementingClock(),
        secrets=(configured_secret,),
    )

    with pytest.raises(
        ValueError,
        match="trace payload contains restricted semantic identity",
    ):
        recorder.emit(
            "model.called",
            {"turn": 0, "runtime_id": configured_secret},
        )

    assert not trace_path(tmp_path).exists()


def test_sensitive_mapping_key_redaction_collision_fails_before_persistence(
    tmp_path: Path,
) -> None:
    recorder = recorder_v2(tmp_path)

    with pytest.raises(ValueError, match="redacted mapping key collision"):
        recorder.emit(
            "claim.created",
            {
                "api_key": "first",
                "authorization": "second",
            },
        )

    assert not trace_path(tmp_path).exists()


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("trace_id", "f" * 32, "trace identity"),
        ("span_id", "f" * 16, "span identity"),
    ],
)
def test_read_trace_rejects_invalid_trace_or_span_identity(
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    path = write_three_event_trace(tmp_path)
    values = read_jsonl(path)
    values[1][field] = replacement
    rehash(values[1])
    write_jsonl(path, values)

    with pytest.raises(ValueError, match=message):
        read_trace(path, expected_run_id=RUN_ID)
