from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from tests.test_contracts import TASK
from tracelane.artifacts import RunStore
from tracelane.contracts import HarnessConfig, load_task
from tracelane.graders.metrics import grade_run
from tracelane.runner import run_task
from tracelane.runtime.stub import DeterministicStubRuntime


def test_runner_writes_identity_inputs_answer_and_trace(tmp_path: Path) -> None:
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        DeterministicStubRuntime(),
        tmp_path,
    )
    run_dir = tmp_path / "runs" / result.run_id
    assert result.status == "passed"
    assert result.answer_path == run_dir / "output" / "answer.json"
    assert result.answer_path.exists()
    assert result.trace_path == run_dir / "trace" / "events.jsonl"
    assert result.trace_path.exists()
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["status"] == "passed"
    assert (run_dir / "output" / "grades.json").exists()
    report = grade_run(RunStore.create(tmp_path, result.run_id))
    assert report.passed
    assert report.operations.model_calls == 2
    assert report.operations.tool_calls == 1
    assert {path.name for path in (run_dir / "input").iterdir()} == {
        "task.json",
        "evidence.json",
        "config.json",
        "identity.json",
    }


def test_runner_identity_is_stable_and_repeat_is_an_experiment_dimension(
    tmp_path: Path,
) -> None:
    task = load_task(deepcopy(TASK))
    config = HarnessConfig()
    first = run_task(task, config, DeterministicStubRuntime(), tmp_path, repeat=1)
    repeated = run_task(task, config, DeterministicStubRuntime(), tmp_path, repeat=1)
    second_repeat = run_task(task, config, DeterministicStubRuntime(), tmp_path, repeat=2)
    assert first.run_id == repeated.run_id
    assert first.run_id != second_repeat.run_id
    assert repeated.resumed_from == "finalize"


def test_runner_refuses_to_overwrite_mutated_immutable_input(tmp_path: Path) -> None:
    task = load_task(deepcopy(TASK))
    config = HarnessConfig()
    first = run_task(task, config, DeterministicStubRuntime(), tmp_path)
    task_path = tmp_path / "runs" / first.run_id / "input" / "task.json"
    task_path.write_text('{"tampered":true}\n', encoding="utf-8")
    second = run_task(task, config, DeterministicStubRuntime(), tmp_path)
    assert second.status == "failed"
    assert json.loads(task_path.read_text(encoding="utf-8")) == {"tampered": True}
