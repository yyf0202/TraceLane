from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.test_artifacts import identity
from tracelane.artifacts import RunStore
from tracelane.tracing import TraceRecorder


class IncrementingClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 24, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.current
        self.current += timedelta(seconds=1)
        return value


def test_trace_sequence_is_monotonic_and_jsonl_is_parseable(tmp_path: Path) -> None:
    expected = identity()
    store = RunStore.create(tmp_path, expected.run_id)
    recorder = TraceRecorder(store, clock=IncrementingClock())
    first = recorder.emit("stage.started", {"input_hash": "a" * 64}, stage="gather")
    second = recorder.emit("stage.completed", {"output_hash": "b" * 64}, stage="gather")
    assert (first.sequence, second.sequence) == (1, 2)
    rows = [
        json.loads(line)
        for line in (store.run_dir / "trace" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert all(row["run_id"] == expected.run_id for row in rows)


def test_reopened_trace_continues_sequence(tmp_path: Path) -> None:
    expected = identity()
    store = RunStore.create(tmp_path, expected.run_id)
    TraceRecorder(store, clock=IncrementingClock()).emit("run.started", {})
    reopened = TraceRecorder(store, clock=IncrementingClock())
    assert reopened.emit("run.resumed", {}).sequence == 2
