from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from tests.test_contracts import TASK
from tracelane.contracts import HarnessConfig, load_task
from tracelane.experiments.runner import inspect_run
from tracelane.runner import run_task
from tracelane.runtime.stub import DeterministicStubRuntime

GOLDEN = Path(__file__).resolve().parent / "golden" / "demo-summary.json"


class FixedClock:
    def __call__(self) -> datetime:
        return datetime(2026, 7, 24, tzinfo=UTC)


def test_demo_outputs_are_byte_stable_across_artifact_roots(tmp_path: Path) -> None:
    task = load_task(deepcopy(TASK))
    first = run_task(
        task,
        HarnessConfig(),
        DeterministicStubRuntime(),
        tmp_path / "first",
        clock=FixedClock(),
    )
    second = run_task(
        task,
        HarnessConfig(),
        DeterministicStubRuntime(),
        tmp_path / "second",
        clock=FixedClock(),
    )
    assert first.run_id == second.run_id
    relative_paths = (
        "input/task.json",
        "input/evidence.json",
        "input/config.json",
        "input/identity.json",
        "trace/events.jsonl",
        "output/answer.json",
        "output/grades.json",
        "run.json",
    )
    for relative in relative_paths:
        first_path = tmp_path / "first" / "runs" / first.run_id / relative
        second_path = tmp_path / "second" / "runs" / second.run_id / relative
        assert first_path.read_bytes() == second_path.read_bytes(), relative


def test_normalized_demo_summary_matches_golden(tmp_path: Path) -> None:
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        DeterministicStubRuntime(),
        tmp_path,
        clock=FixedClock(),
    )
    actual = inspect_run(tmp_path / "runs" / result.run_id)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
