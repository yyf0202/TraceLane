from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.v2.contracts import (
    ArtifactRef,
    content_digest,
    validate_transformation_ref,
)
from tracelane.v2.schema import validate_document, validate_document_date
from tracelane.v2.source import canonical_source_url, source_locator_sha256

_PROVENANCE_FIELDS = (
    "evidence_id",
    "document_date",
    "date_precision",
    "available_at",
    "known_by_cutoff",
    "source_type",
    "source_title",
    "source_locator",
    "source_locator_sha256",
    "curator",
    "candidate_id",
    "candidate_record_sha256",
    "review_sha256",
    "candidate_ref",
    "review_ref",
    "content_ref",
    "fact_ids",
    "transformation_refs",
    "license",
    "excerpt_kind",
)
_LICENSES = frozenset(
    {
        "Public-Domain",
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "LicenseRef-Research-Excerpt",
    }
)


def _unique_non_empty(values: Sequence[str], label: str, *, required: bool = False) -> None:
    if required and not values:
        raise ValueError(f"{label} must not be empty")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must contain unique values")


def _artifact_ref(value: object, label: str) -> ArtifactRef:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an artifact reference")
    return ArtifactRef.from_dict(value)


def _ref_dict(value: ArtifactRef | Mapping[str, object]) -> dict[str, object]:
    if isinstance(value, ArtifactRef):
        return value.to_dict()
    return ArtifactRef.from_dict(value).to_dict()


def compute_evidence_provenance_sha256(value: Mapping[str, object]) -> str:
    missing = [field for field in _PROVENANCE_FIELDS if field not in value]
    if missing:
        raise ValueError(f"provenance fields are missing: {', '.join(missing)}")
    return sha256_json({field: value[field] for field in _PROVENANCE_FIELDS})


def compute_history_bundle_sha256(
    *,
    case_id: str,
    cutoff_at: datetime,
    record_refs: Sequence[ArtifactRef | Mapping[str, object]],
    rejected_future_refs: Sequence[ArtifactRef | Mapping[str, object]],
    transformation_refs: Sequence[ArtifactRef | Mapping[str, object]],
    source_licenses: Mapping[str, str],
) -> str:
    return sha256_json(
        {
            "case_id": case_id,
            "cutoff_at": cutoff_at,
            "record_refs": [_ref_dict(item) for item in record_refs],
            "rejected_future_refs": [_ref_dict(item) for item in rejected_future_refs],
            "transformation_refs": [_ref_dict(item) for item in transformation_refs],
            "source_licenses": dict(source_licenses),
        }
    )


@dataclass(frozen=True)
class EvidenceRecordV2:
    schema_id: str
    schema_version: str
    evidence_id: str
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    available_at: datetime
    known_by_cutoff: Literal["known", "plausibly_known", "unavailable"]
    source_type: Literal["primary", "secondary", "dataset"]
    source_title: str
    source_locator: str
    source_locator_sha256: str
    curator: str
    candidate_id: str
    candidate_record_sha256: str
    review_sha256: str
    candidate_ref: ArtifactRef
    review_ref: ArtifactRef
    license: str
    excerpt_kind: Literal["verbatim", "translated", "paraphrased"]
    content_ref: ArtifactRef
    fact_ids: tuple[str, ...]
    transformation_refs: tuple[ArtifactRef, ...]
    provenance_sha256: str

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceRecordV2:
        validate_document("evidence-record", value)
        validate_document_date(value["document_date"], value["date_precision"])
        if compute_evidence_provenance_sha256(value) != value["provenance_sha256"]:
            raise ValueError("evidence provenance hash mismatch")
        fact_ids = tuple(str(item) for item in value["fact_ids"])  # type: ignore[union-attr]
        _unique_non_empty(fact_ids, "fact_ids", required=True)
        transformations = tuple(
            validate_transformation_ref(
                _artifact_ref(item, "transformation_refs item"),
                label="evidence transformation reference",
            )
            for item in value["transformation_refs"]  # type: ignore[union-attr]
        )
        record = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            evidence_id=str(value["evidence_id"]),
            document_date=str(value["document_date"]),
            date_precision=str(value["date_precision"]),  # type: ignore[arg-type]
            available_at=parse_utc(str(value["available_at"])),
            known_by_cutoff=str(value["known_by_cutoff"]),  # type: ignore[arg-type]
            source_type=str(value["source_type"]),  # type: ignore[arg-type]
            source_title=str(value["source_title"]),
            source_locator=str(value["source_locator"]),
            source_locator_sha256=str(value["source_locator_sha256"]),
            curator=str(value["curator"]),
            candidate_id=str(value["candidate_id"]),
            candidate_record_sha256=str(value["candidate_record_sha256"]),
            review_sha256=str(value["review_sha256"]),
            candidate_ref=_artifact_ref(value["candidate_ref"], "candidate_ref"),
            review_ref=_artifact_ref(value["review_ref"], "review_ref"),
            license=str(value["license"]),
            excerpt_kind=str(value["excerpt_kind"]),  # type: ignore[arg-type]
            content_ref=_artifact_ref(value["content_ref"], "content_ref"),
            fact_ids=fact_ids,
            transformation_refs=transformations,
            provenance_sha256=str(value["provenance_sha256"]),
        )
        if record.license not in _LICENSES:
            raise ValueError("evidence license is unsupported")
        if canonical_source_url(record.source_locator) != record.source_locator:
            raise ValueError("evidence source_locator is not canonical")
        if source_locator_sha256(record.source_locator) != record.source_locator_sha256:
            raise ValueError("evidence source locator identity mismatch")
        if (
            record.candidate_ref.kind != "evidence_candidate"
            or record.candidate_ref.schema_id != "tracelane://schemas/evidence-candidate/v2"
            or record.candidate_ref.media_type != "application/json"
        ):
            raise ValueError("evidence candidate_ref metadata is invalid")
        if (
            record.review_ref.kind != "candidate_review"
            or record.review_ref.schema_id != "tracelane://schemas/candidate-review/v2"
            or record.review_ref.media_type != "application/json"
        ):
            raise ValueError("evidence review_ref metadata is invalid")
        return record

    def to_dict(self) -> dict[str, object]:
        for reference in self.transformation_refs:
            validate_transformation_ref(
                reference,
                label="evidence transformation reference",
            )
        value: dict[str, object] = {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "evidence_id": self.evidence_id,
            "document_date": self.document_date,
            "date_precision": self.date_precision,
            "available_at": self.available_at,
            "known_by_cutoff": self.known_by_cutoff,
            "source_type": self.source_type,
            "source_title": self.source_title,
            "source_locator": self.source_locator,
            "source_locator_sha256": self.source_locator_sha256,
            "curator": self.curator,
            "candidate_id": self.candidate_id,
            "candidate_record_sha256": self.candidate_record_sha256,
            "review_sha256": self.review_sha256,
            "candidate_ref": self.candidate_ref.to_dict(),
            "review_ref": self.review_ref.to_dict(),
            "license": self.license,
            "excerpt_kind": self.excerpt_kind,
            "content_ref": self.content_ref.to_dict(),
            "fact_ids": list(self.fact_ids),
            "transformation_refs": [item.to_dict() for item in self.transformation_refs],
            "provenance_sha256": self.provenance_sha256,
        }
        normalized = json.loads(canonical_json(value))
        validate_document_date(normalized["document_date"], normalized["date_precision"])
        if compute_evidence_provenance_sha256(normalized) != normalized["provenance_sha256"]:
            raise ValueError("evidence provenance hash mismatch")
        if self.license not in _LICENSES:
            raise ValueError("evidence license is unsupported")
        if canonical_source_url(self.source_locator) != self.source_locator:
            raise ValueError("evidence source_locator is not canonical")
        if source_locator_sha256(self.source_locator) != self.source_locator_sha256:
            raise ValueError("evidence source locator identity mismatch")
        if (
            self.candidate_ref.kind != "evidence_candidate"
            or self.candidate_ref.schema_id != "tracelane://schemas/evidence-candidate/v2"
            or self.candidate_ref.media_type != "application/json"
        ):
            raise ValueError("evidence candidate_ref metadata is invalid")
        if (
            self.review_ref.kind != "candidate_review"
            or self.review_ref.schema_id != "tracelane://schemas/candidate-review/v2"
            or self.review_ref.media_type != "application/json"
        ):
            raise ValueError("evidence review_ref metadata is invalid")
        validate_document("evidence-record", normalized)
        return normalized


@dataclass(frozen=True)
class HistoryCase:
    schema_id: str
    schema_version: str
    content_sha256: str
    case_id: str
    title: str
    decision_maker: str
    cutoff_at: datetime
    intervention: str
    projection_end: str
    minimum_alternatives: int
    minimum_scenario_branches: int
    required_domains: tuple[str, ...]
    evidence_manifest_ref: ArtifactRef
    rubric_refs: tuple[ArtifactRef, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> HistoryCase:
        validate_document("case", value)
        domains = tuple(
            str(item)
            for item in value["required_domains"]  # type: ignore[union-attr]
        )
        _unique_non_empty(domains, "required_domains", required=True)
        case = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            content_sha256=str(value["content_sha256"]),
            case_id=str(value["case_id"]),
            title=str(value["title"]),
            decision_maker=str(value["decision_maker"]),
            cutoff_at=parse_utc(str(value["cutoff_at"])),
            intervention=str(value["intervention"]),
            projection_end=str(value["projection_end"]),
            minimum_alternatives=int(value["minimum_alternatives"]),
            minimum_scenario_branches=int(value["minimum_scenario_branches"]),
            required_domains=domains,
            evidence_manifest_ref=_artifact_ref(
                value["evidence_manifest_ref"],
                "evidence_manifest_ref",
            ),
            rubric_refs=tuple(
                _artifact_ref(item, "rubric_refs item")
                for item in value["rubric_refs"]  # type: ignore[union-attr]
            ),
        )
        if content_digest(value) != case.content_sha256:
            raise ValueError("history case content hash mismatch")
        return case

    def to_dict(self) -> dict[str, object]:
        value = json.loads(canonical_json(self))
        validate_document("case", value)
        domains = tuple(str(item) for item in value["required_domains"])
        _unique_non_empty(domains, "required_domains", required=True)
        if content_digest(value) != self.content_sha256:
            raise ValueError("history case content hash mismatch")
        return value


@dataclass(frozen=True)
class EvidenceManifest:
    schema_id: str
    schema_version: str
    content_sha256: str
    case_id: str
    cutoff_at: datetime
    record_refs: tuple[ArtifactRef, ...]
    rejected_future_refs: tuple[ArtifactRef, ...]
    source_licenses: Mapping[str, str]
    transformation_refs: tuple[ArtifactRef, ...]
    bundle_sha256: str
    fixture_root: Path | None = field(default=None, repr=False, compare=False)
    source_path: Path | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for reference in self.transformation_refs:
            validate_transformation_ref(
                reference,
                label="evidence manifest transformation reference",
            )

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
        *,
        fixture_root: Path | None = None,
        source_path: Path | None = None,
    ) -> EvidenceManifest:
        validate_document("evidence-manifest", value)
        source_licenses_value = value["source_licenses"]
        if not isinstance(source_licenses_value, Mapping):
            raise ValueError("source_licenses must be an object")
        licenses = {str(key): str(item) for key, item in source_licenses_value.items()}
        record_refs = tuple(
            _artifact_ref(item, "record_refs item")
            for item in value["record_refs"]  # type: ignore[union-attr]
        )
        rejected_refs = tuple(
            _artifact_ref(item, "rejected_future_refs item")
            for item in value["rejected_future_refs"]  # type: ignore[union-attr]
        )
        transformations = tuple(
            validate_transformation_ref(
                _artifact_ref(item, "transformation_refs item"),
                label="evidence manifest transformation reference",
            )
            for item in value["transformation_refs"]  # type: ignore[union-attr]
        )
        manifest = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            content_sha256=str(value["content_sha256"]),
            case_id=str(value["case_id"]),
            cutoff_at=parse_utc(str(value["cutoff_at"])),
            record_refs=record_refs,
            rejected_future_refs=rejected_refs,
            source_licenses=MappingProxyType(licenses),
            transformation_refs=transformations,
            bundle_sha256=str(value["bundle_sha256"]),
            fixture_root=fixture_root,
            source_path=source_path,
        )
        if content_digest(value) != manifest.content_sha256:
            raise ValueError("evidence manifest content hash mismatch")
        expected_bundle = compute_history_bundle_sha256(
            case_id=manifest.case_id,
            cutoff_at=manifest.cutoff_at,
            record_refs=manifest.record_refs,
            rejected_future_refs=manifest.rejected_future_refs,
            transformation_refs=manifest.transformation_refs,
            source_licenses=manifest.source_licenses,
        )
        if expected_bundle != manifest.bundle_sha256:
            raise ValueError("evidence bundle hash mismatch")
        return manifest

    def to_dict(self) -> dict[str, object]:
        for reference in self.transformation_refs:
            validate_transformation_ref(
                reference,
                label="evidence manifest transformation reference",
            )
        value = {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "content_sha256": self.content_sha256,
            "case_id": self.case_id,
            "cutoff_at": self.cutoff_at,
            "record_refs": [item.to_dict() for item in self.record_refs],
            "rejected_future_refs": [item.to_dict() for item in self.rejected_future_refs],
            "source_licenses": dict(self.source_licenses),
            "transformation_refs": [item.to_dict() for item in self.transformation_refs],
            "bundle_sha256": self.bundle_sha256,
        }
        normalized = json.loads(canonical_json(value))
        validate_document("evidence-manifest", normalized)
        return normalized


@dataclass(frozen=True)
class FrozenHistoryBundle:
    case_id: str
    cutoff_at: datetime
    records: tuple[EvidenceRecordV2, ...]
    rejected_future_ids: tuple[str, ...]
    bundle_sha256: str


@dataclass(frozen=True)
class HistoryScenarioEntry:
    scenario_id: str
    case_id: str
    case_ref: ArtifactRef
    evidence_manifest_ref: ArtifactRef
    fault_ref: ArtifactRef | None
    fixture_root: Path = field(repr=False, compare=False)

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
        *,
        fixture_root: Path,
    ) -> HistoryScenarioEntry:
        fault_value = value.get("fault_ref")
        return cls(
            scenario_id=str(value["scenario_id"]),
            case_id=str(value["case_id"]),
            case_ref=_artifact_ref(value["case_ref"], "case_ref"),
            evidence_manifest_ref=_artifact_ref(
                value["evidence_manifest_ref"],
                "evidence_manifest_ref",
            ),
            fault_ref=(
                _artifact_ref(fault_value, "fault_ref") if fault_value is not None else None
            ),
            fixture_root=fixture_root,
        )

    @property
    def case_ref_path(self) -> Path:
        from tracelane.history.loader import resolve_fixture_ref

        return resolve_fixture_ref(
            self.fixture_root,
            self.case_ref,
            expected_kind="history_case",
            expected_schema_id="tracelane://schemas/case/v2",
        )

    @property
    def evidence_manifest_path(self) -> Path:
        from tracelane.history.loader import resolve_fixture_ref

        return resolve_fixture_ref(
            self.fixture_root,
            self.evidence_manifest_ref,
            expected_kind="evidence_manifest",
            expected_schema_id="tracelane://schemas/evidence-manifest/v2",
        )

    @property
    def fault_ref_path(self) -> Path | None:
        from tracelane.history.loader import resolve_fixture_ref

        if self.fault_ref is None:
            return None
        return resolve_fixture_ref(
            self.fixture_root,
            self.fault_ref,
            expected_kind="fault_fixture",
            expected_schema_id="tracelane://schemas/fault-fixture/v2",
        )
