from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import resume_day3_after_adjudication as recovery  # noqa: E402
import run_day3_coding_eval as day3  # noqa: E402

AMENDMENT = (
    ROOT / "fixtures/coding/bericher-v0.9/day3-adjudication-v2.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_resume_replaces_only_operator_interrupted_slot() -> None:
    original = day3.matrix()
    remaining = recovery.remaining_matrix()
    interrupted_index = next(
        index
        for index, row in enumerate(original)
        if row.run_slug == recovery.INTERRUPTED
    )
    assert remaining[0].run_slug == (
        recovery.INTERRUPTED + "-" + recovery.RESTART_SUFFIX
    )
    assert remaining[1:] == original[interrupted_index + 1 :]
    assert len(remaining) == len(original) - interrupted_index


def test_adjudication_amendment_freezes_new_inputs() -> None:
    value = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert value["status"] == "frozen-before-resume"
    assert _sha256(
        ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v2.py"
    ) == frozen["plan_adjudicator_sha256"]
    assert _sha256(
        ROOT / "tests/fixtures/coding_tasks/br10_hidden_acceptance_v2.py"
    ) == frozen["functional_adjudicator_sha256"]
    assert _sha256(
        ROOT / "scripts/resume_day3_after_adjudication.py"
    ) == frozen["resume_runner_sha256"]
