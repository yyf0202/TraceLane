from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from tracelane.artifacts import RunIdentity, RunStore
from tracelane.checkpoint import CheckpointStore
from tracelane.contracts import (
    AgentAnswer,
    EvidenceRecord,
    FrozenBundle,
    HarnessConfig,
    TaskSpec,
    load_answer,
)
from tracelane.runtime.base import ModelRequest, ModelResponse, ModelRuntime
from tracelane.tracing import TraceRecorder
from tracelane.validation import validate_answer


def _context_records(
    task: TaskSpec,
    bundle: FrozenBundle,
    config: HarnessConfig,
) -> tuple[tuple[EvidenceRecord, ...], tuple[str, ...]]:
    if config.context_policy == "raw":
        return (
            tuple(sorted(task.evidence, key=lambda item: (item.available_at, item.evidence_id))),
            (),
        )

    admitted: list[EvidenceRecord] = []
    omitted: list[str] = []
    consumed_chars = 0
    for record in bundle.records:
        record_chars = len(record.text)
        if consumed_chars + record_chars <= config.context_budget_chars:
            admitted.append(record)
            consumed_chars += record_chars
        else:
            omitted.append(record.evidence_id)
    return tuple(admitted), tuple(omitted)


def _has_conflict(records: tuple[EvidenceRecord, ...]) -> bool:
    values_by_fact: dict[str, set[str]] = {}
    for record in records:
        normalized = " ".join(record.text.casefold().split())
        for fact_id in record.fact_ids:
            values_by_fact.setdefault(fact_id, set()).add(normalized)
    return any(len(values) > 1 for values in values_by_fact.values())


def _response_metrics(response: ModelResponse) -> dict[str, int]:
    return {
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "cached_tokens": response.cached_tokens,
        "latency_ms": response.latency_ms,
        "attempt": response.attempt,
    }


class Orchestrator:
    def __init__(
        self,
        identity: RunIdentity,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._identity = identity
        self._clock = clock
        self.resumed_from: str | None = None

    def run(
        self,
        task: TaskSpec,
        bundle: FrozenBundle,
        config: HarnessConfig,
        runtime: ModelRuntime,
        store: RunStore,
    ) -> AgentAnswer:
        trace = TraceRecorder(store, clock=self._clock)
        checkpoints = CheckpointStore(store, self._identity)
        state: dict[str, object] = {}
        completed: set[str] = set()

        if config.recovery_policy == "checkpoint":
            latest = checkpoints.load_latest(self._identity)
            if latest is not None:
                state = dict(latest.state)
                completed = set(latest.completed_stages)
                self.resumed_from = latest.stage
                trace.emit(
                    "run.resumed",
                    {
                        "checkpoint_sequence": latest.sequence,
                        "checkpoint_stage": latest.stage,
                    },
                )

        if "gather" not in completed:
            context, omitted = self._stage(
                trace,
                "gather",
                lambda: _context_records(task, bundle, config),
                completion_payload=lambda value: {
                    "admitted_evidence_ids": [record.evidence_id for record in value[0]],
                    "omitted_evidence_ids": list(value[1]),
                    "rejected_future_ids": list(bundle.rejected_future_ids),
                },
            )
            state["context_evidence_ids"] = [record.evidence_id for record in context]
            state["omitted_evidence_ids"] = list(omitted)
            checkpoints.save("gather", state)
            completed.add("gather")
        else:
            context = self._records_from_state(task, state)

        if "analyze" not in completed:
            analysis = self._model_stage(
                trace,
                runtime,
                task,
                config,
                context,
                stage="analyze",
                role="evidence-analyst",
                prior_output={},
            )
            state["analysis"] = analysis
            checkpoints.save("analyze", state)
            completed.add("analyze")
        else:
            analysis = self._state_mapping(state, "analysis")

        should_debate = config.debate_policy == "always" or _has_conflict(context)
        if should_debate:
            if "debate" not in completed:
                debate = self._model_stage(
                    trace,
                    runtime,
                    task,
                    config,
                    context,
                    stage="debate",
                    role="evidence-critic",
                    prior_output=analysis,
                )
                state["debate"] = debate
                checkpoints.save("debate", state)
                completed.add("debate")
            else:
                debate = self._state_mapping(state, "debate")
            final_input = debate
        else:
            trace.emit("stage.skipped", {"reason": "no_conflicting_fact_values"}, stage="debate")
            final_input = analysis

        if "finalize" not in completed:
            finalized = self._model_stage(
                trace,
                runtime,
                task,
                config,
                context,
                stage="finalize",
                role="answer-writer",
                prior_output=final_input,
            )
            state["answer"] = finalized
            checkpoints.save("finalize", state)
        else:
            finalized = self._state_mapping(state, "answer")

        answer = self._stage(
            trace,
            "validate",
            lambda: self._validate_answer(finalized, task, bundle),
            completion_payload=lambda _value: {"valid": True},
        )
        self._stage(
            trace,
            "publish",
            lambda: store.write_json("output/answer.json", answer),
            completion_payload=lambda path: {
                "artifact": path.relative_to(store.run_dir).as_posix()
            },
        )
        return answer

    @staticmethod
    def _stage(
        trace: TraceRecorder,
        stage: str,
        operation: Callable[[], object],
        *,
        completion_payload: Callable[[object], Mapping[str, object]],
    ):
        trace.emit("stage.started", {}, stage=stage)
        try:
            value = operation()
        except Exception as exc:
            trace.emit(
                "stage.failed",
                {"error_type": type(exc).__name__},
                stage=stage,
            )
            raise
        trace.emit("stage.completed", completion_payload(value), stage=stage)
        return value

    def _model_stage(
        self,
        trace: TraceRecorder,
        runtime: ModelRuntime,
        task: TaskSpec,
        config: HarnessConfig,
        evidence: tuple[EvidenceRecord, ...],
        *,
        stage: str,
        role: str,
        prior_output: Mapping[str, object],
    ) -> Mapping[str, object]:
        def complete() -> Mapping[str, object]:
            request = ModelRequest(
                run_id=self._identity.run_id,
                stage=stage,
                role=role,
                question=task.question,
                evidence=evidence,
                prior_output=prior_output,
                seed=config.seed,
            )
            response = runtime.complete(request)
            trace.emit("model.completed", _response_metrics(response), stage=stage)
            return response.content

        return self._stage(
            trace,
            stage,
            complete,
            completion_payload=lambda value: {"output_keys": sorted(value)},
        )

    @staticmethod
    def _records_from_state(
        task: TaskSpec,
        state: Mapping[str, object],
    ) -> tuple[EvidenceRecord, ...]:
        evidence_ids = state.get("context_evidence_ids")
        if not isinstance(evidence_ids, (list, tuple)) or any(
            not isinstance(item, str) for item in evidence_ids
        ):
            raise ValueError("checkpoint context evidence IDs are invalid")
        by_id = {record.evidence_id: record for record in task.evidence}
        try:
            return tuple(by_id[evidence_id] for evidence_id in evidence_ids)
        except KeyError as exc:
            raise ValueError("checkpoint references unknown context evidence") from exc

    @staticmethod
    def _state_mapping(
        state: Mapping[str, object],
        key: str,
    ) -> Mapping[str, object]:
        value = state.get(key)
        if not isinstance(value, Mapping):
            raise ValueError(f"checkpoint {key} state is invalid")
        return value

    @staticmethod
    def _validate_answer(
        value: Mapping[str, object],
        task: TaskSpec,
        bundle: FrozenBundle,
    ) -> AgentAnswer:
        answer = load_answer(value)
        report = validate_answer(answer, task, bundle)
        if not report.valid:
            raise ValueError("answer validation failed")
        return answer
