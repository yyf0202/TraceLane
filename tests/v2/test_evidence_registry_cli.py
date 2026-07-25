from __future__ import annotations

import dataclasses
import importlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import prepare_hist001_candidates as preparation
from tracelane.cli import main
from tracelane.contracts import canonical_json
from tracelane.evidence_registry import (
    EvidenceImportMetadata,
    EvidenceImportRow,
    EvidenceProject,
    import_acquisition_project,
    verify_evidence_registry,
)

EXPECTED_IMPORT_ROWS = {
    "1807-07-09": {
        "source_spec_id": "hist001_tilsit_treaty",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of a public-domain treaty and "
            "public-domain contemporary translation."
        ),
        "domains": ("diplomacy", "economy"),
        "fact_ids": (
            "diplomacy.duchy_of_warsaw",
            "diplomacy.tilsit_settlement",
            "economy.british_trade_exclusion",
        ),
        "role": "evidence",
    },
    "1806-11-21": {
        "source_spec_id": "hist001_continental_system_decrees",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of public-domain decrees; the "
            "source identifies the historical editions and translations."
        ),
        "domains": ("economy", "imperial-governance"),
        "fact_ids": (
            "economy.continental_system_scope",
            "economy.neutral_shipping_exposure",
            "imperial_governance.allied_enforcement",
        ),
        "role": "evidence",
    },
    "1807-12-17": {
        "source_spec_id": "hist001_continental_system_decrees",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of public-domain decrees; the "
            "source identifies the historical editions and translations."
        ),
        "domains": ("economy", "imperial-governance"),
        "fact_ids": (
            "economy.continental_system_scope",
            "economy.neutral_shipping_exposure",
            "imperial_governance.allied_enforcement",
        ),
        "role": "evidence",
    },
    "1810-12-19": {
        "source_spec_id": "hist001_russian_trade_1811",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase using the Presidential Library "
            "catalogue record for an 1810 State Council file; no archive image "
            "or modern anthology text is redistributed."
        ),
        "domains": ("diplomacy", "economy"),
        "fact_ids": (
            "diplomacy.franco_russian_trade_friction",
            "economy.russian_trade_rules_1811",
        ),
        "role": "evidence",
    },
    "1812-03-26": {
        "source_spec_id": "hist001_napoleon_supply_correspondence",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of public-domain Napoleonic "
            "correspondence; the modern page is used only as an archive locator "
            "and letter-number reference."
        ),
        "domains": ("logistics", "military"),
        "fact_ids": (
            "logistics.prewar_supply_plan",
            "military.niemen_consumption_boundary",
        ),
        "role": "evidence",
    },
    "1811-05-07": {
        "source_spec_id": "hist001_wellington_iberia_dispatch",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of a public-domain dispatch; no "
            "substantial verbatim text is redistributed."
        ),
        "domains": ("iberia",),
        "fact_ids": (
            "iberia.allied_force_commitment",
            "iberia.portuguese_finance_and_supply",
        ),
        "role": "evidence",
    },
    "1810-12-13": {
        "source_spec_id": "hist001_french_conscription_1811",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of a public-domain proposed decree and tables."
        ),
        "domains": ("imperial-governance", "military"),
        "fact_ids": (
            "imperial_governance.reserve_and_department_allocation",
            "military.conscription_scale_1811",
        ),
        "role": "evidence",
    },
    "1811": {
        "source_spec_id": "hist001_french_conscription_1811",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of a public-domain proposed decree and tables."
        ),
        "domains": ("imperial-governance", "military"),
        "fact_ids": (
            "imperial_governance.reserve_and_department_allocation",
            "military.conscription_scale_1811",
        ),
        "role": "evidence",
    },
    "1812-12-03": {
        "source_spec_id": "hist001_twenty_ninth_bulletin",
        "source_type": "primary",
        "license_basis": (
            "Repository-authored paraphrase of a public-domain military "
            "bulletin; retained only as a future-information leakage control."
        ),
        "domains": ("military",),
        "fact_ids": ("military.post_campaign_outcome",),
        "role": "future-control",
    },
}


def _hist001_project() -> EvidenceProject:
    return EvidenceProject.create(
        project_id="hist-001",
        title="Napoleon 1812 Counterfactual",
        research_question=(
            "How might European history have changed if Napoleon had not "
            "crossed the Niemen or launched the Russian campaign in 1812?"
        ),
        historical_cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        intervention="Napoleon does not cross the Niemen or launch the Russian campaign.",
        required_domains=(
            "diplomacy",
            "economy",
            "iberia",
            "imperial-governance",
            "logistics",
            "military",
        ),
        admitted_source_types=("primary",),
        status="active",
    )


@pytest.fixture
def registry_root(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    prepared = preparation.prepare(source)
    metadata = EvidenceImportMetadata.from_dict(
        json.loads(prepared.metadata_path.read_text(encoding="utf-8"))
    )
    target = tmp_path / "evidence"
    import_acquisition_project(source, target, _hist001_project(), metadata)
    return target


def _tree_state(root: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(root).as_posix(): (path.read_bytes() if path.is_file() else None)
        for path in root.rglob("*")
    }


def test_preparation_writes_valid_canonical_import_metadata(tmp_path: Path) -> None:
    result = preparation.prepare(tmp_path)

    assert dataclasses.is_dataclass(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.review_path = tmp_path  # type: ignore[misc]
    assert result.review_path.name == "candidate-review.md"
    assert result.metadata_path.name == "candidate-metadata.json"
    assert "PENDING USER REVIEW" in result.review_path.read_text(encoding="utf-8")

    metadata_bytes = result.metadata_path.read_bytes()
    metadata_value = json.loads(metadata_bytes)
    metadata = EvidenceImportMetadata.from_dict(metadata_value)
    assert metadata_bytes == (canonical_json(metadata.to_dict()).encode("utf-8") + b"\n")
    assert metadata.project_id == "hist-001"
    assert metadata.session_id == "acq_hist001_20260724"
    assert len(metadata.candidates) == 9
    assert tuple(row.candidate_id for row in metadata.candidates) == tuple(
        sorted(row.candidate_id for row in metadata.candidates)
    )
    assert len({row.source_spec_id for row in metadata.candidates}) == 7
    assert all(row.content_authorship == "repository_authored" for row in metadata.candidates)
    assert all(row.retention_policy == "paraphrase_only" for row in metadata.candidates)
    assert sum(row.role == "future-control" for row in metadata.candidates) == 1
    assert {row.source_spec_id for row in metadata.candidates if row.role == "future-control"} == {
        "hist001_twenty_ninth_bulletin"
    }

    serialized = metadata_bytes.decode("utf-8")
    assert str(tmp_path.resolve()) not in serialized


def test_preparation_metadata_rows_match_authenticated_candidates(tmp_path: Path) -> None:
    result = preparation.prepare(tmp_path)
    metadata = EvidenceImportMetadata.from_dict(
        json.loads(result.metadata_path.read_text(encoding="utf-8"))
    )
    candidate_dir = tmp_path / "acquisition" / metadata.session_id / "candidates"
    candidate_values = {
        value["candidate_id"]: value
        for value in (
            json.loads(path.read_text(encoding="utf-8")) for path in candidate_dir.glob("*.json")
        )
    }
    manifest = json.loads((candidate_dir.parent / "manifest.json").read_text(encoding="utf-8"))

    assert set(candidate_values) == {row.candidate_id for row in metadata.candidates}
    assert metadata.manifest_sha256 == manifest["content_sha256"]
    for row in metadata.candidates:
        candidate = candidate_values[row.candidate_id]
        assert row.candidate_record_sha256 == candidate["record_sha256"]
        assert row.candidate_content_sha256 == candidate["content_sha256"]
        expected = EXPECTED_IMPORT_ROWS[candidate["document_date"]]
        assert {
            "source_spec_id": row.source_spec_id,
            "source_type": row.source_type,
            "license_basis": row.license_basis,
            "domains": row.domains,
            "fact_ids": row.fact_ids,
            "role": row.role,
        } == expected
        assert row.content_authorship == "repository_authored"
        assert row.retention_policy == "paraphrase_only"


def test_evidence_list_has_stable_text_and_json_output(
    registry_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _tree_state(registry_root)
    arguments = [
        "evidence",
        "list",
        "--root",
        str(registry_root),
        "--project",
        "hist-001",
        "--status",
        "pending",
    ]

    assert main(arguments) == 0
    text_output = capsys.readouterr().out
    assert text_output.count("candidate_id=") == 9
    assert "status=pending" in text_output
    assert "role=future-control" in text_output

    assert main([*arguments, "--json"]) == 0
    json_output = capsys.readouterr().out
    values = json.loads(json_output)
    assert len(values) == 9
    assert values == sorted(values, key=lambda item: item["candidate_id"])
    assert all(item["effective_status"] == "pending" for item in values)
    assert _tree_state(registry_root) == before


def test_evidence_find_filters_fact_date_and_clean_view(
    registry_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _tree_state(registry_root)
    common = [
        "evidence",
        "find",
        "--root",
        str(registry_root),
        "--project",
        "hist-001",
        "--json",
    ]
    assert main([*common, "--fact", "logistics.prewar_supply_plan"]) == 0
    fact_values = json.loads(capsys.readouterr().out)
    assert len(fact_values) == 1
    assert fact_values[0]["document_date"] == "1812-03-26"

    assert main([*common, "--date-from", "1812", "--date-to", "1812", "--clean"]) == 0
    clean_values = json.loads(capsys.readouterr().out)
    assert [item["document_date"] for item in clean_values] == ["1812-03-26"]
    assert all(item["role"] != "future-control" for item in clean_values)
    assert _tree_state(registry_root) == before


def test_evidence_verify_has_stable_text_and_json_output(
    registry_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = [
        "evidence",
        "verify",
        "--root",
        str(registry_root),
        "--project",
        "hist-001",
    ]
    assert main(arguments) == 0
    output = capsys.readouterr().out
    assert output.startswith("projects=1 candidates=9 reviews=0 future_controls=1 ")
    assert "pending=9 approved=0 rejected=0 superseded=0" in output

    assert main([*arguments, "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["project_count"] == 1
    assert value["candidate_count"] == 9
    assert value["status_counts"] == {
        "approved": 0,
        "pending": 9,
        "rejected": 0,
        "superseded": 0,
    }


def test_evidence_rebuild_creates_only_missing_derived_files(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "rebuild"
    shutil.copytree(registry_root, target)
    index_path = target / "projects" / "hist-001" / "index.json"
    registry_path = target / "registry.json"
    expected_index = index_path.read_bytes()
    expected_registry = registry_path.read_bytes()
    index_path.unlink()
    registry_path.unlink()

    arguments = [
        "evidence",
        "rebuild-index",
        "--root",
        str(target),
        "--project",
        "hist-001",
        "--json",
    ]
    assert main(arguments) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["project_id"] == "hist-001"
    assert index_path.read_bytes() == expected_index
    assert registry_path.read_bytes() == expected_registry

    before = _tree_state(target)
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == value
    assert _tree_state(target) == before


@pytest.mark.parametrize("corrupt_name", ["index", "source"])
def test_evidence_rebuild_rejects_conflicts_without_partial_mutation(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    corrupt_name: str,
) -> None:
    target = tmp_path / f"corrupt-{corrupt_name}"
    shutil.copytree(registry_root, target)
    index_path = target / "projects" / "hist-001" / "index.json"
    registry_path = target / "registry.json"
    if corrupt_name == "index":
        index_path.write_bytes(b"{}\n")
    else:
        candidate_path = next((target / "projects" / "hist-001" / "candidates").glob("*.json"))
        candidate_path.write_bytes(b"{}\n")
        index_path.unlink()
        registry_path.unlink()
    before = _tree_state(target)

    assert (
        main(
            [
                "evidence",
                "rebuild-index",
                "--root",
                str(target),
                "--project",
                "hist-001",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(target.resolve()) not in captured.err
    assert _tree_state(target) == before


@pytest.mark.parametrize(
    "arguments",
    [
        ["evidence", "find", "--date-from", "1812-99"],
        ["evidence", "list", "--status", "not-a-status"],
    ],
)
def test_evidence_query_errors_are_stable_and_do_not_echo_paths(
    registry_root: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
) -> None:
    command = [
        arguments[0],
        arguments[1],
        "--root",
        str(registry_root),
        "--project",
        "hist-001",
        *arguments[2:],
    ]
    assert main(command) != 0
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(registry_root.resolve()) not in captured.err


def test_evidence_missing_project_and_corrupt_registry_are_stable_errors(
    registry_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "evidence",
                "verify",
                "--root",
                str(registry_root),
                "--project",
                "missing",
            ]
        )
        == 1
    )
    missing_error = capsys.readouterr().err
    assert "Traceback" not in missing_error
    assert str(registry_root.resolve()) not in missing_error

    (registry_root / "registry.json").write_bytes(b"{}\n")
    assert (
        main(
            [
                "evidence",
                "verify",
                "--root",
                str(registry_root),
                "--project",
                "hist-001",
            ]
        )
        == 1
    )
    corrupt_error = capsys.readouterr().err
    assert "Traceback" not in corrupt_error
    assert str(registry_root.resolve()) not in corrupt_error


def test_import_script_imports_and_verifies_locked_hist001_project(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence_import = importlib.import_module("scripts.import_hist001_evidence")
    source = tmp_path / "source"
    source.mkdir()
    preparation.prepare(source)
    target = tmp_path / "evidence"
    arguments = ["--source", str(source), "--target", str(target)]

    assert evidence_import.main(arguments) == 0
    output = capsys.readouterr().out
    fields = dict(item.split("=", 1) for item in output.split())
    assert fields.keys() == {
        "project",
        "candidates",
        "pending",
        "source_manifest_sha256",
        "project_index_sha256",
        "registry_sha256",
    }
    assert fields["project"] == "hist-001"
    assert fields["candidates"] == "9"
    assert fields["pending"] == "9"
    assert all(
        len(fields[name]) == 64
        for name in (
            "source_manifest_sha256",
            "project_index_sha256",
            "registry_sha256",
        )
    )
    assert str(source.resolve()) not in output
    assert str(target.resolve()) not in output

    assert evidence_import.main(arguments) == 0
    assert capsys.readouterr().out == output
    report = verify_evidence_registry(target, "hist-001")
    assert report.candidate_count == 9
    assert report.status_counts["pending"] == 9
    assert report.future_control_count == 1


def test_import_script_rejects_invalid_metadata_without_echo_or_target_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence_import = importlib.import_module("scripts.import_hist001_evidence")
    source = tmp_path / "sensitive-source"
    source.mkdir()
    result = preparation.prepare(source)
    metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))
    metadata["content_sha256"] = "0" * 64
    result.metadata_path.write_text(
        canonical_json(metadata) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "evidence"

    assert evidence_import.main(["--source", str(source), "--target", str(target)]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(source.resolve()) not in captured.err
    assert str(target.resolve()) not in captured.err
    assert not target.exists()


def test_import_script_rejects_semantically_substituted_valid_metadata(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence_import = importlib.import_module("scripts.import_hist001_evidence")
    source = tmp_path / "substituted-source"
    source.mkdir()
    result = preparation.prepare(source)
    original = EvidenceImportMetadata.from_dict(
        json.loads(result.metadata_path.read_text(encoding="utf-8"))
    )
    rows = list(original.candidates)
    changed_value = rows[0].to_dict()
    changed_value["source_spec_id"] = "hist001_substituted_source"
    rows[0] = EvidenceImportRow.from_dict(changed_value)
    substituted = EvidenceImportMetadata.create(
        project_id=original.project_id,
        session_id=original.session_id,
        manifest_sha256=original.manifest_sha256,
        candidates=tuple(rows),
    )
    result.metadata_path.write_text(
        canonical_json(substituted.to_dict()) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "evidence"

    assert evidence_import.main(["--source", str(source), "--target", str(target)]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(source.resolve()) not in captured.err
    assert not target.exists()


def test_import_script_rejects_substituted_license_basis(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence_import = importlib.import_module("scripts.import_hist001_evidence")
    source = tmp_path / "license-source"
    source.mkdir()
    result = preparation.prepare(source)
    original = EvidenceImportMetadata.from_dict(
        json.loads(result.metadata_path.read_text(encoding="utf-8"))
    )
    rows = list(original.candidates)
    changed_value = rows[0].to_dict()
    changed_value["license_basis"] = "Repository-authored replacement license decision."
    rows[0] = EvidenceImportRow.from_dict(changed_value)
    substituted = EvidenceImportMetadata.create(
        project_id=original.project_id,
        session_id=original.session_id,
        manifest_sha256=original.manifest_sha256,
        candidates=tuple(rows),
    )
    result.metadata_path.write_text(
        canonical_json(substituted.to_dict()) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "evidence"

    assert evidence_import.main(["--source", str(source), "--target", str(target)]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(source.resolve()) not in captured.err
    assert not target.exists()
