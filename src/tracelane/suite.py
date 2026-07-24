from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from tracelane.contracts import TaskSpec, load_task

_NON_TASK_FILES = frozenset({"manifest.json", "split-manifest.json"})


def _reject_json_constant(token: str) -> object:
    raise ValueError(f"non-finite JSON constant is forbidden: {token}")


def load_suite(path: Path) -> tuple[TaskSpec, ...]:
    supplied_root = Path(path)
    if supplied_root.is_symlink():
        raise ValueError("suite directory must not be a symlink")
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("suite path must be a directory")

    task_paths = sorted(
        candidate for candidate in root.glob("*.json") if candidate.name not in _NON_TASK_FILES
    )
    if not task_paths:
        raise ValueError("suite contains no task files")

    tasks: list[TaskSpec] = []
    for task_path in task_paths:
        if task_path.is_symlink():
            raise ValueError(f"task file must not be a symlink: {task_path.name}")
        resolved = task_path.resolve(strict=True)
        if resolved.parent != root:
            raise ValueError(f"task file escapes suite directory: {task_path.name}")
        value = json.loads(
            resolved.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
        if not isinstance(value, Mapping):
            raise ValueError(f"task file must contain an object: {task_path.name}")
        tasks.append(load_task(value))

    task_ids = [task.task_id for task in tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("suite contains duplicate task_id values")
    return tuple(sorted(tasks, key=lambda task: task.task_id))
