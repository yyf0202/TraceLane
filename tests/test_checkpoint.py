from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_artifacts import identity
from tracelane.artifacts import RunIdentity, RunStore
from tracelane.checkpoint import CheckpointStore


def checkpoint_store(tmp_path: Path) -> tuple[CheckpointStore, RunIdentity, RunStore]:
    expected = identity()
    run_store = RunStore.create(tmp_path, expected.run_id)
    return CheckpointStore(run_store, expected), expected, run_store


def test_checkpoint_chain_loads_latest_trusted_state(tmp_path: Path) -> None:
    checkpoints, expected, _ = checkpoint_store(tmp_path)
    first = checkpoints.save("gather", {"records": 2})
    second = checkpoints.save("analyze", {"facts": ["fact-1"]})
    loaded = checkpoints.load_latest(expected)
    assert loaded == second
    assert first.previous_checkpoint_sha256 is None
    assert second.previous_checkpoint_sha256 == first.checkpoint_sha256
    assert second.completed_stages == ("gather", "analyze")


def test_checkpoint_hash_changes_when_state_changes(tmp_path: Path) -> None:
    first_store, _, _ = checkpoint_store(tmp_path / "first")
    second_store, _, _ = checkpoint_store(tmp_path / "second")
    first = first_store.save("gather", {"records": 1})
    second = second_store.save("gather", {"records": 2})
    assert first.state_sha256 != second.state_sha256
    assert first.checkpoint_sha256 != second.checkpoint_sha256


def test_checkpoint_rejects_identity_mismatch(tmp_path: Path) -> None:
    checkpoints, _, _ = checkpoint_store(tmp_path)
    checkpoints.save("gather", {"records": 2})
    wrong = identity(model_id="different-model")
    with pytest.raises(ValueError, match="identity"):
        checkpoints.load_latest(wrong)


def test_checkpoint_rejects_tampered_state(tmp_path: Path) -> None:
    checkpoints, expected, run_store = checkpoint_store(tmp_path)
    saved = checkpoints.save("gather", {"records": 2})
    path = next((run_store.run_dir / "checkpoints").glob("*.json"))
    value = json.loads(path.read_text(encoding="utf-8"))
    value["state"]["records"] = 999
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="state hash"):
        checkpoints.load_latest(expected)
    assert saved.state["records"] == 2


def test_partial_temporary_checkpoint_is_ignored(tmp_path: Path) -> None:
    checkpoints, expected, run_store = checkpoint_store(tmp_path)
    saved = checkpoints.save("gather", {"records": 2})
    temporary = run_store.run_dir / "checkpoints" / ".0002-analyze.json.partial.tmp"
    temporary.write_text('{"incomplete":', encoding="utf-8")
    assert checkpoints.load_latest(expected) == saved
