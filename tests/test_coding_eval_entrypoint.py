from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import coding_eval  # noqa: E402


def test_validate_results_checks_complete_run_store(tmp_path: Path) -> None:
    run_id = "a" * 64
    run = tmp_path / "runs" / run_id
    for relative in (
        "input/coding-task.json",
        "input/attempt.json",
        "workspace/final.json",
        "output/provider-cost.json",
    ):
        target = run / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n", encoding="utf-8")
    results = tmp_path / "results.json"
    results.write_text(
        json.dumps({"attempts": [{"attempt_id": "attempt-1", "run_id": run_id}]}),
        encoding="utf-8",
    )
    assert coding_eval.validate_results([results]) == {
        "schema_version": "coding-eval-validation/v0.1",
        "result_files": 1,
        "attempts": 1,
        "run_stores": 1,
        "valid": True,
    }


def test_validate_results_rejects_duplicate_attempt_ids(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps({"attempts": [{"attempt_id": "same"}]}), encoding="utf-8")
    second.write_text(json.dumps({"attempts": [{"attempt_id": "same"}]}), encoding="utf-8")
    try:
        coding_eval.validate_results([first, second])
    except ValueError as exc:
        assert "duplicate attempt IDs" in str(exc)
    else:
        raise AssertionError("duplicate attempt IDs were accepted")
