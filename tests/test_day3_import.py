from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import import_day3_coding_eval as importer  # noqa: E402
import run_day3_coding_eval as experiment  # noqa: E402


def test_day3_resolution_covers_every_frozen_slot() -> None:
    resolution = importer._resolution()
    rows = experiment.matrix()
    assert len(rows) == 36
    for row in rows:
        terminal, phases = importer._primary_phases(row, resolution)
        assert (importer.RAW_ROOT / terminal / "workflow-end.json").is_file()
        assert phases


def test_day3_resolution_keeps_replays_and_integrity_exclusions_separate() -> None:
    resolution = importer._resolution()
    assert len(resolution["gate_replays"]) == 3
    assert set(resolution["budget_integrity_exclusions"]) == {
        "day3v2-br-11-dsv4pro-r1-plan-build",
        "day3v2-br-11-k2.7code-r1-direct-build",
        "day3v2-br-11-glm52-r1-direct-build",
    }
    assert len(resolution["provider_failures"]) == 3
