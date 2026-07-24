from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tracelane.adapters.fixtures import FixtureToolAdapter
from tracelane.artifacts import RunIdentity, RunStore
from tracelane.contracts import (
    FrozenBundle,
    HarnessConfig,
    TaskSpec,
    canonical_json,
    sha256_json,
)
from tracelane.evidence import freeze_evidence
from tracelane.graders.metrics import grade_run
from tracelane.orchestrator import Orchestrator
from tracelane.runtime.base import ModelRuntime
from tracelane.tracing import TraceRecorder


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: str
    answer_path: Path | None
    trace_path: Path
    resumed_from: str | None


def _write_immutable(store: RunStore, name: str, value: object) -> None:
    target = store.path_for(name)
    expected = (canonical_json(value) + "\n").encode("utf-8")
    if target.exists():
        if target.read_bytes() != expected:
            raise ValueError(f"immutable run input does not match identity: {name}")
        return
    store.write_json(name, value)


def _write_inputs(
    store: RunStore,
    task: TaskSpec,
    bundle: FrozenBundle,
    config: HarnessConfig,
    identity: RunIdentity,
) -> None:
    _write_immutable(store, "input/task.json", task)
    _write_immutable(store, "input/evidence.json", bundle)
    _write_immutable(store, "input/config.json", config)
    _write_immutable(store, "input/identity.json", identity.to_dict())


def run_task(
    task: TaskSpec,
    config: HarnessConfig,
    runtime: ModelRuntime,
    artifacts_root: str | Path,
    *,
    repeat: int = 1,
) -> RunResult:
    records = FixtureToolAdapter().collect(task)
    bundle = freeze_evidence(task, records)
    identity = RunIdentity(
        task_sha256=sha256_json(task),
        bundle_sha256=bundle.bundle_sha256,
        config_sha256=sha256_json(config),
        model_id=runtime.model_id,
        repeat=repeat,
    )
    store = RunStore.create(artifacts_root, identity.run_id)
    trace_path = store.run_dir / "trace" / "events.jsonl"
    orchestrator = Orchestrator(identity)

    try:
        _write_inputs(store, task, bundle, config, identity)
        store.write_json(
            "run.json",
            {
                "run_id": identity.run_id,
                "status": "running",
                "identity": identity.to_dict(),
            },
        )
        TraceRecorder(store).emit(
            "tool.completed",
            {
                "tool": "fixture-evidence",
                "record_count": len(records),
                "bundle_sha256": bundle.bundle_sha256,
            },
            stage="gather",
        )
        orchestrator.run(task, bundle, config, runtime, store)
        answer_path = store.run_dir / "output" / "answer.json"
        answer_sha256 = sha256_json(store.read_json("output/answer.json"))
        store.write_json(
            "run.json",
            {
                "run_id": identity.run_id,
                "status": "published",
                "identity": identity.to_dict(),
                "answer_sha256": answer_sha256,
                "answer_published": True,
            },
        )
        grades = grade_run(store)
        grades_value = grades.to_dict()
        store.write_json("output/grades.json", grades_value)
        TraceRecorder(store).emit(
            "grader.completed",
            {
                "passed": grades.passed,
                "completion_coverage": grades.completion.coverage,
                "citation_precision": grades.grounding.citation_precision,
                "citation_recall": grades.grounding.citation_recall,
                "pit_violations": grades.pit.pit_violations,
            },
        )
    except Exception as exc:
        store.write_json(
            "run.json",
            {
                "run_id": identity.run_id,
                "status": "failed",
                "identity": identity.to_dict(),
                "error_type": type(exc).__name__,
            },
        )
        return RunResult(
            run_id=identity.run_id,
            status="failed",
            answer_path=None,
            trace_path=trace_path,
            resumed_from=orchestrator.resumed_from,
        )

    status = "passed" if grades.passed else "failed"
    store.write_json(
        "run.json",
        {
            "run_id": identity.run_id,
            "status": status,
            "identity": identity.to_dict(),
            "answer_sha256": answer_sha256,
            "grades_sha256": sha256_json(grades_value),
            "answer_published": True,
        },
    )
    return RunResult(
        run_id=identity.run_id,
        status=status,
        answer_path=answer_path,
        trace_path=trace_path,
        resumed_from=orchestrator.resumed_from,
    )
