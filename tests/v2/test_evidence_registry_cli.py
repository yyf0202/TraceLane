from __future__ import annotations

import dataclasses
import importlib
import json
import os
import shutil
import subprocess
import sys
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
    EvidenceQuery,
    find_evidence,
    import_acquisition_project,
    verify_evidence_registry,
)
from tracelane.evidence_registry import index as evidence_index

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
    target = tmp_path / "evidence"
    if os.name != "nt":
        checked_in = Path(__file__).resolve().parents[2] / "evidence"
        shutil.copytree(checked_in, target)
        return target

    source = tmp_path / "source"
    source.mkdir()
    prepared = preparation.prepare(source)
    metadata = EvidenceImportMetadata.from_dict(
        json.loads(prepared.metadata_path.read_text(encoding="utf-8"))
    )
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
    entries = find_evidence(
        registry_root,
        EvidenceQuery("hist-001", statuses=("pending",)),
    )
    values = [entry.to_dict() for entry in entries]
    expected_text = "".join(
        f"candidate_id={value['candidate_id']} "
        f"status={value['effective_status']} "
        f"role={value['role']} "
        f"date={value['document_date']} "
        f"source_type={value['source_type']} "
        f"domains={','.join(value['domains'])} "
        f"facts={','.join(value['fact_ids'])}\n"
        for value in values
    )
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
    assert text_output == expected_text

    assert main([*arguments, "--json"]) == 0
    json_output = capsys.readouterr().out
    assert json_output == canonical_json(values) + "\n"
    assert values == sorted(values, key=lambda item: item["candidate_id"])
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
    fact_output = capsys.readouterr().out
    fact_values = json.loads(fact_output)
    assert fact_output == canonical_json(fact_values) + "\n"
    assert len(fact_values) == 1
    assert fact_values[0]["document_date"] == "1812-03-26"

    assert main([*common, "--date-from", "1812", "--date-to", "1812", "--clean"]) == 0
    clean_output = capsys.readouterr().out
    clean_values = json.loads(clean_output)
    assert clean_output == canonical_json(clean_values) + "\n"
    assert [item["document_date"] for item in clean_values] == ["1812-03-26"]
    assert all(item["role"] != "future-control" for item in clean_values)
    assert _tree_state(registry_root) == before


def test_evidence_verify_has_stable_text_and_json_output(
    registry_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _tree_state(registry_root)
    report = verify_evidence_registry(registry_root, "hist-001")
    expected_value = {
        "candidate_count": report.candidate_count,
        "future_control_count": report.future_control_count,
        "project_count": report.project_count,
        "project_index_sha256": report.project_index_sha256,
        "registry_sha256": report.registry_sha256,
        "review_count": report.review_count,
        "status_counts": dict(report.status_counts),
    }
    expected_text = (
        "projects=1 candidates=9 reviews=0 future_controls=1 "
        "pending=9 approved=0 rejected=0 superseded=0 "
        f"registry_sha256={report.registry_sha256} "
        f"project_index_sha256={report.project_index_sha256}\n"
    )
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
    assert output == expected_text

    assert main([*arguments, "--json"]) == 0
    json_output = capsys.readouterr().out
    assert json_output == canonical_json(expected_value) + "\n"
    assert _tree_state(registry_root) == before


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
    json_output = capsys.readouterr().out
    value = json.loads(json_output)
    assert json_output == canonical_json(value) + "\n"
    assert value["project_id"] == "hist-001"
    assert index_path.read_bytes() == expected_index
    assert registry_path.read_bytes() == expected_registry

    before = _tree_state(target)
    text_arguments = [item for item in arguments if item != "--json"]
    assert main(text_arguments) == 0
    assert capsys.readouterr().out == (
        "project=hist-001 "
        f"project_index_sha256={value['project_index_sha256']} "
        f"registry_sha256={value['registry_sha256']}\n"
    )
    assert _tree_state(target) == before


def test_evidence_rebuild_repairs_index_without_registry(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "reverse-asymmetry"
    shutil.copytree(registry_root, target)
    index_path = target / "projects" / "hist-001" / "index.json"
    registry_path = target / "registry.json"
    expected_index = index_path.read_bytes()
    expected_registry = registry_path.read_bytes()
    registry_path.unlink()

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
        == 0
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert index_path.read_bytes() == expected_index
    assert registry_path.read_bytes() == expected_registry
    assert verify_evidence_registry(target, "hist-001").candidate_count == 9


def test_evidence_rebuild_rolls_back_index_after_late_registry_failure(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "late-failure"
    shutil.copytree(registry_root, target)
    (target / "projects" / "hist-001" / "index.json").unlink()
    (target / "registry.json").unlink()
    before = _tree_state(target)
    original_publish = evidence_index._publish_derived_json

    def fail_registry(*args, **kwargs):
        if args[1] == "tracelane://evidence/registry.json":
            raise ValueError("injected registry publication failure")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(
        evidence_index,
        "_publish_derived_json",
        fail_registry,
    )

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
    assert captured.err == "tracelane: error: evidence rebuild-index failed\n"
    assert _tree_state(target) == before


def test_evidence_rebuild_preflights_other_project_before_writing(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "broken-other-project"
    shutil.copytree(registry_root, target)
    (target / "projects" / "hist-001" / "index.json").unlink()
    (target / "registry.json").unlink()
    (target / "projects" / "broken-project").mkdir()
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
    assert capsys.readouterr().err == "tracelane: error: evidence rebuild-index failed\n"
    assert _tree_state(target) == before


def test_evidence_rebuild_does_not_delete_replaced_file_during_rollback(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "rollback-replacement"
    shutil.copytree(registry_root, target)
    index_path = target / "projects" / "hist-001" / "index.json"
    registry_path = target / "registry.json"
    index_path.unlink()
    registry_path.unlink()
    replacement = b"external replacement\n"
    original_publish = evidence_index._publish_derived_json

    def replace_before_failure(*args, **kwargs):
        if args[1] == "tracelane://evidence/registry.json":
            competing = index_path.with_suffix(".competing")
            competing.write_bytes(replacement)
            os.replace(competing, index_path)
            raise ValueError("injected registry publication failure")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(
        evidence_index,
        "_publish_derived_json",
        replace_before_failure,
    )

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
    assert capsys.readouterr().err == "tracelane: error: evidence rebuild-index failed\n"
    assert index_path.read_bytes() == replacement
    assert not registry_path.exists()


def test_evidence_rebuild_does_not_delete_identical_new_identity_during_rollback(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "rollback-identical-replacement"
    shutil.copytree(registry_root, target)
    index_path = target / "projects" / "hist-001" / "index.json"
    registry_path = target / "registry.json"
    index_path.unlink()
    registry_path.unlink()
    original_publish = evidence_index._publish_derived_json

    def replace_before_failure(*args, **kwargs):
        if args[1] == "tracelane://evidence/registry.json":
            original_bytes = index_path.read_bytes()
            replacement = index_path.with_suffix(".replacement")
            replacement.write_bytes(original_bytes)
            os.replace(replacement, index_path)
            raise ValueError("injected registry publication failure")
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(
        evidence_index,
        "_publish_derived_json",
        replace_before_failure,
    )

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
    assert capsys.readouterr().err == "tracelane: error: evidence rebuild-index failed\n"
    assert index_path.exists()
    assert not registry_path.exists()


def test_evidence_rebuild_does_not_rollback_identical_race_owned_by_other_writer(
    registry_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "identical-publication-race"
    shutil.copytree(registry_root, target)
    index_path = target / "projects" / "hist-001" / "index.json"
    registry_path = target / "registry.json"
    index_path.unlink()
    registry_path.unlink()
    original_publish = evidence_index._publish_derived_json

    def race_then_fail(*args, **kwargs):
        root, uri, _kind, _schema_id, value = args
        if uri.endswith("/index.json"):
            root.ensure_parent(root.resolve(uri))
            root.resolve(uri).write_bytes((canonical_json(value) + "\n").encode())
            receipt = original_publish(*args, **kwargs)
            assert receipt.changed_by_this_call is False
            return receipt
        raise ValueError("injected registry publication failure")

    monkeypatch.setattr(
        evidence_index,
        "_publish_derived_json",
        race_then_fail,
    )

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
    assert capsys.readouterr().err == "tracelane: error: evidence rebuild-index failed\n"
    assert index_path.exists()
    assert not registry_path.exists()


@pytest.mark.parametrize("corrupt_name", ["index", "source"])
def test_evidence_rebuild_repairs_derived_state_but_rejects_corrupt_source(
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

    result = main(
        [
            "evidence",
            "rebuild-index",
            "--root",
            str(target),
            "--project",
            "hist-001",
        ]
    )
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(target.resolve()) not in captured.err
    if corrupt_name == "index":
        assert result == 0
        assert captured.err == ""
        assert _tree_state(target) != before
        assert verify_evidence_registry(target, "hist-001").candidate_count == 9
    else:
        assert result == 1
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


@pytest.mark.skipif(
    os.name != "nt",
    reason="acquisition import is a Windows-only capability",
)
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


def test_import_script_direct_entrypoint_loads_installed_locked_manifest() -> None:
    script = Path(__file__).resolve().parents[2] / "scripts" / "import_hist001_evidence.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=script.parent.parent,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.startswith("usage: import_hist001_evidence.py ")
    assert "Traceback" not in completed.stdout


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


def test_import_script_rejects_coherent_future_control_substitution(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_import = importlib.import_module("scripts.import_hist001_evidence")
    substituted = dataclasses.replace(
        preparation.SPECS[-1],
        title="Fabricated post-cutoff bulletin",
        source_url="https://history.example/fabricated-bulletin",
        document_date="1812-11-30",
        note=(
            "A repository-authored fabricated future-control note whose "
            "candidate, content, manifest, and metadata digests are coherent."
        ),
    )
    monkeypatch.setattr(
        preparation,
        "SPECS",
        (*preparation.SPECS[:-1], substituted),
    )
    source = tmp_path / "coherent-substitution"
    source.mkdir()
    preparation.prepare(source)
    target = tmp_path / "evidence"

    assert evidence_import.main(["--source", str(source), "--target", str(target)]) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(source.resolve()) not in captured.err
    assert not target.exists()


@pytest.mark.skipif(
    os.name != "nt",
    reason="acquisition import is a Windows-only capability",
)
def test_import_script_has_no_post_commit_reverification_window(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_import = importlib.import_module("scripts.import_hist001_evidence")
    source = tmp_path / "source"
    source.mkdir()
    preparation.prepare(source)
    target = tmp_path / "evidence"
    verification_calls = 0

    def fail_reverification(*args, **kwargs):
        nonlocal verification_calls
        verification_calls += 1
        raise ValueError("injected post-commit verification failure")

    monkeypatch.setattr(
        evidence_import,
        "verify_evidence_registry",
        fail_reverification,
        raising=False,
    )

    assert evidence_import.main(["--source", str(source), "--target", str(target)]) == 0
    assert verification_calls == 0
    assert "project=hist-001" in capsys.readouterr().out
    assert verify_evidence_registry(target, "hist-001").candidate_count == 9


@pytest.mark.skipif(
    os.name != "nt",
    reason="acquisition import is a Windows-only capability",
)
def test_import_script_stdout_value_error_cannot_reverse_committed_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_import = importlib.import_module("scripts.import_hist001_evidence")
    source = tmp_path / "source"
    source.mkdir()
    preparation.prepare(source)
    target = tmp_path / "evidence"

    class ClosedOutput:
        def write(self, value: str) -> int:
            raise ValueError("I/O operation on closed file")

        def flush(self) -> None:
            pass

    monkeypatch.setattr(evidence_import.sys, "stdout", ClosedOutput())

    assert evidence_import.main(["--source", str(source), "--target", str(target)]) == 0
    assert verify_evidence_registry(target, "hist-001").candidate_count == 9
