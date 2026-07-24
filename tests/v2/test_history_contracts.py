from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from tracelane.history.contracts import (
    EvidenceManifest,
    EvidenceRecordV2,
    compute_evidence_provenance_sha256,
    compute_history_bundle_sha256,
)
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.source import source_locator_sha256


def artifact_ref_value(
    kind: str = "evidence_blob",
    *,
    media_type: str = "text/plain",
    schema_id: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": kind,
        "uri": "tracelane://fixtures/v0.2/history/hist-001/evidence/blob.txt",
        "media_type": media_type,
        "sha256": "a" * 64,
        "size_bytes": 12,
    }
    if schema_id is not None:
        value["schema_id"] = schema_id
    return value


def evidence_record_value() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_id": "tracelane://schemas/evidence-record/v2",
        "schema_version": "2.0.0",
        "evidence_id": "hist-001-ev-0001",
        "document_date": "1812-05",
        "date_precision": "month",
        "available_at": "1812-05-31T23:59:59Z",
        "known_by_cutoff": "plausibly_known",
        "source_type": "primary",
        "source_title": "Correspondance de Napoléon",
        "source_locator": "https://history.example/source",
        "source_locator_sha256": source_locator_sha256("https://history.example/source"),
        "curator": "curator-001",
        "candidate_id": "candidate_" + "a" * 24,
        "candidate_record_sha256": "b" * 64,
        "review_sha256": "c" * 64,
        "candidate_ref": artifact_ref_value(
            "evidence_candidate",
            media_type="application/json",
            schema_id="tracelane://schemas/evidence-candidate/v2",
        ),
        "review_ref": artifact_ref_value(
            "candidate_review",
            media_type="application/json",
            schema_id="tracelane://schemas/candidate-review/v2",
        ),
        "license": "Public-Domain",
        "excerpt_kind": "paraphrased",
        "content_ref": artifact_ref_value(),
        "fact_ids": ["logistics.prewar_supply"],
        "transformation_refs": [],
    }
    value["provenance_sha256"] = compute_evidence_provenance_sha256(value)
    return value


def evidence_manifest_value(
    transformation_ref: dict[str, object] | None = None,
) -> dict[str, object]:
    record_ref = artifact_ref_value(
        "evidence_record",
        media_type="application/json",
        schema_id="tracelane://schemas/evidence-record/v2",
    )
    transformations = [] if transformation_ref is None else [transformation_ref]
    cutoff = datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC)
    value: dict[str, object] = {
        "schema_id": "tracelane://schemas/evidence-manifest/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "case_id": "hist-001",
        "cutoff_at": "1812-06-23T23:59:59Z",
        "record_refs": [record_ref],
        "rejected_future_refs": [],
        "source_licenses": {"hist-001-ev-0001": "Public-Domain"},
        "transformation_refs": transformations,
        "bundle_sha256": compute_history_bundle_sha256(
            case_id="hist-001",
            cutoff_at=cutoff,
            record_refs=(record_ref,),
            rejected_future_refs=(),
            transformation_refs=transformations,
            source_licenses={"hist-001-ev-0001": "Public-Domain"},
        ),
    }
    value["content_sha256"] = content_digest(value)
    return value


@pytest.mark.parametrize(
    "malformed",
    [
        artifact_ref_value("evidence_blob"),
        artifact_ref_value(
            "evidence_transformation",
            schema_id="tracelane://schemas/case/v2",
        ),
    ],
)
def test_evidence_manifest_from_dict_rejects_malformed_transformation_ref(
    malformed: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="transformation"):
        EvidenceManifest.from_dict(evidence_manifest_value(malformed))


@pytest.mark.parametrize(
    "malformed",
    [
        ArtifactRef.from_dict(artifact_ref_value("evidence_blob")),
        ArtifactRef.from_dict(
            artifact_ref_value(
                "evidence_transformation",
                schema_id="tracelane://schemas/case/v2",
            )
        ),
    ],
)
def test_evidence_manifest_python_invariant_rejects_malformed_transformation_ref(
    malformed: ArtifactRef,
) -> None:
    manifest = EvidenceManifest.from_dict(evidence_manifest_value())

    with pytest.raises(ValueError, match="transformation"):
        replace(manifest, transformation_refs=(malformed,))


@pytest.mark.parametrize(
    "malformed",
    [
        ArtifactRef.from_dict(artifact_ref_value("evidence_blob")),
        ArtifactRef.from_dict(
            artifact_ref_value(
                "evidence_transformation",
                schema_id="tracelane://schemas/case/v2",
            )
        ),
    ],
)
def test_evidence_manifest_to_dict_rejects_malformed_transformation_ref(
    malformed: ArtifactRef,
) -> None:
    manifest = EvidenceManifest.from_dict(evidence_manifest_value())
    object.__setattr__(manifest, "transformation_refs", (malformed,))

    with pytest.raises(ValueError, match="transformation"):
        manifest.to_dict()


def test_evidence_record_preserves_date_precision_and_provenance() -> None:
    record = EvidenceRecordV2.from_dict(evidence_record_value())

    assert record.document_date == "1812-05"
    assert record.date_precision == "month"
    assert record.known_by_cutoff == "plausibly_known"
    assert record.content_ref.kind == "evidence_blob"
    assert record.source_locator == "https://history.example/source"


def test_evidence_record_rejects_precision_mismatched_document_date() -> None:
    value = evidence_record_value()
    value["date_precision"] = "day"
    value["provenance_sha256"] = compute_evidence_provenance_sha256(value)

    with pytest.raises(ValueError, match="document_date|date_precision"):
        EvidenceRecordV2.from_dict(value)


def test_evidence_record_rejects_duplicate_fact_ids() -> None:
    value = evidence_record_value()
    value["fact_ids"] = ["logistics.prewar_supply", "logistics.prewar_supply"]
    value["provenance_sha256"] = compute_evidence_provenance_sha256(value)

    try:
        EvidenceRecordV2.from_dict(value)
    except ValueError as exc:
        assert "fact_ids" in str(exc)
    else:
        raise AssertionError("duplicate fact_ids must be rejected")


def test_evidence_record_rejects_arbitrary_provenance_digest() -> None:
    value = evidence_record_value()
    value["provenance_sha256"] = "b" * 64

    with pytest.raises(ValueError, match="provenance"):
        EvidenceRecordV2.from_dict(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("evidence_id", "hist-001-ev-0002"),
        ("document_date", "1813-05"),
        ("date_precision", "estimated"),
        ("available_at", "1812-06-01T00:00:00Z"),
        ("known_by_cutoff", "known"),
        ("source_type", "secondary"),
        ("source_title", "A different primary source"),
        ("source_locator", "https://history.example/alternate"),
        ("source_locator_sha256", "d" * 64),
        ("curator", "curator-002"),
        ("candidate_id", "candidate_" + "d" * 24),
        ("candidate_record_sha256", "d" * 64),
        ("review_sha256", "d" * 64),
        (
            "candidate_ref",
            artifact_ref_value(
                "evidence_candidate",
                media_type="application/json",
                schema_id="tracelane://schemas/evidence-candidate/v2",
            )
            | {"sha256": "d" * 64},
        ),
        (
            "review_ref",
            artifact_ref_value(
                "candidate_review",
                media_type="application/json",
                schema_id="tracelane://schemas/candidate-review/v2",
            )
            | {"sha256": "d" * 64},
        ),
        ("license", "CC0-1.0"),
        ("excerpt_kind", "verbatim"),
        ("content_ref", artifact_ref_value() | {"sha256": "d" * 64}),
        ("fact_ids", ["logistics.alternate_supply"]),
        (
            "transformation_refs",
            [artifact_ref_value("evidence_transformation")],
        ),
    ],
)
def test_each_provenance_field_rejects_an_independent_stale_digest(
    field: str,
    replacement: object,
) -> None:
    value = deepcopy(evidence_record_value())
    value[field] = replacement

    with pytest.raises(ValueError, match="provenance"):
        EvidenceRecordV2.from_dict(value)


def test_evidence_record_rejects_legacy_license_spelling() -> None:
    value = evidence_record_value()
    value["license"] = "Public domain"
    value["provenance_sha256"] = compute_evidence_provenance_sha256(value)

    with pytest.raises(ValueError, match="license"):
        EvidenceRecordV2.from_dict(value)


def test_evidence_record_to_dict_rechecks_provenance() -> None:
    value = evidence_record_value()
    content_ref = value["content_ref"]
    assert isinstance(content_ref, dict)
    content_ref["schema_id"] = "tracelane://schemas/evidence-record/v2"
    value["provenance_sha256"] = compute_evidence_provenance_sha256(value)
    record = EvidenceRecordV2.from_dict(value)
    object.__setattr__(record, "provenance_sha256", "b" * 64)

    with pytest.raises(ValueError, match="provenance"):
        record.to_dict()


def test_evidence_record_to_dict_omits_absent_artifact_schema() -> None:
    value = evidence_record_value()

    assert EvidenceRecordV2.from_dict(value).to_dict() == value


def test_evidence_record_from_dict_rejects_malformed_transformation_ref() -> None:
    value = evidence_record_value()
    value["transformation_refs"] = [artifact_ref_value("transformation_record")]
    value["provenance_sha256"] = compute_evidence_provenance_sha256(value)

    with pytest.raises(ValueError, match="transformation"):
        EvidenceRecordV2.from_dict(value)


def test_evidence_record_to_dict_rejects_malformed_transformation_ref() -> None:
    record = EvidenceRecordV2.from_dict(evidence_record_value())
    malformed = ArtifactRef.from_dict(artifact_ref_value("transformation_record"))
    value = record.to_dict()
    value["transformation_refs"] = [malformed.to_dict()]
    provenance = compute_evidence_provenance_sha256(value)
    mutated = replace(
        record,
        transformation_refs=(malformed,),
        provenance_sha256=provenance,
    )

    with pytest.raises(ValueError, match="transformation"):
        mutated.to_dict()
