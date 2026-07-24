from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from tracelane.artifacts import RunStore
from tracelane.contracts import HarnessConfig, TaskSpec, canonical_json, sha256_json
from tracelane.runner import RunResult, run_task
from tracelane.runtime.stub import DeterministicStubRuntime
from tracelane.suite import load_suite


def _safe_root(path: str | Path) -> Path:
    supplied = Path(path)
    if supplied.is_symlink():
        raise ValueError("artifact root must not be a symlink")
    root = supplied.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (canonical_json(value) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def packaged_v01_suite() -> Path:
    installed = Path(str(files("tracelane").joinpath("fixtures", "v0.1")))
    if installed.is_dir():
        return installed
    repository_fixture = Path(__file__).resolve().parents[3] / "fixtures" / "v0.1"
    if repository_fixture.is_dir():
        return repository_fixture
    raise ValueError("packaged v0.1 fixture suite is unavailable")


def _grade_value(root: Path, result: RunResult) -> dict[str, object] | None:
    path = root / "runs" / result.run_id / "output" / "grades.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("grade artifact must be a JSON object")
    return value


def _number(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _nested(value: dict[str, object] | None, *keys: str) -> object:
    current: object = value
    for key in keys:
        if not isinstance(current, dict):
            return 0
        current = current.get(key, 0)
    return current


def evaluate_suite(
    tasks: tuple[TaskSpec, ...],
    config: HarnessConfig,
    artifacts_root: str | Path,
) -> dict[str, object]:
    root = _safe_root(artifacts_root)
    task_results: list[dict[str, object]] = []
    for task in tasks:
        result = run_task(
            task,
            config,
            DeterministicStubRuntime(),
            root,
        )
        grades = _grade_value(root, result)
        task_results.append(
            {
                "task_id": task.task_id,
                "run_id": result.run_id,
                "status": result.status,
                "passed": bool(grades and grades.get("passed") is True),
                "completion_coverage": _number(_nested(grades, "completion", "coverage")),
                "citation_precision": _number(_nested(grades, "grounding", "citation_precision")),
                "citation_recall": _number(_nested(grades, "grounding", "citation_recall")),
                "pit_violations": int(_number(_nested(grades, "pit", "pit_violations"))),
                "model_calls": int(_number(_nested(grades, "operations", "model_calls"))),
                "total_tokens": int(
                    _number(_nested(grades, "operations", "input_tokens"))
                    + _number(_nested(grades, "operations", "output_tokens"))
                ),
            }
        )

    count = len(task_results)
    passed_count = sum(item["passed"] is True for item in task_results)
    summary = {
        "task_count": count,
        "passed_count": passed_count,
        "pass_rate": passed_count / count if count else 0.0,
        "mean_completion_coverage": (
            sum(float(item["completion_coverage"]) for item in task_results) / count
            if count
            else 0.0
        ),
        "mean_citation_precision": (
            sum(float(item["citation_precision"]) for item in task_results) / count
            if count
            else 0.0
        ),
        "mean_citation_recall": (
            sum(float(item["citation_recall"]) for item in task_results) / count if count else 0.0
        ),
        "pit_violations": sum(int(item["pit_violations"]) for item in task_results),
        "total_model_calls": sum(int(item["model_calls"]) for item in task_results),
        "total_tokens": sum(int(item["total_tokens"]) for item in task_results),
        "tasks": task_results,
    }
    _write_json(root / "summary.json", summary)
    return summary


def _code_commit() -> str:
    repository = Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def ablate_context_policy(
    tasks: tuple[TaskSpec, ...],
    artifacts_root: str | Path,
    *,
    seed: int = 7,
) -> tuple[Path, dict[str, object]]:
    root = _safe_root(artifacts_root)
    arms = {
        "control": HarnessConfig(context_policy="raw", seed=seed),
        "treatment": HarnessConfig(context_policy="pit_budgeted", seed=seed),
    }
    suite_sha256 = sha256_json(tasks)
    experiment_id = sha256_json(
        {
            "suite_sha256": suite_sha256,
            "variable": "context_policy",
            "arms": arms,
            "model_id": DeterministicStubRuntime.model_id,
            "seed": seed,
        }
    )[:16]
    experiment_root = root / "experiments" / experiment_id
    experiment_root.mkdir(parents=True, exist_ok=True)
    experiment = {
        "experiment_id": experiment_id,
        "suite_sha256": suite_sha256,
        "variable": "context_policy",
        "arms": {name: asdict(config) for name, config in arms.items()},
        "model_id": DeterministicStubRuntime.model_id,
        "seed": seed,
        "code_commit": _code_commit(),
        "started_at": datetime.now(UTC),
    }
    _write_json(experiment_root / "experiment.json", experiment)
    summaries = {
        name: evaluate_suite(tasks, config, experiment_root / name) for name, config in arms.items()
    }
    summary = {
        "experiment_id": experiment_id,
        "variable": "context_policy",
        "arms": summaries,
    }
    _write_json(experiment_root / "summary.json", summary)
    return experiment_root, summary


def inspect_run(run_directory: str | Path) -> dict[str, object]:
    supplied = Path(run_directory)
    if supplied.is_symlink():
        raise ValueError("run directory must not be a symlink")
    run_dir = supplied.resolve(strict=True)
    if not run_dir.is_dir() or run_dir.parent.name != "runs":
        raise ValueError("run directory must be an artifacts/runs/<run-id> directory")
    store = RunStore.create(run_dir.parents[1], run_dir.name)
    if store.run_dir != run_dir:
        raise ValueError("run directory identity is invalid")

    run_value = store.read_json("run.json")
    grades_value = store.read_json("output/grades.json")
    trace_path = store.path_for("trace/events.jsonl")
    trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    if not isinstance(run_value, dict) or not isinstance(grades_value, dict):
        raise ValueError("run metadata and grades must be JSON objects")
    stages = [
        {
            "event": row.get("event_type"),
            "stage": row.get("stage"),
        }
        for row in trace
        if row.get("event_type") in {"stage.completed", "stage.skipped", "stage.failed"}
    ]
    return {
        "run_id": run_dir.name,
        "status": run_value.get("status"),
        "identity": run_value.get("identity", {}),
        "stages": stages,
        "validation": grades_value.get("validation", {}),
        "grades": {
            "passed": grades_value.get("passed", False),
            "completion": grades_value.get("completion", {}),
            "grounding": grades_value.get("grounding", {}),
            "pit": grades_value.get("pit", {}),
            "recovery": grades_value.get("recovery", {}),
        },
        "operations": grades_value.get("operations", {}),
    }


def load_tasks(path: str | Path) -> tuple[TaskSpec, ...]:
    return load_suite(Path(path))
