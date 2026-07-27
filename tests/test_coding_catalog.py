from __future__ import annotations

import json
from pathlib import Path

from tracelane.coding import load_coding_task

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "fixtures" / "coding" / "bericher-v0.1" / "suite.json"
SUITE_V2 = ROOT / "fixtures" / "coding" / "bericher-v0.2" / "suite.json"


def test_bericher_frozen_suite_loads_all_tasks() -> None:
    manifest = json.loads(SUITE.read_text(encoding="utf-8"))
    tasks = [
        load_coding_task(json.loads((SUITE.parent / relative).read_text(encoding="utf-8")))
        for relative in manifest["tasks"]
    ]

    assert manifest["schema_version"] == "frozen-coding-suite/v0.1"
    assert [task.task_id for task in tasks] == [
        "BR-01-pit-value-date",
        "BR-02-expanded-static-detection",
        "BR-03-auto-compact-daily-run",
        "BR-04-expanded-static-inference",
    ]
    assert len({task.task_sha256 for task in tasks}) == len(tasks)
    assert all(task.acceptance.public_commands for task in tasks)
    assert all(task.acceptance.hidden_commands for task in tasks)
    assert all(task.diff_policy.editable_paths for task in tasks)
    assert all(task.max_model_tokens > 0 for task in tasks)


def test_bericher_v2_adds_high_complexity_causal_task() -> None:
    manifest = json.loads(SUITE_V2.read_text(encoding="utf-8"))
    tasks = [
        load_coding_task(json.loads((SUITE_V2.parent / relative).read_text(encoding="utf-8")))
        for relative in manifest["tasks"]
    ]

    assert [task.task_id for task in tasks] == [
        "BR-01-pit-value-date",
        "BR-02-expanded-static-detection",
        "BR-03-auto-compact-daily-run",
        "BR-04-expanded-static-inference",
        "BR-05-t1-causality-alignment",
    ]
    assert tasks[0].version == 2
    br05 = tasks[-1]
    assert br05.diff_policy.editable_paths == (
        "src/backtest/engine.py",
        "src/components/models.py",
        "src/components/target_generator.py",
    )
    assert "data/**" in br05.diff_policy.protected_paths
    assert br05.max_model_tokens == 220_000
