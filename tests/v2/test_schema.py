from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from tracelane.acquisition.contracts import EvidenceCandidate, compute_candidate_id
from tracelane.evidence_registry.contracts import (
    EvidenceImportMetadata,
    EvidenceImportRow,
    EvidenceProject,
    EvidenceTransformation,
    ProjectEvidenceCandidate,
)
from tracelane.v2.contracts import ArtifactRef
from tracelane.v2.schema import SchemaValidationError, validate_document


def artifact_ref_value(**overrides: object) -> Mapping[str, object]:
    value: dict[str, object] = {
        "kind": "evidence_record",
        "uri": "tracelane://fixtures/v0.2/history/hist-001/case.json",
        "media_type": "application/json",
        "sha256": "a" * 64,
        "size_bytes": 42,
        "schema_id": "tracelane://schemas/case/v2",
    }
    value.update(overrides)
    return value


def test_schema_error_has_code_schema_id_and_json_pointer() -> None:
    with pytest.raises(SchemaValidationError) as captured:
        validate_document("artifact-ref", {"kind": "evidence_record"})

    assert captured.value.code == "schema_validation_failed"
    assert captured.value.schema_id == "tracelane://schemas/artifact-ref/v2"
    assert captured.value.pointer == "/"


def test_schema_rejects_unknown_fields() -> None:
    with pytest.raises(SchemaValidationError) as captured:
        validate_document("artifact-ref", artifact_ref_value(hidden="not-allowed"))

    assert captured.value.pointer == "/"
    assert "Additional properties" in str(captured.value)


def test_schema_rejects_non_finite_values_before_validation() -> None:
    with pytest.raises(ValueError, match="canonical JSON"):
        validate_document("artifact-ref", artifact_ref_value(size_bytes=float("nan")))


def test_unknown_schema_name_is_rejected_without_path_escape() -> None:
    with pytest.raises(ValueError, match="schema name"):
        validate_document("../task", artifact_ref_value())


def test_json_schema_rejects_invalid_date_time_format() -> None:
    value = {
        "schema_id": "tracelane://schemas/acquisition-session/v2",
        "schema_version": "2.0.0",
        "content_sha256": "a" * 64,
        "session_id": "acq_hist001_20260724",
        "mode": "codex_manual",
        "created_at": "2026-07-24T00:00:00Z",
        "network_access_available_to_agent": False,
        "candidate_refs": [],
        "review_refs": [],
        "promoted_record_refs": [],
    }
    value["created_at"] = "not-a-date"
    with pytest.raises(SchemaValidationError, match="date-time"):
        validate_document("acquisition-session", value)


def acquisition_session_value(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_id": "tracelane://schemas/acquisition-session/v2",
        "schema_version": "2.0.0",
        "content_sha256": "a" * 64,
        "session_id": "acq_hist001_20260724",
        "mode": "codex_manual",
        "created_at": "2026-07-24T00:00:00Z",
        "network_access_available_to_agent": False,
        "candidate_refs": [],
        "review_refs": [],
        "promoted_record_refs": [],
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-07-24T01:00:00+01:00",
        "2026-02-30T00:00:00Z",
    ],
)
def test_json_schema_rejects_noncanonical_or_invalid_date_time(created_at: str) -> None:
    with pytest.raises(SchemaValidationError, match="date-time"):
        validate_document("acquisition-session", acquisition_session_value(created_at=created_at))


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-07-24T00:00:00Z",
        "2026-07-24T00:00:00.123Z",
    ],
)
def test_json_schema_accepts_canonical_utc_date_time(created_at: str) -> None:
    validate_document("acquisition-session", acquisition_session_value(created_at=created_at))


@pytest.mark.parametrize(
    "uri",
    [
        "tracelane://fixtures/../secret",
        "tracelane://fixtures/%2e%2e/secret",
        "tracelane://fixtures\\secret",
        "tracelane://fixtures//secret",
    ],
)
def test_artifact_ref_schema_rejects_unsafe_uri(uri: str) -> None:
    with pytest.raises(SchemaValidationError):
        validate_document("artifact-ref", artifact_ref_value(uri=uri))


@pytest.mark.parametrize(
    "uri",
    [
        "tracelane://fixtures/v0.2/history/hist-001/case.json",
        "tracelane://artifacts/runs/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/checksums.json",
    ],
)
def test_artifact_ref_schema_accepts_safe_uri_segments(uri: str) -> None:
    validate_document("artifact-ref", artifact_ref_value(uri=uri))


@pytest.mark.parametrize(
    "schema_name",
    ["evidence-candidate", "evidence-record"],
)
def test_evidence_json_schemas_reject_malformed_transformation_ref(
    schema_name: str,
) -> None:
    malformed = artifact_ref_value(
        kind="transformation_record",
        media_type="text/plain",
        schema_id=None,
    )
    malformed.pop("schema_id", None)
    if schema_name == "evidence-candidate":
        content_ref = artifact_ref_value(
            kind="evidence_blob",
            media_type="text/plain",
            schema_id=None,
        )
        content_ref.pop("schema_id", None)
        candidate_id = compute_candidate_id(
            query="query",
            title="title",
            source_url="https://history.example/source",
            document_date="1812-05",
            date_precision="month",
            content_sha256="a" * 64,
        )
        value = EvidenceCandidate.create(
            candidate_id=candidate_id,
            query="query",
            title="title",
            source_url="https://history.example/source",
            document_date="1812-05",
            date_precision="month",
            retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
            curator="curator",
            transformation_refs=(),
            content_ref=ArtifactRef.from_dict(content_ref),
        ).to_dict()
    else:
        from tests.v2.test_history_contracts import evidence_record_value

        value = evidence_record_value()
    value["transformation_refs"] = [malformed]

    with pytest.raises(SchemaValidationError):
        validate_document(schema_name, value)


@pytest.mark.parametrize(
    ("kind", "schema_id"),
    [
        ("evidence_blob", None),
        ("evidence_transformation", "tracelane://schemas/case/v2"),
    ],
)
def test_evidence_manifest_schema_rejects_malformed_transformation_ref(
    kind: str,
    schema_id: str | None,
) -> None:
    from tests.v2.test_history_contracts import evidence_manifest_value

    malformed = dict(
        artifact_ref_value(
            kind=kind,
            media_type="text/plain",
            schema_id=schema_id,
        )
    )
    if schema_id is None:
        malformed.pop("schema_id")

    with pytest.raises(SchemaValidationError):
        validate_document("evidence-manifest", evidence_manifest_value(malformed))


def registry_blob_ref(*, digest: str = "a" * 64) -> ArtifactRef:
    return ArtifactRef.from_dict(
        {
            "kind": "evidence_blob",
            "uri": f"tracelane://evidence/blobs/sha256/{digest}",
            "media_type": "text/plain",
            "sha256": digest,
            "size_bytes": 12,
        }
    )


def registry_candidate_value() -> dict[str, object]:
    content_ref = registry_blob_ref()
    candidate_id = compute_candidate_id(
        query="query",
        title="title",
        source_url="https://history.example/source",
        document_date="1812-05",
        date_precision="month",
        content_sha256=content_ref.sha256,
    )
    return ProjectEvidenceCandidate.create(
        project_id="hist-001",
        candidate_id=candidate_id,
        source_spec_id="hist001_source",
        query="query",
        title="title",
        source_url="https://history.example/source",
        document_date="1812-05",
        date_precision="month",
        retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
        curator="curator",
        source_type="primary",
        role="evidence",
        domains=("diplomacy",),
        fact_ids=("diplomacy.fact",),
        content_ref=content_ref,
        transformation_refs=(),
        content_authorship="repository_authored",
        retention_policy="paraphrase_only",
        license_basis="Public Domain",
        acquisition_session_id="acq_hist001_20260724",
        source_candidate_uri="tracelane://artifacts/candidates/source.json",
        source_candidate_id=candidate_id,
        source_candidate_record_sha256="b" * 64,
        source_candidate_content_sha256=content_ref.sha256,
    ).to_dict()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_type", "tertiary"),
        ("role", "control"),
        ("content_authorship", "unknown"),
        ("retention_policy", "full_text"),
        ("record_sha256", "A" * 64),
    ],
)
def test_project_candidate_schema_covers_enums_and_digest_pattern(
    field: str, replacement: str
) -> None:
    value = registry_candidate_value()
    value[field] = replacement

    with pytest.raises(SchemaValidationError):
        validate_document("project-evidence-candidate", value)


def test_project_schema_covers_status_source_types_and_duplicate_arrays() -> None:
    project = EvidenceProject.create(
        project_id="hist-001",
        title="title",
        research_question="question",
        historical_cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        intervention="intervention",
        required_domains=("diplomacy",),
        admitted_source_types=("primary",),
        status="active",
    ).to_dict()
    for field, replacement in (
        ("status", "retired"),
        ("admitted_source_types", ["tertiary"]),
        ("required_domains", ["diplomacy", "diplomacy"]),
    ):
        invalid = dict(project)
        invalid[field] = replacement
        with pytest.raises(SchemaValidationError):
            validate_document("evidence-project", invalid)


def test_transformation_and_metadata_schemas_cover_typed_refs_and_rows() -> None:
    candidate = registry_candidate_value()
    transformation = EvidenceTransformation.create(
        project_id="hist-001",
        candidate_id=str(candidate["candidate_id"]),
        transformation_type="ocr",
        input_ref=registry_blob_ref(),
        output_ref=registry_blob_ref(digest="c" * 64),
        actor="operator",
        method="OCR",
        parameters={"options": [True, None, 1]},
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
        license_implications="None.",
    ).to_dict()
    transformation["input_ref"] = artifact_ref_value(kind="evidence_transformation")
    with pytest.raises(SchemaValidationError):
        validate_document("evidence-transformation", transformation)

    parsed_candidate = ProjectEvidenceCandidate.from_dict(candidate)
    metadata = EvidenceImportMetadata.create(
        project_id="hist-001",
        session_id="acq_hist001_20260724",
        manifest_sha256="d" * 64,
        candidates=(EvidenceImportRow.from_candidate(parsed_candidate),),
    ).to_dict()
    metadata["candidates"] = [metadata["candidates"][0], metadata["candidates"][0]]
    with pytest.raises(SchemaValidationError):
        validate_document("evidence-import-metadata", metadata)


@pytest.mark.parametrize(
    "schema_name",
    [
        "evidence-project",
        "project-evidence-candidate",
        "evidence-transformation",
        "evidence-import-metadata",
    ],
)
def test_evidence_registry_schemas_reject_missing_required_and_unknown_fields(
    schema_name: str,
) -> None:
    values: dict[str, dict[str, object]] = {
        "evidence-project": EvidenceProject.create(
            project_id="hist-001",
            title="title",
            research_question="question",
            historical_cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
            intervention="intervention",
            required_domains=("diplomacy",),
            admitted_source_types=("primary",),
            status="active",
        ).to_dict(),
        "project-evidence-candidate": registry_candidate_value(),
        "evidence-transformation": EvidenceTransformation.create(
            project_id="hist-001",
            candidate_id=str(registry_candidate_value()["candidate_id"]),
            transformation_type="normalization",
            input_ref=registry_blob_ref(),
            output_ref=registry_blob_ref(digest="e" * 64),
            actor="operator",
            method="normalization",
            parameters={},
            created_at=datetime(2026, 7, 24, tzinfo=UTC),
            license_implications="None.",
        ).to_dict(),
        "evidence-import-metadata": EvidenceImportMetadata.create(
            project_id="hist-001",
            session_id="acq_hist001_20260724",
            manifest_sha256="f" * 64,
            candidates=(EvidenceImportRow.from_candidate(ProjectEvidenceCandidate.from_dict(registry_candidate_value())),),
        ).to_dict(),
    }
    value = values[schema_name]
    missing = dict(value)
    missing.pop("schema_id")
    with pytest.raises(SchemaValidationError):
        validate_document(schema_name, missing)
    with pytest.raises(SchemaValidationError):
        validate_document(schema_name, value | {"unexpected": True})
