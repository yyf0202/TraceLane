from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tracelane.history.loader import (
    freeze_history_evidence,
    load_evidence_manifest,
    load_history_case,
    load_history_suite,
)

FIXTURES_V02 = Path(__file__).parents[2] / "fixtures" / "v0.2"


def test_hist001_has_locked_cutoff_domains_sources_and_future_control() -> None:
    development = load_history_suite(FIXTURES_V02, "development")
    heldout = load_history_suite(FIXTURES_V02, "heldout")
    entry = next(item for item in development if item.scenario_id == "hist-001/clean")
    case = load_history_case(entry.case_ref_path)
    bundle = freeze_history_evidence(
        case,
        load_evidence_manifest(entry.evidence_manifest_path),
    )

    assert case.case_id == "hist-001"
    assert case.cutoff_at == datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC)
    assert case.intervention == "Napoleon does not cross the Niemen or launch the Russian campaign."
    assert set(case.required_domains) == {
        "diplomacy",
        "military",
        "logistics",
        "economy",
        "iberia",
        "imperial_governance",
    }
    assert len(bundle.records) == 6
    assert bundle.rejected_future_ids == ("hist-001-ev-future-0001",)
    assert all(record.available_at <= case.cutoff_at for record in bundle.records)
    assert all(record.license and record.source_locator for record in bundle.records)
    assert {item.scenario_id for item in development} == {
        "hist-001/clean",
        "hist-001/fault/future-leakage",
        "hist-001/fault/ambiguous-source-contract",
    }
    assert {item.scenario_id for item in heldout} == {"hist-001/fault/logistics-context-omission"}
