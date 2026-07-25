from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.v2.contracts import (
    ArtifactRef,
    content_digest,
    validate_transformation_ref,
)
from tracelane.v2.schema import validate_document, validate_document_date
from tracelane.v2.source import canonical_source_url, source_locator_sha256


def _record_digest(value: Mapping[str, object]) -> str:
    return sha256_json({str(key): item for key, item in value.items() if key != "record_sha256"})


def compute_candidate_id(
    *,
    query: str,
    title: str,
    source_url: str,
    document_date: str,
    date_precision: str,
    content_sha256: str,
) -> str:
    source_url = canonical_source_url(source_url)
    identity = {
        "query": query,
        "title": title,
        "source_url": source_url,
        "document_date": document_date,
        "date_precision": date_precision,
        "content_sha256": content_sha256,
    }
    return f"candidate_{sha256_json(identity)[:24]}"


@dataclass(frozen=True)
class EvidenceCandidate:
    schema_id: str
    schema_version: str
    record_sha256: str
    candidate_id: str
    query: str
    title: str
    source_url: str
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    retrieved_at: datetime
    curator: str
    transformation_refs: tuple[ArtifactRef, ...]
    content_ref: ArtifactRef
    content_sha256: str
    trust_level: Literal["untrusted_external"] = "untrusted_external"

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        query: str,
        title: str,
        source_url: str,
        document_date: str,
        date_precision: Literal["day", "month", "year", "estimated"],
        retrieved_at: datetime,
        curator: str,
        transformation_refs: Sequence[ArtifactRef],
        content_ref: ArtifactRef,
    ) -> EvidenceCandidate:
        candidate = cls(
            schema_id="tracelane://schemas/evidence-candidate/v2",
            schema_version="2.0.0",
            record_sha256="",
            candidate_id=candidate_id,
            query=query,
            title=title,
            source_url=source_url,
            document_date=document_date,
            date_precision=date_precision,
            retrieved_at=retrieved_at,
            curator=curator,
            transformation_refs=tuple(transformation_refs),
            content_ref=content_ref,
            content_sha256=content_ref.sha256,
        )
        value = candidate._raw_dict()
        value["record_sha256"] = _record_digest(value)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceCandidate:
        validate_document("evidence-candidate", value)
        validate_document_date(value["document_date"], value["date_precision"])
        content_ref = value["content_ref"]
        if not isinstance(content_ref, Mapping):
            raise ValueError("candidate content_ref must be an object")
        transformations = tuple(
            validate_transformation_ref(
                ArtifactRef.from_dict(item),
                label="candidate transformation reference",
            )
            for item in value["transformation_refs"]  # type: ignore[union-attr]
        )
        candidate = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            record_sha256=str(value["record_sha256"]),
            candidate_id=str(value["candidate_id"]),
            query=str(value["query"]),
            title=str(value["title"]),
            source_url=str(value["source_url"]),
            document_date=str(value["document_date"]),
            date_precision=str(value["date_precision"]),  # type: ignore[arg-type]
            retrieved_at=parse_utc(str(value["retrieved_at"])),
            curator=str(value["curator"]),
            transformation_refs=transformations,
            content_ref=ArtifactRef.from_dict(content_ref),
            content_sha256=str(value["content_sha256"]),
            trust_level="untrusted_external",
        )
        if candidate.content_ref.sha256 != str(value["content_blob_sha256"]):
            raise ValueError("candidate content hash does not match its blob")
        if candidate.content_ref.sha256 != candidate.content_sha256:
            raise ValueError("candidate content_sha256 does not match its blob")
        if canonical_source_url(candidate.source_url) != candidate.source_url:
            raise ValueError("candidate source_url is not canonical")
        expected_id = compute_candidate_id(
            query=candidate.query,
            title=candidate.title,
            source_url=candidate.source_url,
            document_date=candidate.document_date,
            date_precision=candidate.date_precision,
            content_sha256=candidate.content_sha256,
        )
        if expected_id != candidate.candidate_id:
            raise ValueError("candidate_id does not match candidate identity")
        if _record_digest(candidate._raw_dict()) != candidate.record_sha256:
            raise ValueError("candidate record hash mismatch")
        return candidate

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_sha256": self.record_sha256,
            "content_sha256": self.content_sha256,
            "candidate_id": self.candidate_id,
            "query": self.query,
            "title": self.title,
            "source_url": self.source_url,
            "document_date": self.document_date,
            "date_precision": self.date_precision,
            "retrieved_at": self.retrieved_at,
            "curator": self.curator,
            "transformation_refs": [item.to_dict() for item in self.transformation_refs],
            "content_ref": self.content_ref.to_dict(),
            "content_blob_sha256": self.content_ref.sha256,
            "trust_level": self.trust_level,
        }

    def to_dict(self) -> dict[str, object]:
        for reference in self.transformation_refs:
            validate_transformation_ref(
                reference,
                label="candidate transformation reference",
            )
        value = json.loads(canonical_json(self._raw_dict()))
        validate_document_date(value["document_date"], value["date_precision"])
        validate_document("evidence-candidate", value)
        return value


@dataclass(frozen=True)
class AcquisitionCandidateClosure:
    candidate_ref: ArtifactRef
    candidate: EvidenceCandidate
    candidate_bytes: bytes
    content_bytes: bytes
    transformations: tuple[tuple[ArtifactRef, bytes], ...]


@dataclass(frozen=True)
class CandidateReview:
    content_sha256: str
    candidate_id: str
    candidate_record_sha256: str
    candidate_content_sha256: str
    source_locator_sha256: str
    decision: Literal["approved", "rejected"]
    reviewer: str
    reviewed_at: datetime
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    available_at: datetime
    source_type: Literal["primary", "secondary", "dataset"]
    license: str
    reason: str

    @classmethod
    def create(
        cls,
        candidate: EvidenceCandidate,
        *,
        decision: Literal["approved", "rejected"],
        reviewer: str,
        reviewed_at: datetime,
        available_at: datetime,
        source_type: Literal["primary", "secondary", "dataset"],
        license: str,
        reason: str,
    ) -> CandidateReview:
        review = cls(
            content_sha256="",
            candidate_id=candidate.candidate_id,
            candidate_record_sha256=candidate.record_sha256,
            candidate_content_sha256=candidate.content_sha256,
            source_locator_sha256=source_locator_sha256(candidate.source_url),
            decision=decision,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            document_date=candidate.document_date,
            date_precision=candidate.date_precision,
            available_at=available_at,
            source_type=source_type,
            license=license,
            reason=reason,
        )
        value = review._raw_dict()
        value["content_sha256"] = content_digest(value)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> CandidateReview:
        validate_document("candidate-review", value)
        validate_document_date(value["document_date"], value["date_precision"])
        if content_digest(value) != str(value["content_sha256"]):
            raise ValueError("review content hash mismatch")
        return cls(
            content_sha256=str(value["content_sha256"]),
            candidate_id=str(value["candidate_id"]),
            candidate_record_sha256=str(value["candidate_record_sha256"]),
            candidate_content_sha256=str(value["candidate_content_sha256"]),
            source_locator_sha256=str(value["source_locator_sha256"]),
            decision=str(value["decision"]),  # type: ignore[arg-type]
            reviewer=str(value["reviewer"]),
            reviewed_at=parse_utc(str(value["reviewed_at"])),
            document_date=str(value["document_date"]),
            date_precision=str(value["date_precision"]),  # type: ignore[arg-type]
            available_at=parse_utc(str(value["available_at"])),
            source_type=str(value["source_type"]),  # type: ignore[arg-type]
            license=str(value["license"]),
            reason=str(value["reason"]),
        )

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": "tracelane://schemas/candidate-review/v2",
            "schema_version": "2.0.0",
            "content_sha256": self.content_sha256,
            "candidate_id": self.candidate_id,
            "candidate_record_sha256": self.candidate_record_sha256,
            "candidate_content_sha256": self.candidate_content_sha256,
            "source_locator_sha256": self.source_locator_sha256,
            "decision": self.decision,
            "reviewer": self.reviewer,
            "reviewed_at": self.reviewed_at,
            "document_date": self.document_date,
            "date_precision": self.date_precision,
            "available_at": self.available_at,
            "source_type": self.source_type,
            "license": self.license,
            "reason": self.reason,
        }

    def to_dict(self) -> dict[str, object]:
        value = json.loads(canonical_json(self._raw_dict()))
        validate_document_date(value["document_date"], value["date_precision"])
        validate_document("candidate-review", value)
        if content_digest(value) != self.content_sha256:
            raise ValueError("review content hash mismatch")
        return value
