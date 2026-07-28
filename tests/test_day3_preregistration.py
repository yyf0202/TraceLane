from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_day3_coding_eval as experiment  # noqa: E402

PREREG = ROOT / "fixtures/coding/bericher-v0.9/day3-experiment-v2.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_day3_matrix_matches_frozen_order_and_balance() -> None:
    value = json.loads(PREREG.read_text(encoding="utf-8"))
    rows = experiment.matrix()
    assert [row.run_slug for row in rows] == value["attempt_order"]
    assert len(rows) == 36
    assert {
        (row.task.short_id, row.model, row.repeat, row.workflow) for row in rows
    } == {
        (task, model, repeat, workflow)
        for task in value["tasks"]
        for model in value["models"]
        for repeat in (1, 2)
        for workflow in value["workflows"]
    }
    for index in range(0, len(rows), 2):
        first, second = rows[index : index + 2]
        assert (first.task, first.model, first.repeat) == (
            second.task,
            second.model,
            second.repeat,
        )
        assert first.workflow != second.workflow


def test_day3_frozen_input_hashes_match() -> None:
    value = json.loads(PREREG.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert _sha256(experiment.HARNESS) == frozen["harness_manifest_sha256"]
    assert _sha256(experiment.PLAN_GATE) == frozen["plan_gate_sha256"]
    assert (
        _sha256(ROOT / "scripts/run_opencode_coding_attempt.py")
        == frozen["attempt_runner_sha256"]
    )
    assert (
        _sha256(ROOT / "scripts/run_day2_coding_eval.py")
        == frozen["shared_execution_engine_sha256"]
    )
    assert (
        _sha256(ROOT / "scripts/run_day3_coding_eval.py")
        == frozen["matrix_runner_sha256"]
    )
    assert (
        _sha256(ROOT / "scripts/prepare_opencode_plan_handoff.py")
        == frozen["plan_handoff_sha256"]
    )
    for task in experiment.TASKS:
        assert _sha256(task.manifest) == frozen["task_manifest_sha256"][task.short_id]
        assert _sha256(task.grader) == frozen["hidden_grader_sha256"][task.short_id]


def test_day3_v2_preserves_and_excludes_infrastructure_pilot() -> None:
    value = json.loads(PREREG.read_text(encoding="utf-8"))
    assert value["supersedes"] == "tracelane-opencode-day3"
    excluded = value["excluded_infrastructure_pilot"]
    assert [row["state"] for row in excluded] == [
        "completed",
        "build_request_not_dispatched",
        "operator_interrupted",
    ]
