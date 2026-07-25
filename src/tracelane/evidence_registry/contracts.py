from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from tracelane.acquisition.contracts import compute_candidate_id
from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.security import classify_and_redact
from tracelane.v2.contracts import (
    ArtifactRef,
    content_digest,
    make_object_id,
    validate_transformation_ref,
)
from tracelane.v2.schema import validate_document, validate_document_date
from tracelane.v2.source import canonical_source_url

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_SOURCE_SPEC_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_CANDIDATE_ID = re.compile(r"^candidate_[0-9a-f]{24}$")
_TRANSFORMATION_ID = re.compile(r"^transformation_[0-9a-f]{24}$")
_SESSION_ID = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_SOURCE_CANDIDATE_URI = re.compile(
    r"^tracelane://artifacts/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$"
)
_BLOB_URI = re.compile(r"^tracelane://evidence/blobs/sha256/[0-9a-f]{64}$")
_LOCAL_STATE_COMPONENT = re.compile(r"(?i)(?<!\w)\.local(?=$|[\\/])")
_SOURCE_TYPES = frozenset({"primary", "secondary", "dataset"})
_ROLES = frozenset({"evidence", "future-control"})
_AUTHORS = frozenset({"repository_authored", "third_party"})
_RETENTION = frozenset({"paraphrase_only", "public_domain_full_text", "licensed_full_text"})
_TRANSFORMATION_TYPES = frozenset(
    {"manual_excerpt", "repository_paraphrase", "translation", "ocr", "normalization"}
)


def candidate_record_digest(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("record digest input must be a mapping")
    payload = {str(key): item for key, item in value.items() if key != "record_sha256"}
    return sha256_json(payload)


def _validate_persisted_text(value: str, label: str) -> str:
    if classify_and_redact(value).redaction_applied or _LOCAL_STATE_COMPONENT.search(value):
        raise ValueError(f"{label} contains sensitive text")
    return value


def _contains_local_state_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_local_state_reference(str(key)) or _contains_local_state_reference(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_local_state_reference(item) for item in value)
    return isinstance(value, str) and _LOCAL_STATE_COMPONENT.search(value) is not None


def _validate_persisted_json(value: object, label: str) -> None:
    if classify_and_redact(value).redaction_applied or _contains_local_state_reference(value):
        raise ValueError(f"{label} contains sensitive text")


def _non_empty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return _validate_persisted_text(value.strip(), label)


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _format_utc(value: object, label: str) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sorted_unique_strings(value: object, label: str, *, required: bool = True) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be a sequence")
    normalized = tuple(value)
    if required and not normalized:
        raise ValueError(f"{label} must not be empty")
    for item in normalized:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} must contain non-empty strings")
        _validate_persisted_text(item, label)
    if tuple(sorted(normalized)) != normalized or len(set(normalized)) != len(normalized):
        raise ValueError(f"{label} must be sorted and unique")
    return normalized  # type: ignore[return-value]


def _ordered_unique_refs(value: object, label: str) -> tuple[ArtifactRef, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be a sequence")
    references = tuple(value)
    if not all(isinstance(item, ArtifactRef) for item in references):
        raise ValueError(f"{label} must contain ArtifactRef values")
    keys = tuple(canonical_json(item.to_dict()) for item in references)
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} must be unique")
    return references  # type: ignore[return-value]


def _blob_ref(reference: ArtifactRef, label: str, *, require_blob_uri: bool = False) -> ArtifactRef:
    if reference.kind != "evidence_blob" or reference.schema_id is not None:
        raise ValueError(f"{label} must be an evidence blob reference")
    if require_blob_uri:
        if _BLOB_URI.fullmatch(reference.uri) is None:
            raise ValueError(f"{label} URI is invalid")
        if reference.uri.rsplit("/", 1)[-1] != reference.sha256:
            raise ValueError(f"{label} URI does not match its digest")
    return reference


def _require_project_id(value: object) -> str:
    if not isinstance(value, str) or _PROJECT_ID.fullmatch(value) is None:
        raise ValueError("project_id is invalid")
    return value


def _validate_retention(authorship: str, policy: str) -> None:
    if authorship not in _AUTHORS:
        raise ValueError("content_authorship is invalid")
    if policy not in _RETENTION:
        raise ValueError("retention policy is invalid")
    if authorship == "repository_authored" and policy != "paraphrase_only":
        raise ValueError("retention policy is invalid for repository-authored content")
    if authorship == "third_party" and policy not in {
        "public_domain_full_text",
        "licensed_full_text",
    }:
        raise ValueError("retention policy is invalid for third-party content")


@dataclass(frozen=True)
class EvidenceProject:
    schema_id: str
    schema_version: str
    record_sha256: str
    project_id: str
    title: str
    research_question: str
    historical_cutoff_at: datetime
    intervention: str
    required_domains: tuple[str, ...]
    future_control_policy: Literal["exclude_from_clean"]
    admitted_source_types: tuple[Literal["primary", "secondary", "dataset"], ...]
    status: Literal["active", "paused", "completed", "archived"]

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        title: str,
        research_question: str,
        historical_cutoff_at: datetime,
        intervention: str,
        required_domains: Sequence[str],
        admitted_source_types: Sequence[Literal["primary", "secondary", "dataset"]],
        status: Literal["active", "paused", "completed", "archived"],
    ) -> EvidenceProject:
        value: dict[str, object] = {
            "schema_id": "tracelane://schemas/evidence-project/v1",
            "schema_version": "1.0.0",
            "record_sha256": "",
            "project_id": project_id,
            "title": title,
            "research_question": research_question,
            "historical_cutoff_at": _format_utc(historical_cutoff_at, "historical_cutoff_at"),
            "intervention": intervention,
            "required_domains": list(required_domains),
            "future_control_policy": "exclude_from_clean",
            "admitted_source_types": list(admitted_source_types),
            "status": status,
        }
        value["record_sha256"] = candidate_record_digest(value)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceProject:
        validate_document("evidence-project", value)
        project = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            record_sha256=str(value["record_sha256"]),
            project_id=str(value["project_id"]),
            title=str(value["title"]),
            research_question=str(value["research_question"]),
            historical_cutoff_at=parse_utc(str(value["historical_cutoff_at"])),
            intervention=str(value["intervention"]),
            required_domains=_sorted_unique_strings(value["required_domains"], "required_domains"),
            future_control_policy=str(value["future_control_policy"]),  # type: ignore[arg-type]
            admitted_source_types=_sorted_unique_strings(
                value["admitted_source_types"], "admitted_source_types"
            ),  # type: ignore[arg-type]
            status=str(value["status"]),  # type: ignore[arg-type]
        )
        _require_project_id(project.project_id)
        _non_empty(project.title, "title")
        _non_empty(project.research_question, "research_question")
        _non_empty(project.intervention, "intervention")
        if set(project.admitted_source_types) - _SOURCE_TYPES:
            raise ValueError("admitted_source_types is invalid")
        if candidate_record_digest(project._raw_dict()) != project.record_sha256:
            raise ValueError("project record digest is stale")
        return project

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_sha256": self.record_sha256,
            "project_id": self.project_id,
            "title": self.title,
            "research_question": self.research_question,
            "historical_cutoff_at": _format_utc(self.historical_cutoff_at, "historical_cutoff_at"),
            "intervention": self.intervention,
            "required_domains": list(self.required_domains),
            "future_control_policy": self.future_control_policy,
            "admitted_source_types": list(self.admitted_source_types),
            "status": self.status,
        }

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return json.loads(canonical_json(self._raw_dict()))


@dataclass(frozen=True)
class ProjectEvidenceCandidate:
    schema_id: str
    schema_version: str
    record_sha256: str
    project_id: str
    candidate_id: str
    source_spec_id: str
    query: str
    title: str
    source_url: str
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    retrieved_at: datetime
    curator: str
    source_type: Literal["primary", "secondary", "dataset"]
    role: Literal["evidence", "future-control"]
    domains: tuple[str, ...]
    fact_ids: tuple[str, ...]
    content_ref: ArtifactRef
    transformation_refs: tuple[ArtifactRef, ...]
    content_sha256: str
    content_authorship: Literal["repository_authored", "third_party"]
    retention_policy: Literal["paraphrase_only", "public_domain_full_text", "licensed_full_text"]
    license_basis: str
    acquisition_session_id: str
    source_candidate_uri: str
    source_candidate_id: str
    source_candidate_record_sha256: str
    source_candidate_content_sha256: str
    trust_level: Literal["untrusted_external"] = "untrusted_external"

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        candidate_id: str,
        source_spec_id: str,
        query: str,
        title: str,
        source_url: str,
        document_date: str,
        date_precision: Literal["day", "month", "year", "estimated"],
        retrieved_at: datetime,
        curator: str,
        source_type: Literal["primary", "secondary", "dataset"],
        role: Literal["evidence", "future-control"],
        domains: Sequence[str],
        fact_ids: Sequence[str],
        content_ref: ArtifactRef,
        transformation_refs: Sequence[ArtifactRef],
        content_authorship: Literal["repository_authored", "third_party"],
        retention_policy: Literal[
            "paraphrase_only", "public_domain_full_text", "licensed_full_text"
        ],
        license_basis: str,
        acquisition_session_id: str,
        source_candidate_uri: str,
        source_candidate_id: str,
        source_candidate_record_sha256: str,
        source_candidate_content_sha256: str,
    ) -> ProjectEvidenceCandidate:
        value: dict[str, object] = {
            "schema_id": "tracelane://schemas/project-evidence-candidate/v1",
            "schema_version": "1.0.0",
            "record_sha256": "",
            "project_id": project_id,
            "candidate_id": candidate_id,
            "source_spec_id": source_spec_id,
            "query": query,
            "title": title,
            "source_url": source_url,
            "document_date": document_date,
            "date_precision": date_precision,
            "retrieved_at": _format_utc(retrieved_at, "retrieved_at"),
            "curator": curator,
            "source_type": source_type,
            "role": role,
            "domains": list(domains),
            "fact_ids": list(fact_ids),
            "content_ref": content_ref.to_dict(),
            "transformation_refs": [item.to_dict() for item in transformation_refs],
            "content_sha256": content_ref.sha256,
            "content_authorship": content_authorship,
            "retention_policy": retention_policy,
            "license_basis": license_basis,
            "acquisition_session_id": acquisition_session_id,
            "source_candidate_uri": source_candidate_uri,
            "source_candidate_id": source_candidate_id,
            "source_candidate_record_sha256": source_candidate_record_sha256,
            "source_candidate_content_sha256": source_candidate_content_sha256,
            "trust_level": "untrusted_external",
        }
        value["record_sha256"] = candidate_record_digest(value)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ProjectEvidenceCandidate:
        validate_document("project-evidence-candidate", value)
        validate_document_date(value["document_date"], value["date_precision"])
        content_value = value["content_ref"]
        if not isinstance(content_value, Mapping):
            raise ValueError("content_ref must be an object")
        transformations_value = value["transformation_refs"]
        if isinstance(transformations_value, (str, bytes)) or not isinstance(
            transformations_value, Sequence
        ):
            raise ValueError("transformation_refs must be a sequence")
        transformations = tuple(
            validate_transformation_ref(
                ArtifactRef.from_dict(item), label="candidate transformation reference"
            )
            for item in transformations_value
        )
        candidate = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            record_sha256=str(value["record_sha256"]),
            project_id=str(value["project_id"]),
            candidate_id=str(value["candidate_id"]),
            source_spec_id=str(value["source_spec_id"]),
            query=str(value["query"]),
            title=str(value["title"]),
            source_url=str(value["source_url"]),
            document_date=str(value["document_date"]),
            date_precision=str(value["date_precision"]),  # type: ignore[arg-type]
            retrieved_at=parse_utc(str(value["retrieved_at"])),
            curator=str(value["curator"]),
            source_type=str(value["source_type"]),  # type: ignore[arg-type]
            role=str(value["role"]),  # type: ignore[arg-type]
            domains=_sorted_unique_strings(value["domains"], "domains"),
            fact_ids=_sorted_unique_strings(value["fact_ids"], "fact_ids"),
            content_ref=ArtifactRef.from_dict(content_value),
            transformation_refs=transformations,
            content_sha256=str(value["content_sha256"]),
            content_authorship=str(value["content_authorship"]),  # type: ignore[arg-type]
            retention_policy=str(value["retention_policy"]),  # type: ignore[arg-type]
            license_basis=str(value["license_basis"]),
            acquisition_session_id=str(value["acquisition_session_id"]),
            source_candidate_uri=str(value["source_candidate_uri"]),
            source_candidate_id=str(value["source_candidate_id"]),
            source_candidate_record_sha256=str(value["source_candidate_record_sha256"]),
            source_candidate_content_sha256=str(value["source_candidate_content_sha256"]),
        )
        _require_project_id(candidate.project_id)
        if _CANDIDATE_ID.fullmatch(candidate.candidate_id) is None:
            raise ValueError("candidate_id is invalid")
        if _SOURCE_SPEC_ID.fullmatch(candidate.source_spec_id) is None:
            raise ValueError("source_spec_id is invalid")
        for item, label in (
            (candidate.query, "query"),
            (candidate.title, "title"),
            (candidate.curator, "curator"),
            (candidate.license_basis, "license_basis"),
        ):
            _non_empty(item, label)
        if canonical_source_url(candidate.source_url) != candidate.source_url:
            raise ValueError("source_url is not canonical")
        if candidate.source_type not in _SOURCE_TYPES:
            raise ValueError("source_type is invalid")
        if candidate.role not in _ROLES:
            raise ValueError("role is invalid")
        _blob_ref(candidate.content_ref, "content_ref", require_blob_uri=True)
        _ordered_unique_refs(candidate.transformation_refs, "transformation_refs")
        if candidate.content_sha256 != candidate.content_ref.sha256:
            raise ValueError("content_sha256 does not match content_ref")
        _validate_retention(candidate.content_authorship, candidate.retention_policy)
        if _SESSION_ID.fullmatch(candidate.acquisition_session_id) is None:
            raise ValueError("acquisition_session_id is invalid")
        if _SOURCE_CANDIDATE_URI.fullmatch(candidate.source_candidate_uri) is None:
            raise ValueError("source_candidate_uri is invalid")
        if candidate.source_candidate_id != candidate.candidate_id:
            raise ValueError("candidate lineage ID is invalid")
        _digest(candidate.source_candidate_record_sha256, "source_candidate_record_sha256")
        if candidate.source_candidate_content_sha256 != candidate.content_sha256:
            raise ValueError("candidate lineage content digest is invalid")
        expected_id = compute_candidate_id(
            query=candidate.query,
            title=candidate.title,
            source_url=candidate.source_url,
            document_date=candidate.document_date,
            date_precision=candidate.date_precision,
            content_sha256=candidate.content_sha256,
        )
        if candidate.candidate_id != expected_id:
            raise ValueError("candidate_id does not match candidate identity")
        if candidate_record_digest(candidate._raw_dict()) != candidate.record_sha256:
            raise ValueError("candidate record digest is stale")
        return candidate

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_sha256": self.record_sha256,
            "project_id": self.project_id,
            "candidate_id": self.candidate_id,
            "source_spec_id": self.source_spec_id,
            "query": self.query,
            "title": self.title,
            "source_url": self.source_url,
            "document_date": self.document_date,
            "date_precision": self.date_precision,
            "retrieved_at": _format_utc(self.retrieved_at, "retrieved_at"),
            "curator": self.curator,
            "source_type": self.source_type,
            "role": self.role,
            "domains": list(self.domains),
            "fact_ids": list(self.fact_ids),
            "content_ref": self.content_ref.to_dict(),
            "transformation_refs": [item.to_dict() for item in self.transformation_refs],
            "content_sha256": self.content_sha256,
            "content_authorship": self.content_authorship,
            "retention_policy": self.retention_policy,
            "license_basis": self.license_basis,
            "acquisition_session_id": self.acquisition_session_id,
            "source_candidate_uri": self.source_candidate_uri,
            "source_candidate_id": self.source_candidate_id,
            "source_candidate_record_sha256": self.source_candidate_record_sha256,
            "source_candidate_content_sha256": self.source_candidate_content_sha256,
            "trust_level": self.trust_level,
        }

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return json.loads(canonical_json(self._raw_dict()))


@dataclass(frozen=True)
class EvidenceTransformation:
    schema_id: str
    schema_version: str
    record_sha256: str
    transformation_id: str
    project_id: str
    candidate_id: str
    transformation_type: Literal[
        "manual_excerpt", "repository_paraphrase", "translation", "ocr", "normalization"
    ]
    input_ref: ArtifactRef
    output_ref: ArtifactRef
    actor: str
    method: str
    parameters: Mapping[str, object]
    created_at: datetime
    license_implications: str

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        candidate_id: str,
        transformation_type: Literal[
            "manual_excerpt", "repository_paraphrase", "translation", "ocr", "normalization"
        ],
        input_ref: ArtifactRef,
        output_ref: ArtifactRef,
        actor: str,
        method: str,
        parameters: Mapping[str, object],
        created_at: datetime,
        license_implications: str,
    ) -> EvidenceTransformation:
        identity: dict[str, object] = {
            "project_id": project_id,
            "candidate_id": candidate_id,
            "transformation_type": transformation_type,
            "input_ref": input_ref.to_dict(),
            "output_ref": output_ref.to_dict(),
            "actor": actor,
            "method": method,
            "parameters": parameters,
            "created_at": _format_utc(created_at, "created_at"),
            "license_implications": license_implications,
        }
        value: dict[str, object] = {
            "schema_id": "tracelane://schemas/evidence-transformation/v1",
            "schema_version": "1.0.0",
            "record_sha256": "",
            "transformation_id": make_object_id("transformation", identity),
            **identity,
        }
        value["record_sha256"] = candidate_record_digest(value)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceTransformation:
        validate_document("evidence-transformation", value)
        input_value = value["input_ref"]
        output_value = value["output_ref"]
        parameters = value["parameters"]
        if not isinstance(input_value, Mapping) or not isinstance(output_value, Mapping):
            raise ValueError("transformation references must be objects")
        if not isinstance(parameters, Mapping):
            raise ValueError("parameters must be an object")
        normalized_parameters = json.loads(canonical_json(parameters))
        _validate_persisted_json(normalized_parameters, "parameters")
        transformation = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            record_sha256=str(value["record_sha256"]),
            transformation_id=str(value["transformation_id"]),
            project_id=str(value["project_id"]),
            candidate_id=str(value["candidate_id"]),
            transformation_type=str(value["transformation_type"]),  # type: ignore[arg-type]
            input_ref=ArtifactRef.from_dict(input_value),
            output_ref=ArtifactRef.from_dict(output_value),
            actor=str(value["actor"]),
            method=str(value["method"]),
            parameters=normalized_parameters,
            created_at=parse_utc(str(value["created_at"])),
            license_implications=str(value["license_implications"]),
        )
        if _TRANSFORMATION_ID.fullmatch(transformation.transformation_id) is None:
            raise ValueError("transformation_id is invalid")
        _require_project_id(transformation.project_id)
        if _CANDIDATE_ID.fullmatch(transformation.candidate_id) is None:
            raise ValueError("candidate_id is invalid")
        if transformation.transformation_type not in _TRANSFORMATION_TYPES:
            raise ValueError("transformation_type is invalid")
        _blob_ref(transformation.input_ref, "input_ref")
        _blob_ref(transformation.output_ref, "output_ref")
        if transformation.input_ref.sha256 == transformation.output_ref.sha256:
            raise ValueError(
                "transformation input and output must have different content identities"
            )
        for item, label in (
            (transformation.actor, "actor"),
            (transformation.method, "method"),
            (transformation.license_implications, "license_implications"),
        ):
            _non_empty(item, label)
        if candidate_record_digest(transformation._raw_dict()) != transformation.record_sha256:
            raise ValueError("transformation record digest is stale")
        return transformation

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_sha256": self.record_sha256,
            "transformation_id": self.transformation_id,
            "project_id": self.project_id,
            "candidate_id": self.candidate_id,
            "transformation_type": self.transformation_type,
            "input_ref": self.input_ref.to_dict(),
            "output_ref": self.output_ref.to_dict(),
            "actor": self.actor,
            "method": self.method,
            "parameters": dict(self.parameters),
            "created_at": _format_utc(self.created_at, "created_at"),
            "license_implications": self.license_implications,
        }

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return json.loads(canonical_json(self._raw_dict()))


@dataclass(frozen=True)
class EvidenceImportRow:
    source_spec_id: str
    candidate_id: str
    candidate_record_sha256: str
    candidate_content_sha256: str
    source_type: Literal["primary", "secondary", "dataset"]
    license_basis: str
    content_authorship: Literal["repository_authored", "third_party"]
    retention_policy: Literal["paraphrase_only", "public_domain_full_text", "licensed_full_text"]
    domains: tuple[str, ...]
    fact_ids: tuple[str, ...]
    role: Literal["evidence", "future-control"]

    @classmethod
    def from_candidate(cls, candidate: ProjectEvidenceCandidate) -> EvidenceImportRow:
        candidate.to_dict()
        return cls(
            source_spec_id=candidate.source_spec_id,
            candidate_id=candidate.candidate_id,
            candidate_record_sha256=candidate.record_sha256,
            candidate_content_sha256=candidate.content_sha256,
            source_type=candidate.source_type,
            license_basis=candidate.license_basis,
            content_authorship=candidate.content_authorship,
            retention_policy=candidate.retention_policy,
            domains=candidate.domains,
            fact_ids=candidate.fact_ids,
            role=candidate.role,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceImportRow:
        row = cls(
            source_spec_id=str(value["source_spec_id"]),
            candidate_id=str(value["candidate_id"]),
            candidate_record_sha256=str(value["candidate_record_sha256"]),
            candidate_content_sha256=str(value["candidate_content_sha256"]),
            source_type=str(value["source_type"]),  # type: ignore[arg-type]
            license_basis=str(value["license_basis"]),
            content_authorship=str(value["content_authorship"]),  # type: ignore[arg-type]
            retention_policy=str(value["retention_policy"]),  # type: ignore[arg-type]
            domains=_sorted_unique_strings(value["domains"], "domains"),
            fact_ids=_sorted_unique_strings(value["fact_ids"], "fact_ids"),
            role=str(value["role"]),  # type: ignore[arg-type]
        )
        if _SOURCE_SPEC_ID.fullmatch(row.source_spec_id) is None:
            raise ValueError("source_spec_id is invalid")
        if _CANDIDATE_ID.fullmatch(row.candidate_id) is None:
            raise ValueError("candidate_id is invalid")
        _digest(row.candidate_record_sha256, "candidate_record_sha256")
        _digest(row.candidate_content_sha256, "candidate_content_sha256")
        if row.source_type not in _SOURCE_TYPES:
            raise ValueError("source_type is invalid")
        _non_empty(row.license_basis, "license_basis")
        _validate_retention(row.content_authorship, row.retention_policy)
        if row.role not in _ROLES:
            raise ValueError("role is invalid")
        return row

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return self._raw_dict()

    def _raw_dict(self) -> dict[str, object]:
        return {
            "source_spec_id": self.source_spec_id,
            "candidate_id": self.candidate_id,
            "candidate_record_sha256": self.candidate_record_sha256,
            "candidate_content_sha256": self.candidate_content_sha256,
            "source_type": self.source_type,
            "license_basis": self.license_basis,
            "content_authorship": self.content_authorship,
            "retention_policy": self.retention_policy,
            "domains": list(self.domains),
            "fact_ids": list(self.fact_ids),
            "role": self.role,
        }


@dataclass(frozen=True)
class EvidenceImportMetadata:
    schema_id: str
    schema_version: str
    content_sha256: str
    project_id: str
    session_id: str
    manifest_sha256: str
    candidates: tuple[EvidenceImportRow, ...]

    @classmethod
    def create(
        cls,
        *,
        project_id: str,
        session_id: str,
        manifest_sha256: str,
        candidates: Sequence[EvidenceImportRow],
    ) -> EvidenceImportMetadata:
        value: dict[str, object] = {
            "schema_id": "tracelane://schemas/evidence-import-metadata/v1",
            "schema_version": "1.0.0",
            "content_sha256": "",
            "project_id": project_id,
            "session_id": session_id,
            "manifest_sha256": manifest_sha256,
            "candidates": [item.to_dict() for item in candidates],
        }
        value["content_sha256"] = content_digest(value)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceImportMetadata:
        validate_document("evidence-import-metadata", value)
        values = value["candidates"]
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise ValueError("candidates must be a sequence")
        rows = tuple(EvidenceImportRow.from_dict(item) for item in values)
        metadata = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            content_sha256=str(value["content_sha256"]),
            project_id=str(value["project_id"]),
            session_id=str(value["session_id"]),
            manifest_sha256=str(value["manifest_sha256"]),
            candidates=rows,
        )
        _require_project_id(metadata.project_id)
        if _SESSION_ID.fullmatch(metadata.session_id) is None:
            raise ValueError("session_id is invalid")
        _digest(metadata.manifest_sha256, "manifest_sha256")
        candidate_ids = tuple(item.candidate_id for item in metadata.candidates)
        if tuple(sorted(candidate_ids)) != candidate_ids or len(set(candidate_ids)) != len(
            candidate_ids
        ):
            raise ValueError("candidates must be sorted and unique by candidate_id")
        if content_digest(metadata._raw_dict()) != metadata.content_sha256:
            raise ValueError("metadata content digest is stale")
        return metadata

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "content_sha256": self.content_sha256,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "manifest_sha256": self.manifest_sha256,
            "candidates": [item.to_dict() for item in self.candidates],
        }

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return json.loads(canonical_json(self._raw_dict()))
