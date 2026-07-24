from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracelane.artifacts import RunIdentity, RunStore, compute_run_id


def identity(*, repeat: int = 1, model_id: str = "deterministic-stub-v1") -> RunIdentity:
    return RunIdentity(
        task_sha256="a" * 64,
        bundle_sha256="b" * 64,
        config_sha256="c" * 64,
        model_id=model_id,
        repeat=repeat,
    )


def test_run_id_is_stable_and_repeat_changes_identity() -> None:
    first = identity(repeat=1)
    assert first.run_id == compute_run_id("a" * 64, "b" * 64, "c" * 64, "deterministic-stub-v1", 1)
    assert first.run_id == identity(repeat=1).run_id
    assert first.run_id != identity(repeat=2).run_id


def test_run_store_writes_canonical_json_atomically(tmp_path: Path) -> None:
    store = RunStore.create(tmp_path, identity().run_id)
    target = store.write_json("input/task.json", {"b": 2, "a": 1})
    assert target == store.run_dir / "input" / "task.json"
    assert target.read_text(encoding="utf-8") == '{"a":1,"b":2}\n'
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert list(target.parent.glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "name",
    [
        "../outside.json",
        "input/../../outside.json",
        "D:/outside.json",
        "D:\\outside.json",
    ],
)
def test_run_store_rejects_path_traversal(tmp_path: Path, name: str) -> None:
    store = RunStore.create(tmp_path, identity().run_id)
    with pytest.raises(ValueError, match="relative|escapes"):
        store.write_json(name, {"unsafe": True})


def test_run_store_rejects_invalid_run_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run_id"):
        RunStore.create(tmp_path, "../escape")


def test_append_jsonl_is_parseable_and_lf_terminated(tmp_path: Path) -> None:
    store = RunStore.create(tmp_path, identity().run_id)
    path = store.append_jsonl("trace/events.jsonl", {"sequence": 1})
    store.append_jsonl("trace/events.jsonl", {"sequence": 2})
    assert [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] == [
        {"sequence": 1},
        {"sequence": 2},
    ]
    assert b"\r\n" not in path.read_bytes()
