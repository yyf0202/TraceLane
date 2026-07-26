from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracelane.artifacts import RunIdentity, RunStore
from tracelane.contracts import (
    EvidenceRecord,
    FrozenBundle,
    HarnessConfig,
    TaskSpec,
    sha256_json,
)
from tracelane.decision_orchestrator import AnalystSpec, DecisionOrchestrator
from tracelane.runtime.stub import DeterministicStubRuntime
from tracelane.spine import Resolution
from tracelane.spine.ledger import Ledger


def _ts(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def make_task() -> TaskSpec:
    evidence = (
        EvidenceRecord(
            evidence_id="ev_good",
            available_at=_ts(1),
            source="news",
            text="Strong earnings beat.",
            fact_ids=("f1",),
        ),
        EvidenceRecord(
            evidence_id="ev_bad",
            available_at=_ts(2),
            source="filing",
            text="Rising debt load.",
            fact_ids=("f2",),
        ),
    )
    return TaskSpec(
        task_id="DEC-001",
        question="Research SUBJ and decide.",
        cutoff_at=_ts(3),
        expected_facts={"f1": "earnings", "f2": "debt"},
        completion_facts=("f1",),
        evidence=evidence,
        future_evidence_ids=(),
        fault_scenario=None,
        license="test",
    )


def make_bundle(task: TaskSpec) -> FrozenBundle:
    records = tuple(sorted(task.evidence, key=lambda r: (r.available_at, r.evidence_id)))
    return FrozenBundle(
        task_id=task.task_id,
        cutoff_at=task.cutoff_at,
        records=records,
        rejected_future_ids=(),
        bundle_sha256=sha256_json({"records": [r.evidence_id for r in records]}),
    )


def make_setup(tmp_path: Path, analysts, **config_kw):
    task = make_task()
    bundle = make_bundle(task)
    config = HarnessConfig(**config_kw)
    identity = RunIdentity(
        task_sha256=sha256_json(task),
        bundle_sha256=bundle.bundle_sha256,
        config_sha256=sha256_json(config),
        model_id=DeterministicStubRuntime.model_id,
        repeat=1,
    )
    store = RunStore.create(tmp_path, identity.run_id)
    orch = DecisionOrchestrator(identity)
    return task, bundle, config, identity, store, orch


def bullish_bearish():
    return (
        AnalystSpec("fund", "fundamentals-analyst", "bullish", 0.9),
        AnalystSpec("risk", "risk-analyst", "bearish", 0.7),
    )


def test_decision_chain_commits_signal_and_decision_to_ledger(tmp_path: Path) -> None:
    task, bundle, config, identity, store, orch = make_setup(tmp_path, bullish_bearish())
    result = orch.run(
        task, bundle, config, DeterministicStubRuntime(), store, analysts=bullish_bearish()
    )
    ledger = Ledger(store)
    kinds = [entry.kind for entry in ledger.entries()]
    assert kinds == ["signal", "signal", "decision"]
    assert ledger.records("decision")[0]["decision_id"] == result.decision.decision_id
    assert result.outcome is None  # no resolution supplied


def test_decision_chain_full_loop_with_resolution(tmp_path: Path) -> None:
    task, bundle, config, identity, store, orch = make_setup(tmp_path, bullish_bearish())
    resolution = Resolution(
        subject="DEC-001", actual_direction="bullish", metric_name="net_alpha", metric_value=0.03
    )
    result = orch.run(
        task,
        bundle,
        config,
        DeterministicStubRuntime(),
        store,
        analysts=bullish_bearish(),
        resolution=resolution,
    )
    kinds = [entry.kind for entry in Ledger(store).entries()]
    assert kinds == ["signal", "signal", "decision", "outcome", "feedback"]
    assert result.outcome is not None and result.outcome.status == "observed"


def test_analyst_signals_are_evidence_bound(tmp_path: Path) -> None:
    task, bundle, config, identity, store, orch = make_setup(tmp_path, bullish_bearish())
    result = orch.run(
        task, bundle, config, DeterministicStubRuntime(), store, analysts=bullish_bearish()
    )
    for signal in result.signals:
        assert signal.evidence_ids  # non-abstaining signals must cite evidence
        assert set(signal.evidence_ids) <= {"ev_good", "ev_bad"}


def test_abstaining_analyst_abstains_with_reason(tmp_path: Path) -> None:
    analysts = (
        AnalystSpec("fund", "fundamentals-analyst", "bullish", 0.9),
        AnalystSpec("news", "news-analyst", abstains=True),
    )
    task, bundle, config, identity, store, orch = make_setup(tmp_path, analysts)
    result = orch.run(task, bundle, config, DeterministicStubRuntime(), store, analysts=analysts)
    by_id = {s.analyst_id: s for s in result.signals}
    assert by_id["news"].abstained is True
    assert by_id["news"].abstain_reason is not None
    assert "news" in result.fusion.abstained_analysts


def test_debate_policy_always_marks_debated(tmp_path: Path) -> None:
    task, bundle, config, identity, store, orch = make_setup(tmp_path, bullish_bearish())
    result = orch.run(
        task,
        bundle,
        config,
        DeterministicStubRuntime(),
        store,
        analysts=bullish_bearish(),
        debate_policy="always",
    )
    assert result.debated is True


def test_debate_policy_never_skips(tmp_path: Path) -> None:
    task, bundle, config, identity, store, orch = make_setup(tmp_path, bullish_bearish())
    result = orch.run(
        task,
        bundle,
        config,
        DeterministicStubRuntime(),
        store,
        analysts=bullish_bearish(),
        debate_policy="never",
    )
    assert result.debated is False


def test_conflicting_signals_trigger_conditional_debate(tmp_path: Path) -> None:
    # Two evenly-matched opposing analysts -> high disagreement -> debate.
    analysts = (
        AnalystSpec("a", "role-a", "bullish", 0.9),
        AnalystSpec("b", "role-b", "bearish", 0.9),
    )
    task, bundle, config, identity, store, orch = make_setup(tmp_path, analysts)
    result = orch.run(
        task,
        bundle,
        config,
        DeterministicStubRuntime(),
        store,
        analysts=analysts,
        debate_policy="conditional",
    )
    assert result.fusion.disagreement > 0.5
    assert result.debated is True


def test_deterministic_across_reruns(tmp_path: Path) -> None:
    first = make_setup(tmp_path / "a", bullish_bearish())
    second = make_setup(tmp_path / "b", bullish_bearish())
    resolution = Resolution(
        subject="DEC-001", actual_direction="bullish", metric_name="net_alpha", metric_value=0.02
    )
    r1 = first[5].run(
        first[0],
        first[1],
        first[2],
        DeterministicStubRuntime(),
        first[4],
        analysts=bullish_bearish(),
        resolution=resolution,
    )
    r2 = second[5].run(
        second[0],
        second[1],
        second[2],
        DeterministicStubRuntime(),
        second[4],
        analysts=bullish_bearish(),
        resolution=resolution,
    )
    assert r1.decision.decision_id == r2.decision.decision_id
    assert [s.signal_id for s in r1.signals] == [s.signal_id for s in r2.signals]


def test_requires_at_least_one_analyst(tmp_path: Path) -> None:
    task, bundle, config, identity, store, orch = make_setup(tmp_path, ())
    with pytest.raises(ValueError, match="at least one analyst"):
        orch.run(task, bundle, config, DeterministicStubRuntime(), store, analysts=())
