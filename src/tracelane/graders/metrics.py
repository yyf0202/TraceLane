from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tracelane.artifacts import RunStore
from tracelane.contracts import (
    EvidenceRecord,
    FrozenBundle,
    load_task,
    parse_utc,
)
from tracelane.graders.completion import CompletionGrade, grade_completion
from tracelane.graders.grounding import GroundingGrade, grade_grounding
from tracelane.graders.pit import PitGrade, grade_pit
from tracelane.graders.recovery import RecoveryGrade, grade_recovery
from tracelane.validation import ValidationReport, validate_answer


@dataclass(frozen=True)
class OperationalMetrics:
    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    latency_ms: int
    retries: int
    repeated_stages: tuple[str, ...]
    resume_position: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "latency_ms": self.latency_ms,
            "retries": self.retries,
            "repeated_stages": self.repeated_stages,
            "resume_position": self.resume_position,
        }


@dataclass(frozen=True)
class GradeReport:
    validation: ValidationReport
    completion: CompletionGrade
    grounding: GroundingGrade
    pit: PitGrade
    recovery: RecoveryGrade
    operations: OperationalMetrics
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "validation": self.validation.to_dict(),
            "completion": self.completion.to_dict(),
            "grounding": self.grounding.to_dict(),
            "pit": self.pit.to_dict(),
            "recovery": self.recovery.to_dict(),
            "operations": self.operations.to_dict(),
            "passed": self.passed,
        }


def _trace_rows(store: RunStore) -> tuple[Mapping[str, object], ...]:
    path = store.path_for("trace/events.jsonl")
    if not path.exists():
        return ()
    try:
        values = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("trace is not valid JSONL") from exc
    if any(not isinstance(value, dict) for value in values):
        raise ValueError("trace events must be JSON objects")
    return tuple(values)


def _integer(payload: object, key: str) -> int:
    if not isinstance(payload, Mapping):
        return 0
    value = payload.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _operational_metrics(trace: Sequence[Mapping[str, object]]) -> OperationalMetrics:
    model_events = [event for event in trace if event.get("event_type") == "model.completed"]
    completed_counts: dict[str, int] = {}
    resume_position: str | None = None
    for event in trace:
        if event.get("event_type") == "stage.completed" and isinstance(event.get("stage"), str):
            stage = event["stage"]
            completed_counts[stage] = completed_counts.get(stage, 0) + 1
        if event.get("event_type") == "run.resumed":
            payload = event.get("payload")
            if isinstance(payload, Mapping) and isinstance(payload.get("checkpoint_stage"), str):
                resume_position = payload["checkpoint_stage"]
    return OperationalMetrics(
        model_calls=len(model_events),
        tool_calls=sum(event.get("event_type") == "tool.completed" for event in trace),
        input_tokens=sum(_integer(event.get("payload"), "input_tokens") for event in model_events),
        output_tokens=sum(
            _integer(event.get("payload"), "output_tokens") for event in model_events
        ),
        cached_tokens=sum(
            _integer(event.get("payload"), "cached_tokens") for event in model_events
        ),
        latency_ms=sum(_integer(event.get("payload"), "latency_ms") for event in model_events),
        retries=sum(
            max(0, _integer(event.get("payload"), "attempt") - 1) for event in model_events
        ),
        repeated_stages=tuple(
            sorted(stage for stage, count in completed_counts.items() if count > 1)
        ),
        resume_position=resume_position,
    )


def _load_bundle(value: object) -> FrozenBundle:
    if not isinstance(value, dict):
        raise ValueError("frozen evidence bundle must be a JSON object")
    records = value.get("records")
    if not isinstance(records, list):
        raise ValueError("frozen evidence records must be a list")
    return FrozenBundle(
        task_id=value["task_id"],
        cutoff_at=parse_utc(value["cutoff_at"]),
        records=tuple(
            EvidenceRecord(
                evidence_id=record["evidence_id"],
                available_at=parse_utc(record["available_at"]),
                source=record["source"],
                text=record["text"],
                fact_ids=tuple(record["fact_ids"]),
            )
            for record in records
        ),
        rejected_future_ids=tuple(value["rejected_future_ids"]),
        bundle_sha256=value["bundle_sha256"],
    )


def grade_run(store: RunStore) -> GradeReport:
    task_value = store.read_json("input/task.json")
    bundle_value = store.read_json("input/evidence.json")
    run_value = store.read_json("run.json")
    answer_value = store.read_json("output/answer.json")
    if not isinstance(task_value, dict) or not isinstance(run_value, dict):
        raise ValueError("run inputs are invalid")
    if not isinstance(answer_value, dict):
        raise ValueError("published answer must be a JSON object")
    if run_value.get("status") != "published" and run_value.get("answer_published") is not True:
        raise ValueError("graders only grade an explicitly published answer")

    task = load_task(task_value)
    bundle = _load_bundle(bundle_value)
    expected_sha256 = run_value.get("answer_sha256")
    if not isinstance(expected_sha256, str):
        expected_sha256 = None
    validation = validate_answer(
        answer_value,
        task,
        bundle,
        expected_sha256=expected_sha256,
    )
    completion = grade_completion(answer_value, task)
    grounding = grade_grounding(answer_value, task, bundle)
    pit = grade_pit(answer_value, task)
    trace = _trace_rows(store)
    recovery = grade_recovery(trace, task)
    operations = _operational_metrics(trace)
    passed = (
        validation.valid
        and completion.coverage == 1.0
        and grounding.unsupported_claims == 0
        and pit.pit_violations == 0
    )
    return GradeReport(
        validation=validation,
        completion=completion,
        grounding=grounding,
        pit=pit,
        recovery=recovery,
        operations=operations,
        passed=passed,
    )
