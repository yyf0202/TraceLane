from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tracelane.acquisition.contracts import compute_candidate_id
from tracelane.evidence_registry.contracts import (
    EvidenceImportMetadata,
    EvidenceImportRow,
    EvidenceProject,
    EvidenceTransformation,
    ProjectEvidenceCandidate,
    candidate_record_digest,
)
from tracelane.v2.contracts import ArtifactRef


def blob_ref(*, digest: str = "a" * 64) -> ArtifactRef:
    return ArtifactRef.from_dict(
        {
            "kind": "evidence_blob",
            "uri": f"tracelane://evidence/blobs/sha256/{digest}",
            "media_type": "text/plain",
            "sha256": digest,
            "size_bytes": 12,
        }
    )


def transformation_ref(*, digest: str = "b" * 64) -> ArtifactRef:
    return ArtifactRef.from_dict(
        {
            "kind": "evidence_transformation",
            "uri": f"tracelane://artifacts/transformations/{digest}.json",
            "media_type": "application/json",
            "sha256": digest,
            "size_bytes": 12,
        }
    )


@pytest.fixture
def project_input() -> dict[str, object]:
    return {
        "project_id": "hist-001",
        "title": "The 1812 campaign",
        "research_question": "What was known before the campaign?",
        "historical_cutoff_at": datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        "intervention": "Provide source-grounded answers.",
        "required_domains": ("diplomacy", "logistics"),
        "admitted_source_types": ("primary", "secondary"),
        "status": "active",
    }


@pytest.fixture
def candidate_input() -> dict[str, object]:
    content_ref = blob_ref()
    candidate_id = compute_candidate_id(
        query="treaty of tils(it)",
        title="Treaty of Tilsit",
        source_url="https://history.example/treaty",
        document_date="1807-07-07",
        date_precision="day",
        content_sha256=content_ref.sha256,
    )
    return {
        "project_id": "hist-001",
        "candidate_id": candidate_id,
        "source_spec_id": "hist001_tilsit_treaty",
        "query": "treaty of tils(it)",
        "title": "Treaty of Tilsit",
        "source_url": "https://history.example/treaty",
        "document_date": "1807-07-07",
        "date_precision": "day",
        "retrieved_at": datetime(2026, 7, 24, tzinfo=UTC),
        "curator": "curator-001",
        "source_type": "primary",
        "role": "evidence",
        "domains": ("diplomacy", "treaties"),
        "fact_ids": ("diplomacy.tilsit",),
        "content_ref": content_ref,
        "transformation_refs": (transformation_ref(),),
        "content_authorship": "repository_authored",
        "retention_policy": "paraphrase_only",
        "license_basis": "Public Domain",
        "acquisition_session_id": "acq_hist001_20260724",
        "source_candidate_uri": "tracelane://artifacts/candidates/tilsit.json",
        "source_candidate_id": candidate_id,
        "source_candidate_record_sha256": "c" * 64,
        "source_candidate_content_sha256": content_ref.sha256,
    }


def test_project_round_trip(project_input: dict[str, object]) -> None:
    project = EvidenceProject.create(**project_input)

    assert EvidenceProject.from_dict(project.to_dict()) == project


def test_project_rejects_invalid_id_and_noncanonical_cutoff(
    project_input: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="project_id"):
        EvidenceProject.create(**(project_input | {"project_id": "History-001"}))
    with pytest.raises(ValueError, match="date-time"):
        EvidenceProject.from_dict(
            EvidenceProject.create(**project_input).to_dict()
            | {"historical_cutoff_at": "1812-06-23T23:59:59+00:00"}
        )


def test_project_rejects_stale_record_digest(project_input: dict[str, object]) -> None:
    value = EvidenceProject.create(**project_input).to_dict()
    value["record_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="record"):
        EvidenceProject.from_dict(value)


def test_project_candidate_round_trip(candidate_input: dict[str, object]) -> None:
    candidate = ProjectEvidenceCandidate.create(**candidate_input)

    assert ProjectEvidenceCandidate.from_dict(candidate.to_dict()) == candidate
    assert "status" not in candidate.to_dict()


def test_candidate_rejects_mismatched_lineage(candidate_input: dict[str, object]) -> None:
    candidate_input["source_candidate_content_sha256"] = "d" * 64

    with pytest.raises(ValueError, match="lineage|content"):
        ProjectEvidenceCandidate.create(**candidate_input)


@pytest.mark.parametrize("field", ["domains", "fact_ids"])
def test_candidate_rejects_duplicate_or_unsorted_wire_values(
    candidate_input: dict[str, object], field: str
) -> None:
    candidate_input[field] = ("zeta", "alpha")
    with pytest.raises(ValueError, match=field):
        ProjectEvidenceCandidate.create(**candidate_input)
    candidate_input[field] = ("alpha", "alpha")
    with pytest.raises(ValueError, match=field):
        ProjectEvidenceCandidate.create(**candidate_input)


def test_third_party_content_requires_positive_retention(
    candidate_input: dict[str, object],
) -> None:
    candidate_input["content_authorship"] = "third_party"
    candidate_input["retention_policy"] = "paraphrase_only"
    with pytest.raises(ValueError, match="retention policy"):
        ProjectEvidenceCandidate.create(**candidate_input)


def test_candidate_role_is_schema_bound(candidate_input: dict[str, object]) -> None:
    candidate_input["role"] = "control"
    with pytest.raises(ValueError, match="role"):
        ProjectEvidenceCandidate.create(**candidate_input)


def test_future_control_candidate_round_trip(candidate_input: dict[str, object]) -> None:
    candidate_input["role"] = "future-control"

    assert ProjectEvidenceCandidate.create(**candidate_input).role == "future-control"


def test_candidate_to_dict_rejects_stale_instance(candidate_input: dict[str, object]) -> None:
    candidate = ProjectEvidenceCandidate.create(**candidate_input)
    stale = replace(candidate, record_sha256="d" * 64)

    with pytest.raises(ValueError, match="record"):
        stale.to_dict()


def test_transformation_round_trip_and_typed_refs(candidate_input: dict[str, object]) -> None:
    candidate = ProjectEvidenceCandidate.create(**candidate_input)
    transformation = EvidenceTransformation.create(
        project_id=candidate.project_id,
        candidate_id=candidate.candidate_id,
        transformation_type="translation",
        input_ref=blob_ref(),
        output_ref=blob_ref(digest="d" * 64),
        actor="translator-001",
        method="human translation",
        parameters={"target_language": "en", "quality": 1.0},
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
        license_implications="Translation is retained as a paraphrase.",
    )

    assert EvidenceTransformation.from_dict(transformation.to_dict()) == transformation
    assert transformation.transformation_id.startswith("transformation_")


def test_transformation_rejects_same_or_invalid_typed_refs() -> None:
    common = {
        "project_id": "hist-001",
        "candidate_id": "candidate_" + "a" * 24,
        "transformation_type": "ocr",
        "input_ref": blob_ref(),
        "output_ref": blob_ref(),
        "actor": "operator-001",
        "method": "OCR",
        "parameters": {},
        "created_at": datetime(2026, 7, 24, tzinfo=UTC),
        "license_implications": "None.",
    }
    with pytest.raises(ValueError, match="different"):
        EvidenceTransformation.create(**common)
    with pytest.raises(ValueError, match="input"):
        EvidenceTransformation.create(**(common | {"input_ref": transformation_ref()}))


def test_import_metadata_round_trip(candidate_input: dict[str, object]) -> None:
    candidate = ProjectEvidenceCandidate.create(**candidate_input)
    row = EvidenceImportRow.from_candidate(candidate)
    metadata = EvidenceImportMetadata.create(
        project_id=candidate.project_id,
        session_id=candidate.acquisition_session_id,
        manifest_sha256="e" * 64,
        candidates=(row,),
    )

    assert EvidenceImportMetadata.from_dict(metadata.to_dict()) == metadata


def test_import_metadata_rejects_unsorted_rows(candidate_input: dict[str, object]) -> None:
    candidate = ProjectEvidenceCandidate.create(**candidate_input)
    row = EvidenceImportRow.from_candidate(candidate)
    duplicate = replace(row, candidate_id="candidate_" + "b" * 24)

    with pytest.raises(ValueError, match="candidates"):
        EvidenceImportMetadata.create(
            project_id=candidate.project_id,
            session_id=candidate.acquisition_session_id,
            manifest_sha256="e" * 64,
            candidates=(duplicate, row),
        )


def test_candidate_record_digest_excludes_only_record_digest() -> None:
    value = {"record_sha256": "0" * 64, "content_sha256": "a" * 64, "field": "value"}
    changed = deepcopy(value)
    changed["record_sha256"] = "b" * 64

    assert candidate_record_digest(value) == candidate_record_digest(changed)
