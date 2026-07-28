from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tracelane.coding import load_coding_task

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "fixtures/coding/bericher-v0.9/suite.json"


def test_v09_freezes_three_new_weighted_complex_tasks() -> None:
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    new_tasks = [
        path for path in suite["tasks"] if path.startswith("tasks/BR-1")
    ]
    assert len(new_tasks) == 3
    loaded = [
        load_coding_task(
            json.loads((SUITE.parent / relative).read_text(encoding="utf-8"))
        )
        for relative in new_tasks
    ]
    assert [task.task_id[:5] for task in loaded] == ["BR-10", "BR-11", "BR-12"]
    assert all(len(task.diff_policy.editable_paths) >= 2 for task in loaded)
    assert all(task.max_model_tokens == 2_000_000 for task in loaded)


def test_v09_hidden_grader_hashes_are_frozen() -> None:
    expected = {
        "br10_hidden_acceptance.py": (
            "8f5f9cf5aef17fd591c729b2284dea6ffdd9ab6035a6d54eec69fd37c4e571a6"
        ),
        "br11_hidden_acceptance.py": (
            "9417a2eb956b37b29b0a626b0bba33e1cb47f105db195e48b637e5eedfbd83d0"
        ),
        "br12_hidden_acceptance.py": (
            "82d615c0907857884d5968e515f5ac7cee8734d07d38fd14072591dcd0d8f5e5"
        ),
    }
    for name, digest in expected.items():
        source = ROOT / "tests/fixtures/coding_tasks" / name
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
