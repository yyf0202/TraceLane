from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.v2.test_history_loader import build_suite
from tracelane.history.loader import (
    freeze_history_evidence,
    load_evidence_manifest,
    load_history_case,
    load_history_suite,
)

FIXTURES_V02 = Path(__file__).parents[2] / "fixtures" / "v0.2"


def test_hist001_has_locked_cutoff_domains_sources_and_future_control() -> None:
    assert FIXTURES_V02.is_dir(), (
        "release gate: tracked fixtures/v0.2 is absent and has not been approved"
    )
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


def test_generated_v02_shape_passes_schema_loader_and_provenance_closure(
    tmp_path: Path,
) -> None:
    generated = tmp_path / "generated-v0.2"
    build_suite(
        generated,
        rejected_available_at="1812-06-25T00:00:00Z",
    )

    development = load_history_suite(generated, "development")
    heldout = load_history_suite(generated, "heldout")
    assert len(development) == 1
    assert heldout == ()
    entry = development[0]
    case = load_history_case(entry.case_ref_path)
    bundle = freeze_history_evidence(
        case,
        load_evidence_manifest(entry.evidence_manifest_path),
    )

    assert case.case_id == "hist-001"
    assert case.cutoff_at == datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC)
    assert len(bundle.records) == 1
    assert bundle.rejected_future_ids == ("hist-001-ev-0002",)
    assert all(record.available_at <= case.cutoff_at for record in bundle.records)
    assert all(record.license and record.source_locator for record in bundle.records)
