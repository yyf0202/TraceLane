from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tests/fixtures/coding_tasks/day3_plan_acceptance_v3.py"
RUNNER = ROOT / "scripts/resume_day3_after_br11_plan_gate_v3.py"
AMENDMENT = (
    ROOT / "fixtures/coding/bericher-v0.9/day3-br11-plan-gate-v3.json"
)
sys.path.insert(0, str(ROOT / "scripts"))

import resume_day3_after_br11_plan_gate_v3 as recovery  # noqa: E402
import run_day3_coding_eval as day3  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_plan_gate_v3_replay_uses_distinct_layer() -> None:
    spec = recovery.replay_spec()
    assert spec.run_slug.endswith(recovery.REPLAY_SUFFIX)
    assert "gate-replay" in spec.run_slug
    assert spec.run_slug != recovery.SOURCE_PLAN_RUN


def test_plan_gate_v3_replaces_only_interrupted_next_attempt() -> None:
    original = day3.matrix()
    index = next(
        index
        for index, row in enumerate(original)
        if row.run_slug == recovery.INTERRUPTED_NEXT
    )
    remaining = recovery.remaining_matrix()
    assert remaining[0].run_slug == (
        recovery.INTERRUPTED_NEXT + "-" + recovery.NEXT_RESTART_SUFFIX
    )
    assert remaining[1:] == original[index + 1 :]


def test_plan_gate_v3_amendment_hashes() -> None:
    value = json.loads(AMENDMENT.read_text(encoding="utf-8"))
    frozen = value["frozen_inputs"]
    assert _sha256(GATE) == frozen["plan_gate_sha256"]
    assert _sha256(RUNNER) == frozen["resume_runner_sha256"]
