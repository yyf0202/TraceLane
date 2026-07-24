from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from tracelane.acquisition.contracts import EvidenceCandidate, compute_candidate_id
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
