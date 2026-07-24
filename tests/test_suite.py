from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from tests.test_contracts import TASK
from tracelane.suite import load_suite


def write_task(path: Path, *, task_id: str) -> None:
    value = deepcopy(TASK)
    value["task_id"] = task_id
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_suite_orders_tasks_by_id_and_ignores_manifests(tmp_path: Path) -> None:
    write_task(tmp_path / "second.json", task_id="summary-002")
    write_task(tmp_path / "first.json", task_id="summary-001")
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "split-manifest.json").write_text("{}\n", encoding="utf-8")
    tasks = load_suite(tmp_path)
    assert tuple(task.task_id for task in tasks) == ("summary-001", "summary-002")


def test_suite_rejects_duplicate_task_ids(tmp_path: Path) -> None:
    write_task(tmp_path / "first.json", task_id="summary-001")
    write_task(tmp_path / "duplicate.json", task_id="summary-001")
    with pytest.raises(ValueError, match="duplicate task_id"):
        load_suite(tmp_path)


def test_suite_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no task files"):
        load_suite(tmp_path)


def test_suite_rejects_task_symlink(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    write_task(source, task_id="summary-source")
    suite = tmp_path / "suite"
    suite.mkdir()
    link = suite / "linked.json"
    try:
        os.symlink(source, link)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows host")
    with pytest.raises(ValueError, match="symlink"):
        load_suite(suite)
