"""A domain decision orchestrator driving the decision → outcome → feedback spine.

Where :class:`~tracelane.orchestrator.Orchestrator` is answer-oriented (it
produces an :class:`~tracelane.contracts.AgentAnswer` of grounded claims),
this orchestrator is decision-oriented: it gathers point-in-time evidence,
asks several analyst roles for typed signals, fuses them deterministically,
optionally routes the stance through debate, commits a decision to the spine
ledger, and — given a point-in-time-true resolution of the world — resolves it
into a factual outcome and deterministic feedback.

The chain is checkpoint-resumable and append-only-auditable.  The same code
path runs offline against :class:`~tracelane.runtime.stub.DeterministicStubRuntime`
and online against a real runtime, so the whole
``evidence → signal → decision → outcome → feedback`` loop can be studied and
ablated without external credentials.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from tracelane.artifacts import RunIdentity, RunStore
from tracelane.checkpoint import CheckpointStore
from tracelane.contracts import EvidenceRecord, FrozenBundle, HarnessConfig, TaskSpec
from tracelane.runtime.base import ModelRequest, ModelResponse, ModelRuntime
from tracelane.spine.contracts import DecisionRecord, OutcomeRecord, TypedSignal
from tracelane.spine.debate import DebatePolicy, should_debate
from tracelane.spine.feedback import Resolution, attribute_feedback, resolve_decision
from tracelane.spine.fusion import FusionResult, fuse_signals
from tracelane.spine.ledger import Ledger
from tracelane.tracing import TraceRecorder


@dataclass(frozen=True)
class AnalystSpec:
    """One analyst role in the decision chain.

    ``direction_hint`` / ``confidence_hint`` are the deterministic stance the
    offline stub reflects back; a real runtime would instead prompt the model
    with the role and evidence and parse the same typed fields.
    """

    analyst_id: str
    role: str
    direction_hint: str = "neutral"
    confidence_hint: float = 0.5
    abstains: bool = False


@dataclass(frozen=True)
class DecisionOutcome:
    """The published result of one decision-orchestrator run."""

    decision: DecisionRecord
    fusion: FusionResult
    signals: tuple[TypedSignal, ...]
    outcome: OutcomeRecord | None
    debated: bool


class DecisionOrchestrator:
    """Run the decision chain against a runtime and journal it to the spine."""

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
        *,
        analysts: Sequence[AnalystSpec],
        resolution: Resolution | None = None,
        debate_policy: DebatePolicy = "conditional",
    ) -> DecisionOutcome:
        if not analysts:
            raise ValueError("decision orchestrator requires at least one analyst")
        trace = TraceRecorder(store, clock=self._clock)
        checkpoints = CheckpointStore(store, self._identity)
        ledger = Ledger(store)
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

        subject = task.task_id
        context = self._gather(trace, checkpoints, task, bundle, config, state, completed)

        signals = self._analysts(
            trace, checkpoints, runtime, task, config, context, subject, analysts, state, completed
        )
        fusion = self._fuse(trace, checkpoints, signals, subject, analysts, state, completed)
        debated, fusion = self._maybe_debate(
            trace, checkpoints, runtime, task, config, context, signals, fusion, debate_policy,
            state, completed,
        )
        decision = self._decide(
            trace, checkpoints, ledger, task, signals, fusion, subject, state, completed
        )

        outcome: OutcomeRecord | None = None
        if resolution is not None:
            outcome = self._resolve_and_feedback(
                trace, ledger, decision, fusion, signals, resolution
            )

        return DecisionOutcome(
            decision=decision,
            fusion=fusion,
            signals=signals,
            outcome=outcome,
            debated=debated,
        )

    # -- stages -----------------------------------------------------------

    def _gather(
        self,
        trace: TraceRecorder,
        checkpoints: CheckpointStore,
        task: TaskSpec,
        bundle: FrozenBundle,
        config: HarnessConfig,
        state: dict[str, object],
        completed: set[str],
    ) -> tuple[EvidenceRecord, ...]:
        if "gather" in completed:
            return self._records_from_state(task, state)

        def op() -> tuple[tuple[EvidenceRecord, ...], tuple[str, ...]]:
            if config.context_policy == "raw":
                return (
                    tuple(sorted(task.evidence, key=lambda item: (item.available_at, item.evidence_id))),
                    (),
                )
            admitted: list[EvidenceRecord] = []
            omitted: list[str] = []
            consumed = 0
            for record in bundle.records:
                if consumed + len(record.text) <= config.context_budget_chars:
                    admitted.append(record)
                    consumed += len(record.text)
                else:
                    omitted.append(record.evidence_id)
            return tuple(admitted), tuple(omitted)

        trace.emit("stage.started", {}, stage="gather")
        context, omitted = op()
        state["context_evidence_ids"] = [record.evidence_id for record in context]
        state["omitted_evidence_ids"] = list(omitted)
        trace.emit(
            "stage.completed",
            {
                "admitted_evidence_ids": [record.evidence_id for record in context],
                "omitted_evidence_ids": list(omitted),
                "rejected_future_ids": list(bundle.rejected_future_ids),
            },
            stage="gather",
        )
        checkpoints.save("gather", state)
        completed.add("gather")
        return context

    def _analysts(
        self,
        trace: TraceRecorder,
        checkpoints: CheckpointStore,
        runtime: ModelRuntime,
        task: TaskSpec,
        config: HarnessConfig,
        context: tuple[EvidenceRecord, ...],
        subject: str,
        analysts: Sequence[AnalystSpec],
        state: dict[str, object],
        completed: set[str],
    ) -> tuple[TypedSignal, ...]:
        if "analysts" in completed:
            raw = state.get("signals")
            if not isinstance(raw, list):
                raise ValueError("checkpoint signals state is invalid")
            return tuple(self._signal_from_dict(item) for item in raw)

        trace.emit("stage.started", {"analyst_count": len(analysts)}, stage="analysts")
        signals: list[TypedSignal] = []
        for spec in analysts:
            request = ModelRequest(
                run_id=self._identity.run_id,
                stage="analyst",
                role=spec.role,
                question=task.question,
                evidence=context,
                prior_output={
                    "direction": spec.direction_hint,
                    "confidence": spec.confidence_hint,
                    "abstained": spec.abstains,
                },
                seed=config.seed,
            )
            response = runtime.complete(request)
            trace.emit("model.completed", _response_metrics(response), stage="analysts")
            signals.append(self._parse_signal(spec, response.content, subject, runtime))
        state["signals"] = [signal.to_dict() for signal in signals]
        trace.emit(
            "stage.completed",
            {"signal_ids": [signal.signal_id for signal in signals]},
            stage="analysts",
        )
        checkpoints.save("analysts", state)
        completed.add("analysts")
        return tuple(signals)

    def _fuse(
        self,
        trace: TraceRecorder,
        checkpoints: CheckpointStore,
        signals: tuple[TypedSignal, ...],
        subject: str,
        analysts: Sequence[AnalystSpec],
        state: dict[str, object],
        completed: set[str],
    ) -> FusionResult:
        if "fuse" in completed:
            raw = state.get("fusion")
            if not isinstance(raw, Mapping):
                raise ValueError("checkpoint fusion state is invalid")
            return self._fusion_from_dict(raw, subject)

        trace.emit("stage.started", {}, stage="fuse")
        fusion = fuse_signals(signals, expected_analysts=len(analysts))
        state["fusion"] = fusion.to_dict()
        trace.emit(
            "stage.completed",
            {
                "direction": fusion.direction,
                "score": fusion.score,
                "coverage": fusion.coverage,
                "disagreement": fusion.disagreement,
            },
            stage="fuse",
        )
        checkpoints.save("fuse", state)
        completed.add("fuse")
        return fusion

    def _maybe_debate(
        self,
        trace: TraceRecorder,
        checkpoints: CheckpointStore,
        runtime: ModelRuntime,
        task: TaskSpec,
        config: HarnessConfig,
        context: tuple[EvidenceRecord, ...],
        signals: tuple[TypedSignal, ...],
        fusion: FusionResult,
        debate_policy: DebatePolicy,
        state: dict[str, object],
        completed: set[str],
    ) -> tuple[bool, FusionResult]:
        decision = should_debate(fusion, policy=debate_policy)
        if not decision.should_debate:
            trace.emit("stage.skipped", {"reason": decision.reason}, stage="debate")
            return False, fusion

        if "debate" in completed:
            raw = state.get("fusion")
            if not isinstance(raw, Mapping):
                raise ValueError("checkpoint fusion state is invalid")
            return True, self._fusion_from_dict(raw, fusion.subject)

        trace.emit("stage.started", {"reason": decision.reason}, stage="debate")
        # A real runtime would re-argue the stance against counter-evidence
        # here; the deterministic spine keeps the fused stance but records that
        # debate happened, so the arm comparison isolates the debate variable.
        request = ModelRequest(
            run_id=self._identity.run_id,
            stage="debate",
            role="portfolio-critic",
            question=task.question,
            evidence=context,
            prior_output=fusion.to_dict(),
            seed=config.seed,
        )
        response = runtime.complete(request)
        trace.emit("model.completed", _response_metrics(response), stage="debate")
        state["fusion"] = fusion.to_dict()
        state["debated"] = True
        trace.emit("stage.completed", {"reason": decision.reason}, stage="debate")
        checkpoints.save("debate", state)
        completed.add("debate")
        return True, fusion

    def _decide(
        self,
        trace: TraceRecorder,
        checkpoints: CheckpointStore,
        ledger: Ledger,
        task: TaskSpec,
        signals: tuple[TypedSignal, ...],
        fusion: FusionResult,
        subject: str,
        state: dict[str, object],
        completed: set[str],
    ) -> DecisionRecord:
        trace.emit("stage.started", {}, stage="decide")
        for signal in signals:
            ledger.append("signal", signal)
        decision = DecisionRecord.create(
            subject=subject,
            final_decision=self._final_decision(fusion),
            fusion=fusion.to_dict(),
            signal_ids=tuple(signal.signal_id for signal in signals),
            evidence_ids=tuple(sorted({e for s in signals for e in s.evidence_ids})),
            abstained_analysts=fusion.abstained_analysts,
        )
        ledger.append("decision", decision)
        state["decision_id"] = decision.decision_id
        trace.emit(
            "stage.completed",
            {"decision_id": decision.decision_id, "final_decision": decision.final_decision},
            stage="decide",
        )
        checkpoints.save("decide", state)
        completed.add("decide")
        return decision

    def _resolve_and_feedback(
        self,
        trace: TraceRecorder,
        ledger: Ledger,
        decision: DecisionRecord,
        fusion: FusionResult,
        signals: tuple[TypedSignal, ...],
        resolution: Resolution,
    ) -> OutcomeRecord:
        trace.emit("stage.started", {}, stage="resolve")
        outcome = resolve_decision(decision, fusion, resolution)
        ledger.append("outcome", outcome)
        trace.emit(
            "stage.completed",
            {"outcome_id": outcome.outcome_id, "status": outcome.status},
            stage="resolve",
        )

        trace.emit("stage.started", {}, stage="feedback")
        feedback = attribute_feedback(decision, fusion, signals, outcome, resolution)
        ledger.append("feedback", feedback)
        trace.emit(
            "stage.completed",
            {"feedback_id": feedback.feedback_id, "decision_correct": feedback.decision_correct},
            stage="feedback",
        )
        return outcome

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _final_decision(fusion: FusionResult) -> str:
        return {
            "bullish": "overweight",
            "bearish": "underweight",
            "neutral": "hold",
            "abstain": "abstain",
        }[fusion.direction]

    @staticmethod
    def _parse_signal(
        spec: AnalystSpec,
        content: Mapping[str, object],
        subject: str,
        runtime: ModelRuntime,
    ) -> TypedSignal:
        abstained = bool(content.get("abstained", False))
        direction = str(content.get("direction", "neutral"))
        confidence = content.get("confidence", 0.0)
        evidence_ids = content.get("evidence_ids", [])
        if not isinstance(evidence_ids, (list, tuple)):
            evidence_ids = []
        return TypedSignal.create(
            analyst_id=spec.analyst_id,
            subject=subject,
            direction=direction if abstained else direction,  # type: ignore[arg-type]
            confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.0,
            evidence_ids=tuple(str(item) for item in evidence_ids),
            abstained=abstained,
            abstain_reason=(
                str(content.get("abstain_reason")) if content.get("abstain_reason") else None
            ),
            model_id=runtime.model_id,
        )

    @staticmethod
    def _signal_from_dict(value: object) -> TypedSignal:
        if not isinstance(value, Mapping):
            raise ValueError("signal state is invalid")
        return TypedSignal(
            signal_id=str(value["signal_id"]),
            analyst_id=str(value["analyst_id"]),
            subject=str(value["subject"]),
            direction=str(value["direction"]),  # type: ignore[arg-type]
            confidence=float(value["confidence"]),  # type: ignore[arg-type]
            evidence_ids=tuple(str(item) for item in value["evidence_ids"]),  # type: ignore[union-attr]
            abstained=bool(value["abstained"]),
            abstain_reason=(str(value["abstain_reason"]) if value["abstain_reason"] else None),
            model_id=(str(value["model_id"]) if value["model_id"] else None),
        )

    @staticmethod
    def _fusion_from_dict(value: Mapping[str, object], subject: str) -> FusionResult:
        independence = value.get("independence", {})
        if not isinstance(independence, Mapping):
            independence = {}
        return FusionResult(
            subject=str(value.get("subject", subject)),
            direction=str(value["direction"]),  # type: ignore[arg-type]
            score=float(value["score"]),  # type: ignore[arg-type]
            confidence=float(value["confidence"]),  # type: ignore[arg-type]
            eligible_signal_ids=tuple(str(i) for i in value["eligible_signal_ids"]),  # type: ignore[union-attr]
            excluded_signal_ids=tuple(str(i) for i in value["excluded_signal_ids"]),  # type: ignore[union-attr]
            abstained_analysts=tuple(str(i) for i in value["abstained_analysts"]),  # type: ignore[union-attr]
            disagreement=float(value["disagreement"]),  # type: ignore[arg-type]
            coverage=float(value["coverage"]),  # type: ignore[arg-type]
            independence=tuple(sorted((str(k), float(v)) for k, v in independence.items())),  # type: ignore[arg-type]
            reason=(str(value["reason"]) if value.get("reason") else None),
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


def _response_metrics(response: ModelResponse) -> dict[str, int]:
    return {
        "attempt": response.attempt,
        "cached_tokens": response.cached_tokens,
        "input_tokens": response.input_tokens,
        "latency_ms": response.latency_ms,
        "output_tokens": response.output_tokens,
    }
