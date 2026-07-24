from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from tests.test_contracts import TASK
from tests.test_evidence import task_with_cutoff_edges
from tracelane.contracts import HarnessConfig, load_task
from tracelane.runner import run_task
from tracelane.runtime.base import ModelRequest, ModelResponse
from tracelane.runtime.stub import DeterministicStubRuntime


def trace_rows(result) -> list[dict[str, object]]:
    return [json.loads(line) for line in result.trace_path.read_text(encoding="utf-8").splitlines()]


def stage_transitions(result) -> list[tuple[str, str | None]]:
    return [
        (row["event_type"], row["stage"])
        for row in trace_rows(result)
        if str(row["event_type"]).startswith("stage.")
    ]


def test_conditional_stage_machine_skips_unneeded_debate(tmp_path: Path) -> None:
    runtime = DeterministicStubRuntime()
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        runtime,
        tmp_path,
    )
    assert result.status == "published"
    assert stage_transitions(result) == [
        ("stage.started", "gather"),
        ("stage.completed", "gather"),
        ("stage.started", "analyze"),
        ("stage.completed", "analyze"),
        ("stage.skipped", "debate"),
        ("stage.started", "finalize"),
        ("stage.completed", "finalize"),
        ("stage.started", "validate"),
        ("stage.completed", "validate"),
        ("stage.started", "publish"),
        ("stage.completed", "publish"),
    ]
    assert [request.stage for request in runtime.requests] == ["analyze", "finalize"]


def test_always_debate_executes_exactly_once(tmp_path: Path) -> None:
    runtime = DeterministicStubRuntime()
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(debate_policy="always"),
        runtime,
        tmp_path,
    )
    assert result.status == "published"
    assert [request.stage for request in runtime.requests].count("debate") == 1
    assert ("stage.completed", "debate") in stage_transitions(result)


def test_budgeted_context_is_the_only_evidence_visible_to_runtime(tmp_path: Path) -> None:
    task = task_with_cutoff_edges()
    first_record = min(task.evidence, key=lambda record: (record.available_at, record.evidence_id))
    runtime = DeterministicStubRuntime()
    result = run_task(
        task,
        HarnessConfig(context_budget_chars=len(first_record.text)),
        runtime,
        tmp_path,
    )
    assert result.status == "published"
    assert runtime.requests
    assert {record.evidence_id for request in runtime.requests for record in request.evidence} == {
        first_record.evidence_id
    }
    gather = next(
        row
        for row in trace_rows(result)
        if row["event_type"] == "stage.completed" and row["stage"] == "gather"
    )
    assert gather["payload"]["omitted_evidence_ids"] == ["ev-cutoff"]


def test_raw_context_exposes_future_control_arm_to_runtime(tmp_path: Path) -> None:
    task = task_with_cutoff_edges()
    runtime = DeterministicStubRuntime()
    result = run_task(
        task,
        HarnessConfig(context_policy="raw"),
        runtime,
        tmp_path,
    )
    assert result.status == "published"
    assert {record.evidence_id for record in runtime.requests[0].evidence} == {
        record.evidence_id for record in task.evidence
    }


class FailFinalizeOnceRuntime(DeterministicStubRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def complete(self, request: ModelRequest) -> ModelResponse:
        if request.stage == "finalize" and not self.failed:
            self.failed = True
            raise RuntimeError("synthetic finalize interruption")
        return super().complete(request)


def test_resume_from_analyze_does_not_repeat_completed_stages(tmp_path: Path) -> None:
    task = load_task(deepcopy(TASK))
    runtime = FailFinalizeOnceRuntime()
    interrupted = run_task(task, HarnessConfig(), runtime, tmp_path)
    assert interrupted.status == "failed"
    resumed = run_task(task, HarnessConfig(), runtime, tmp_path)
    assert resumed.status == "published"
    assert resumed.resumed_from == "analyze"
    assert [request.stage for request in runtime.requests].count("analyze") == 1
    assert any(
        row["event_type"] == "run.resumed" and row["payload"]["checkpoint_stage"] == "analyze"
        for row in trace_rows(resumed)
    )


class InvalidFinalizeRuntime(DeterministicStubRuntime):
    def complete(self, request: ModelRequest) -> ModelResponse:
        response = super().complete(request)
        if request.stage != "finalize":
            return response
        return replace(response, content={"answer": "", "claims": [], "missing_information": []})


def test_invalid_runtime_output_is_not_published(tmp_path: Path) -> None:
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        InvalidFinalizeRuntime(),
        tmp_path,
    )
    assert result.status == "failed"
    assert result.answer_path is None
    assert not (tmp_path / "runs" / result.run_id / "output" / "answer.json").exists()
    assert any(
        row["event_type"] == "stage.failed" and row["stage"] == "validate"
        for row in trace_rows(result)
    )
