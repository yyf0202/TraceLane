"""Loaders for deterministic decision suites.

A decision suite extends a plain task suite with, per task, the analyst roster
that should research it and the point-in-time-true resolution the world later
produced.  Keeping the resolution in the suite (not in the harness) is what
lets the decision → outcome → feedback loop run offline and reproducibly.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from tracelane.contracts import TaskSpec, load_task
from tracelane.decision_orchestrator import AnalystSpec
from tracelane.spine import Resolution
from tracelane.spine.experiments import DecisionTaskSpec


def _non_empty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _load_analysts(value: object, task_id: str) -> tuple[AnalystSpec, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"decision task {task_id} requires a non-empty analysts list")
    analysts: list[AnalystSpec] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError(f"decision task {task_id} analyst must be an object")
        abstains = bool(item.get("abstains", False))
        direction = str(item.get("direction_hint", "neutral"))
        if direction not in {"bullish", "bearish", "neutral"}:
            raise ValueError(f"decision task {task_id} analyst direction_hint is invalid")
        confidence = item.get("confidence_hint", 0.5)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError(f"decision task {task_id} analyst confidence_hint is invalid")
        analysts.append(
            AnalystSpec(
                analyst_id=_non_empty(item.get("analyst_id"), "analyst_id"),
                role=_non_empty(item.get("role"), "role"),
                direction_hint=direction,
                confidence_hint=float(confidence),
                abstains=abstains,
            )
        )
    ids = [a.analyst_id for a in analysts]
    if len(set(ids)) != len(ids):
        raise ValueError(f"decision task {task_id} has duplicate analyst_id values")
    return tuple(analysts)


def _load_resolution(value: object, task_id: str) -> Resolution:
    if not isinstance(value, Mapping):
        raise ValueError(f"decision task {task_id} requires a resolution object")
    actual = value.get("actual_direction")
    if actual is not None and actual not in {"bullish", "bearish", "neutral"}:
        raise ValueError(f"decision task {task_id} resolution actual_direction is invalid")
    metric_value = value.get("metric_value")
    if metric_value is not None and (
        isinstance(metric_value, bool) or not isinstance(metric_value, (int, float))
    ):
        raise ValueError(f"decision task {task_id} resolution metric_value is invalid")
    return Resolution(
        subject=task_id,
        actual_direction=actual,  # type: ignore[arg-type]
        metric_name=(str(value["metric_name"]) if value.get("metric_name") else None),
        metric_value=(float(metric_value) if metric_value is not None else None),
        invalid_reason=(str(value["invalid_reason"]) if value.get("invalid_reason") else None),
    )


def load_decision_suite(path: str | Path) -> tuple[DecisionTaskSpec, ...]:
    """Load a directory of decision task files into typed specs.

    Each file extends a standard task document with ``analysts`` and
    ``resolution`` members.  Files named ``manifest.json`` /
    ``split-manifest.json`` are ignored, matching the plain suite loader.
    """
    supplied_root = Path(path)
    if supplied_root.is_symlink():
        raise ValueError("decision suite directory must not be a symlink")
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("decision suite path must be a directory")

    task_paths = sorted(
        candidate
        for candidate in root.glob("*.json")
        if candidate.name not in {"manifest.json", "split-manifest.json"}
    )
    if not task_paths:
        raise ValueError("decision suite contains no task files")

    specs: list[DecisionTaskSpec] = []
    for task_path in task_paths:
        if task_path.is_symlink():
            raise ValueError(f"decision task file must not be a symlink: {task_path.name}")
        resolved = task_path.resolve(strict=True)
        if resolved.parent != root:
            raise ValueError(f"decision task file escapes suite directory: {task_path.name}")
        value = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise ValueError(f"decision task file must contain an object: {task_path.name}")
        analysts = _load_analysts(value.get("analysts"), str(value.get("task_id", task_path.stem)))
        resolution = _load_resolution(
            value.get("resolution"), str(value.get("task_id", task_path.stem))
        )
        # Strip the decision-only members before validating against the task
        # schema, which forbids additional properties.
        task_value = {k: v for k, v in value.items() if k not in {"analysts", "resolution"}}
        task: TaskSpec = load_task(task_value)
        specs.append(DecisionTaskSpec(task=task, analysts=analysts, resolution=resolution))

    ids = [spec.task.task_id for spec in specs]
    if len(set(ids)) != len(ids):
        raise ValueError("decision suite contains duplicate task_id values")
    return tuple(sorted(specs, key=lambda spec: spec.task.task_id))
