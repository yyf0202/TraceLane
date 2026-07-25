from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

from tracelane.contracts import canonical_json
from tracelane.evidence_registry.contracts import (
    EvidenceProject,
    EvidenceTransformation,
    ProjectEvidenceCandidate,
    _digest,
    _non_empty,
    _require_project_id,
    _sorted_unique_strings,
    _validate_persisted_json,
    candidate_record_digest,
)
from tracelane.evidence_registry.reviews import (
    EffectiveStatus,
    EvidenceReview,
    current_review,
    validate_review_chain,
)
from tracelane.evidence_registry.storage import (
    EvidenceBlobStore,
    EvidenceRoot,
    JsonPublicationReceipt,
    _is_link_or_reparse,
    _secure_read,
    evidence_root_mutation_lock,
    read_json_object,
    rollback_json_publication,
    write_json_create_or_match,
    write_json_create_or_match_receipt,
)
from tracelane.v2.contracts import ArtifactRef
from tracelane.v2.schema import validate_document, validate_document_date

_PROJECT_INDEX_SCHEMA = "tracelane://schemas/evidence-project-index/v1"
_REGISTRY_SCHEMA = "tracelane://schemas/evidence-registry/v1"
_PROJECT_SCHEMA = "tracelane://schemas/evidence-project/v1"
_CANDIDATE_SCHEMA = "tracelane://schemas/project-evidence-candidate/v1"
_REVIEW_SCHEMA = "tracelane://schemas/evidence-review/v1"
_TRANSFORMATION_SCHEMA = "tracelane://schemas/evidence-transformation/v1"
_CANDIDATE_ID = re.compile(r"^candidate_[0-9a-f]{24}$")
_REVIEW_ID = re.compile(r"^review_[0-9a-f]{24}$")
_TRANSFORMATION_ID = re.compile(r"^transformation_[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_URI = re.compile(r"^tracelane://[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$")
_PROJECT_URI = re.compile(r"^tracelane://evidence/projects/([a-z][a-z0-9-]{2,63})/project\.json$")
_INDEX_URI = re.compile(r"^tracelane://evidence/projects/([a-z][a-z0-9-]{2,63})/index\.json$")
_CANDIDATE_URI = re.compile(
    r"^tracelane://evidence/projects/([a-z][a-z0-9-]{2,63})/candidates/"
    r"(candidate_[0-9a-f]{24})\.json$"
)
_REVIEW_URI = re.compile(
    r"^tracelane://evidence/projects/([a-z][a-z0-9-]{2,63})/reviews/"
    r"(review_[0-9a-f]{24})\.json$"
)
_STATUSES = ("pending", "approved", "rejected", "superseded")
_STATUS_SET = frozenset(_STATUSES)
_SOURCE_TYPES = frozenset({"primary", "secondary", "dataset"})
_ROLES = frozenset({"evidence", "future-control"})
_LICENSE_CLASSES = frozenset({"paraphrase_only", "public_domain_full_text", "licensed_full_text"})
_PROJECT_STATUSES = frozenset({"active", "paused", "completed", "archived"})


def _preflight_copy(value: object, key: str | None = None) -> object:
    if isinstance(value, Mapping):
        return {item_key: _preflight_copy(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_preflight_copy(item, key) for item in value]
    if isinstance(value, str):
        if key in {"record_sha256", "content_sha256", "sha256"} and _SHA256.fullmatch(value):
            return "derived-digest"
        if key == "candidate_id" and _CANDIDATE_ID.fullmatch(value):
            return "derived-candidate"
        if key == "project_id" and re.fullmatch(r"[a-z][a-z0-9-]{2,63}", value):
            return "derived-project"
        if key == "transformation_ids" and _TRANSFORMATION_ID.fullmatch(value):
            return "derived-transformation"
        if key == "uri" and _SAFE_URI.fullmatch(value):
            return "derived-uri"
    return value


def _record_preflight(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} record is invalid")
    try:
        _validate_persisted_json(_preflight_copy(value), label)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} contains sensitive or invalid content") from exc
    return value


def _exact_keys(
    value: Mapping[str, object],
    required: set[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    if set(value) - required - optional or required - set(value):
        raise ValueError(f"{label} record shape is invalid")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is invalid")
    return value


def _artifact_ref(value: object, label: str) -> ArtifactRef:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} reference is invalid")
    try:
        return ArtifactRef.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} reference is invalid") from exc


def _validate_json_ref(
    reference: ArtifactRef,
    *,
    kind: str,
    schema_id: str,
    uri_pattern: re.Pattern[str],
    label: str,
) -> re.Match[str]:
    try:
        reference.to_dict()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} reference is invalid") from exc
    match = uri_pattern.fullmatch(reference.uri)
    if (
        reference.kind != kind
        or reference.schema_id != schema_id
        or reference.media_type != "application/json"
        or match is None
    ):
        raise ValueError(f"{label} reference is invalid")
    return match


@dataclass(frozen=True)
class EvidenceIndexEntry:
    candidate_id: str
    candidate_ref: ArtifactRef
    effective_status: EffectiveStatus
    current_review_ref: ArtifactRef | None
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    source_type: Literal["primary", "secondary", "dataset"]
    role: Literal["evidence", "future-control"]
    domains: tuple[str, ...]
    fact_ids: tuple[str, ...]
    content_sha256: str
    license_class: Literal["paraphrase_only", "public_domain_full_text", "licensed_full_text"]
    transformation_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceIndexEntry:
        value = _record_preflight(value, "evidence index entry")
        required = {
            "candidate_id",
            "candidate_ref",
            "effective_status",
            "document_date",
            "date_precision",
            "source_type",
            "role",
            "domains",
            "fact_ids",
            "content_sha256",
            "license_class",
            "transformation_ids",
        }
        _exact_keys(
            value,
            required,
            "evidence index entry",
            optional={"current_review_ref"},
        )
        candidate_ref = _artifact_ref(value["candidate_ref"], "candidate")
        candidate_match = _validate_json_ref(
            candidate_ref,
            kind="evidence_candidate",
            schema_id=_CANDIDATE_SCHEMA,
            uri_pattern=_CANDIDATE_URI,
            label="candidate",
        )
        if "current_review_ref" in value and value["current_review_ref"] is None:
            raise ValueError("current review reference is invalid")
        review_value = value.get("current_review_ref")
        review_ref = (
            _artifact_ref(review_value, "current review") if review_value is not None else None
        )
        if review_ref is not None:
            review_match = _validate_json_ref(
                review_ref,
                kind="evidence_review",
                schema_id=_REVIEW_SCHEMA,
                uri_pattern=_REVIEW_URI,
                label="current review",
            )
            if review_match[1] != candidate_match[1]:
                raise ValueError("current review project is invalid")
        entry = cls(
            candidate_id=_text(value["candidate_id"], "candidate_id"),
            candidate_ref=candidate_ref,
            effective_status=_text(  # type: ignore[arg-type]
                value["effective_status"], "effective_status"
            ),
            current_review_ref=review_ref,
            document_date=_text(value["document_date"], "document_date"),
            date_precision=_text(value["date_precision"], "date_precision"),  # type: ignore[arg-type]
            source_type=_text(value["source_type"], "source_type"),  # type: ignore[arg-type]
            role=_text(value["role"], "role"),  # type: ignore[arg-type]
            domains=_sorted_unique_strings(value["domains"], "domains"),
            fact_ids=_sorted_unique_strings(value["fact_ids"], "fact_ids"),
            content_sha256=_text(value["content_sha256"], "content_sha256"),
            license_class=_text(value["license_class"], "license_class"),  # type: ignore[arg-type]
            transformation_ids=_sorted_unique_strings(
                value["transformation_ids"],
                "transformation_ids",
                required=False,
            ),
        )
        if _CANDIDATE_ID.fullmatch(entry.candidate_id) is None:
            raise ValueError("candidate_id is invalid")
        if candidate_match[2] != entry.candidate_id:
            raise ValueError("candidate reference identity is invalid")
        if entry.effective_status not in _STATUS_SET:
            raise ValueError("effective_status is invalid")
        if (entry.effective_status == "pending") != (entry.current_review_ref is None):
            raise ValueError("current review reference is inconsistent")
        validate_document_date(entry.document_date, entry.date_precision)
        if entry.source_type not in _SOURCE_TYPES:
            raise ValueError("source_type is invalid")
        if entry.role not in _ROLES:
            raise ValueError("role is invalid")
        _digest(entry.content_sha256, "content_sha256")
        if entry.license_class not in _LICENSE_CLASSES:
            raise ValueError("license_class is invalid")
        if any(_TRANSFORMATION_ID.fullmatch(item) is None for item in entry.transformation_ids):
            raise ValueError("transformation_ids is invalid")
        return entry

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "candidate_id": self.candidate_id,
            "candidate_ref": self.candidate_ref.to_dict(),
            "effective_status": self.effective_status,
            "document_date": self.document_date,
            "date_precision": self.date_precision,
            "source_type": self.source_type,
            "role": self.role,
            "domains": list(self.domains),
            "fact_ids": list(self.fact_ids),
            "content_sha256": self.content_sha256,
            "license_class": self.license_class,
            "transformation_ids": list(self.transformation_ids),
        }
        if self.current_review_ref is not None:
            value["current_review_ref"] = self.current_review_ref.to_dict()
        type(self).from_dict(value)
        return json.loads(canonical_json(value))


@dataclass(frozen=True)
class EvidenceProjectIndex:
    schema_id: str
    schema_version: str
    record_sha256: str
    project_id: str
    entries: tuple[EvidenceIndexEntry, ...]
    status_counts: Mapping[str, int]

    @classmethod
    def create(
        cls,
        project_id: str,
        entries: Sequence[EvidenceIndexEntry],
    ) -> EvidenceProjectIndex:
        values = tuple(entries)
        counts = {
            status: sum(item.effective_status == status for item in values) for status in _STATUSES
        }
        raw: dict[str, object] = {
            "schema_id": _PROJECT_INDEX_SCHEMA,
            "schema_version": "1.0.0",
            "record_sha256": "",
            "project_id": project_id,
            "entries": [item.to_dict() for item in values],
            "status_counts": counts,
        }
        raw["record_sha256"] = candidate_record_digest(raw)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceProjectIndex:
        value = _record_preflight(value, "project index")
        try:
            validate_document("evidence-project-index", value)
        except (TypeError, ValueError) as exc:
            raise ValueError("project index record is invalid") from exc
        entries_value = value["entries"]
        if isinstance(entries_value, (str, bytes)) or not isinstance(entries_value, Sequence):
            raise ValueError("project index entries are invalid")
        entries = tuple(EvidenceIndexEntry.from_dict(item) for item in entries_value)
        counts_value = value["status_counts"]
        if not isinstance(counts_value, Mapping):
            raise ValueError("project index status counts are invalid")
        counts = dict(counts_value)
        index = cls(
            schema_id=_text(value["schema_id"], "schema_id"),
            schema_version=_text(value["schema_version"], "schema_version"),
            record_sha256=_text(value["record_sha256"], "record_sha256"),
            project_id=_text(value["project_id"], "project_id"),
            entries=entries,
            status_counts=counts,  # type: ignore[arg-type]
        )
        _require_project_id(index.project_id)
        identifiers = tuple(item.candidate_id for item in index.entries)
        if identifiers != tuple(sorted(identifiers)) or len(set(identifiers)) != len(identifiers):
            raise ValueError("project index entries must be sorted and unique")
        for entry in index.entries:
            match = _CANDIDATE_URI.fullmatch(entry.candidate_ref.uri)
            if match is None or match[1] != index.project_id:
                raise ValueError("candidate reference project is invalid")
            if entry.current_review_ref is not None:
                review_match = _REVIEW_URI.fullmatch(entry.current_review_ref.uri)
                if review_match is None or review_match[1] != index.project_id:
                    raise ValueError("current review reference project is invalid")
        if set(counts) != set(_STATUSES) or any(
            type(counts[status]) is not int or counts[status] < 0 for status in _STATUSES
        ):
            raise ValueError("project index status counts are invalid")
        expected_counts = {
            status: sum(item.effective_status == status for item in index.entries)
            for status in _STATUSES
        }
        if counts != expected_counts:
            raise ValueError("project index status counts are stale")
        _digest(index.record_sha256, "record_sha256")
        if candidate_record_digest(index._raw_dict()) != index.record_sha256:
            raise ValueError("project index record digest is stale")
        return index

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_sha256": self.record_sha256,
            "project_id": self.project_id,
            "entries": [item.to_dict() for item in self.entries],
            "status_counts": {status: self.status_counts[status] for status in _STATUSES},
        }

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return json.loads(canonical_json(self._raw_dict()))


@dataclass(frozen=True)
class EvidenceRegistryEntry:
    project_id: str
    title: str
    status: Literal["active", "paused", "completed", "archived"]
    project_ref: ArtifactRef
    index_ref: ArtifactRef

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceRegistryEntry:
        value = _record_preflight(value, "registry entry")
        _exact_keys(
            value,
            {"project_id", "title", "status", "project_ref", "index_ref"},
            "registry entry",
        )
        entry = cls(
            project_id=_text(value["project_id"], "project_id"),
            title=_text(value["title"], "title"),
            status=_text(value["status"], "status"),  # type: ignore[arg-type]
            project_ref=_artifact_ref(value["project_ref"], "project"),
            index_ref=_artifact_ref(value["index_ref"], "project index"),
        )
        _require_project_id(entry.project_id)
        _non_empty(entry.title, "title")
        if entry.status not in _PROJECT_STATUSES:
            raise ValueError("project status is invalid")
        project_match = _validate_json_ref(
            entry.project_ref,
            kind="evidence_project",
            schema_id=_PROJECT_SCHEMA,
            uri_pattern=_PROJECT_URI,
            label="project",
        )
        index_match = _validate_json_ref(
            entry.index_ref,
            kind="evidence_project_index",
            schema_id=_PROJECT_INDEX_SCHEMA,
            uri_pattern=_INDEX_URI,
            label="project index",
        )
        if project_match[1] != entry.project_id or index_match[1] != entry.project_id:
            raise ValueError("registry entry project identity is invalid")
        return entry

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status,
            "project_ref": self.project_ref.to_dict(),
            "index_ref": self.index_ref.to_dict(),
        }
        type(self).from_dict(value)
        return json.loads(canonical_json(value))


@dataclass(frozen=True)
class EvidenceRegistry:
    schema_id: str
    schema_version: str
    record_sha256: str
    projects: tuple[EvidenceRegistryEntry, ...]

    @classmethod
    def create(
        cls,
        projects: Sequence[EvidenceRegistryEntry],
    ) -> EvidenceRegistry:
        values = tuple(projects)
        raw: dict[str, object] = {
            "schema_id": _REGISTRY_SCHEMA,
            "schema_version": "1.0.0",
            "record_sha256": "",
            "projects": [item.to_dict() for item in values],
        }
        raw["record_sha256"] = candidate_record_digest(raw)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceRegistry:
        value = _record_preflight(value, "evidence registry")
        try:
            validate_document("evidence-registry", value)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence registry record is invalid") from exc
        projects_value = value["projects"]
        if isinstance(projects_value, (str, bytes)) or not isinstance(projects_value, Sequence):
            raise ValueError("evidence registry projects are invalid")
        registry = cls(
            schema_id=_text(value["schema_id"], "schema_id"),
            schema_version=_text(value["schema_version"], "schema_version"),
            record_sha256=_text(value["record_sha256"], "record_sha256"),
            projects=tuple(EvidenceRegistryEntry.from_dict(item) for item in projects_value),
        )
        identifiers = tuple(item.project_id for item in registry.projects)
        project_uris = tuple(item.project_ref.uri for item in registry.projects)
        index_uris = tuple(item.index_ref.uri for item in registry.projects)
        if identifiers != tuple(sorted(identifiers)) or any(
            len(set(values)) != len(values) for values in (identifiers, project_uris, index_uris)
        ):
            raise ValueError("evidence registry projects must be sorted and unique")
        _digest(registry.record_sha256, "record_sha256")
        if candidate_record_digest(registry._raw_dict()) != registry.record_sha256:
            raise ValueError("evidence registry record digest is stale")
        return registry

    def _raw_dict(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_sha256": self.record_sha256,
            "projects": [item.to_dict() for item in self.projects],
        }

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return json.loads(canonical_json(self._raw_dict()))


@dataclass(frozen=True)
class EvidenceQuery:
    project_id: str
    statuses: tuple[str, ...] = ()
    fact_id: str | None = None
    domain: str | None = None
    role: str | None = None
    source_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    clean_only: bool = False


@dataclass(frozen=True)
class VerificationReport:
    project_count: int
    candidate_count: int
    review_count: int
    future_control_count: int
    status_counts: Mapping[str, int]
    registry_sha256: str
    project_index_sha256: str | None


@dataclass(frozen=True)
class _ProjectFiles:
    project_id: str
    project: Path
    index: Path | None
    candidates: tuple[Path, ...]
    reviews: tuple[Path, ...]
    transformations: tuple[Path, ...]
    source_inventory: tuple[str, ...]


@dataclass(frozen=True)
class _ProjectSnapshot:
    source_inventory: tuple[str, ...]
    project: EvidenceProject
    project_ref: ArtifactRef
    candidates: tuple[tuple[ProjectEvidenceCandidate, ArtifactRef], ...]
    reviews: tuple[tuple[EvidenceReview, ArtifactRef], ...]
    transformations: tuple[tuple[EvidenceTransformation, ArtifactRef], ...]
    expected_index: EvidenceProjectIndex


def _as_root(root: EvidenceRoot | str | Path) -> EvidenceRoot:
    try:
        path = root.path if isinstance(root, EvidenceRoot) else root
        return EvidenceRoot.open(path)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("evidence root is invalid") from exc


def _metadata(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if _is_link_or_reparse(metadata):
        raise ValueError(f"{label} contains an unsafe link")
    return metadata


def _directory_children(path: Path, label: str) -> tuple[Path, ...]:
    metadata = _metadata(path, label)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} is unavailable")
    try:
        return tuple(sorted(path.iterdir(), key=lambda item: item.name))
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc


def _managed_json_files(project_dir: Path, name: str) -> tuple[Path, ...]:
    directory = project_dir / name
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise ValueError(f"{name} inventory is unavailable") from exc
    if _is_link_or_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{name} inventory contains an unsafe entry")
    values: list[Path] = []
    for path in _directory_children(directory, f"{name} inventory"):
        metadata = _metadata(path, f"{name} inventory")
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or path.suffix != ".json":
            raise ValueError(f"{name} inventory contains an unsupported entry")
        values.append(path)
    return tuple(values)


def _discover_project(root: EvidenceRoot, project_id: str) -> _ProjectFiles:
    _require_project_id(project_id)
    project_dir = root.path / "projects" / project_id
    allowed = {
        "README.md",
        "project.json",
        "index.json",
        "candidates",
        "reviews",
        "transformations",
    }
    children = _directory_children(project_dir, "evidence project inventory")
    names = {path.name for path in children}
    if "project.json" not in names or names - allowed:
        raise ValueError("evidence project inventory is invalid")
    for path in children:
        metadata = _metadata(path, "evidence project inventory")
        if path.name in {"candidates", "reviews", "transformations"}:
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("evidence project inventory is invalid")
        elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("evidence project inventory is invalid")
    candidates = _managed_json_files(project_dir, "candidates")
    reviews = _managed_json_files(project_dir, "reviews")
    transformations = _managed_json_files(project_dir, "transformations")
    source_inventory = tuple(
        sorted(
            [path.name for path in children if path.name != "index.json"]
            + [f"candidates/{path.name}" for path in candidates]
            + [f"reviews/{path.name}" for path in reviews]
            + [f"transformations/{path.name}" for path in transformations]
        )
    )
    return _ProjectFiles(
        project_id=project_id,
        project=project_dir / "project.json",
        index=(project_dir / "index.json") if "index.json" in names else None,
        candidates=candidates,
        reviews=reviews,
        transformations=transformations,
        source_inventory=source_inventory,
    )


def _discover_project_ids(root: EvidenceRoot) -> tuple[str, ...]:
    allowed_root = {"README.md", "registry.json", "blobs", "projects"}
    root_children = _directory_children(root.path, "evidence root inventory")
    if {path.name for path in root_children} - allowed_root:
        raise ValueError("evidence root inventory is invalid")
    for path in root_children:
        metadata = _metadata(path, "evidence root inventory")
        if path.name in {"blobs", "projects"}:
            if not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("evidence root inventory is invalid")
        elif not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("evidence root inventory is invalid")
    projects_dir = root.path / "projects"
    if not projects_dir.exists():
        return ()
    identifiers: list[str] = []
    for path in _directory_children(projects_dir, "evidence projects inventory"):
        metadata = _metadata(path, "evidence projects inventory")
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("evidence projects inventory is invalid")
        try:
            identifiers.append(_require_project_id(path.name))
        except ValueError as exc:
            raise ValueError("evidence projects inventory is invalid") from exc
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("evidence projects inventory contains duplicate IDs")
    return tuple(sorted(identifiers))


def _json_uri(project_id: str, category: str, filename: str) -> str:
    if category == "project":
        return f"tracelane://evidence/projects/{project_id}/project.json"
    if category == "index":
        return f"tracelane://evidence/projects/{project_id}/index.json"
    return f"tracelane://evidence/projects/{project_id}/{category}/{filename}"


def _read_discovered_json(
    root: EvidenceRoot,
    path: Path,
    *,
    uri: str,
    kind: str,
    schema_id: str,
    label: str,
) -> tuple[dict[str, object], ArtifactRef, bytes]:
    try:
        data = _secure_read(path, root, label)
        reference = ArtifactRef.from_dict(
            {
                "kind": kind,
                "uri": uri,
                "media_type": "application/json",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "schema_id": schema_id,
            }
        )
        value = read_json_object(
            root,
            reference,
            expected_kind=kind,
            expected_schema_id=schema_id,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    return value, reference, data


def _load_records(
    root: EvidenceRoot,
    files: _ProjectFiles,
) -> tuple[
    EvidenceProject,
    ArtifactRef,
    list[tuple[ProjectEvidenceCandidate, ArtifactRef]],
    list[tuple[EvidenceReview, ArtifactRef]],
    list[tuple[EvidenceTransformation, ArtifactRef]],
]:
    project_value, project_ref, _ = _read_discovered_json(
        root,
        files.project,
        uri=_json_uri(files.project_id, "project", "project.json"),
        kind="evidence_project",
        schema_id=_PROJECT_SCHEMA,
        label="evidence project record",
    )
    try:
        project = EvidenceProject.from_dict(project_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence project record is invalid") from exc
    if project.project_id != files.project_id:
        raise ValueError("evidence project identity is invalid")

    candidates: list[tuple[ProjectEvidenceCandidate, ArtifactRef]] = []
    candidate_ids: set[str] = set()
    for path in files.candidates:
        value, reference, _ = _read_discovered_json(
            root,
            path,
            uri=_json_uri(files.project_id, "candidates", path.name),
            kind="evidence_candidate",
            schema_id=_CANDIDATE_SCHEMA,
            label="evidence candidate record",
        )
        try:
            candidate = ProjectEvidenceCandidate.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence candidate record is invalid") from exc
        if candidate.candidate_id in candidate_ids:
            raise ValueError("candidate inventory contains duplicate IDs")
        candidate_ids.add(candidate.candidate_id)
        if (
            candidate.project_id != files.project_id
            or path.name != f"{candidate.candidate_id}.json"
        ):
            raise ValueError("candidate inventory identity is invalid")
        candidates.append((candidate, reference))

    reviews: list[tuple[EvidenceReview, ArtifactRef]] = []
    review_ids: set[str] = set()
    for path in files.reviews:
        value, reference, _ = _read_discovered_json(
            root,
            path,
            uri=_json_uri(files.project_id, "reviews", path.name),
            kind="evidence_review",
            schema_id=_REVIEW_SCHEMA,
            label="evidence review record",
        )
        try:
            review = EvidenceReview.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence review record is invalid") from exc
        if review.review_id in review_ids:
            raise ValueError("review inventory contains duplicate IDs")
        review_ids.add(review.review_id)
        if review.project_id != files.project_id or path.name != f"{review.review_id}.json":
            raise ValueError("review inventory identity is invalid")
        reviews.append((review, reference))

    transformations: list[tuple[EvidenceTransformation, ArtifactRef]] = []
    transformation_ids: set[str] = set()
    for path in files.transformations:
        value, reference, _ = _read_discovered_json(
            root,
            path,
            uri=_json_uri(files.project_id, "transformations", path.name),
            kind="evidence_transformation",
            schema_id=_TRANSFORMATION_SCHEMA,
            label="evidence transformation record",
        )
        try:
            transformation = EvidenceTransformation.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence transformation record is invalid") from exc
        if transformation.transformation_id in transformation_ids:
            raise ValueError("transformation inventory contains duplicate IDs")
        transformation_ids.add(transformation.transformation_id)
        if (
            transformation.project_id != files.project_id
            or path.name != f"{transformation.transformation_id}.json"
        ):
            raise ValueError("transformation inventory identity is invalid")
        transformations.append((transformation, reference))
    return project, project_ref, candidates, reviews, transformations


def _without_schema(reference: ArtifactRef) -> ArtifactRef:
    value = reference.to_dict()
    value.pop("schema_id", None)
    return ArtifactRef.from_dict(value)


def _validate_closure(
    root: EvidenceRoot,
    project: EvidenceProject,
    candidates: Sequence[tuple[ProjectEvidenceCandidate, ArtifactRef]],
    reviews: Sequence[tuple[EvidenceReview, ArtifactRef]],
    transformations: Sequence[tuple[EvidenceTransformation, ArtifactRef]],
) -> EvidenceProjectIndex:
    blob_store = EvidenceBlobStore(root)
    candidate_by_id = {candidate.candidate_id: candidate for candidate, _ in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("candidate inventory contains duplicate IDs")
    reviews_by_candidate: dict[str, list[tuple[EvidenceReview, ArtifactRef]]] = {
        candidate_id: [] for candidate_id in candidate_by_id
    }
    for review, reference in reviews:
        if review.candidate_id not in reviews_by_candidate:
            raise ValueError("review inventory contains an orphan record")
        reviews_by_candidate[review.candidate_id].append((review, reference))
    transformations_by_id = {
        transformation.transformation_id: (transformation, reference)
        for transformation, reference in transformations
    }
    if len(transformations_by_id) != len(transformations):
        raise ValueError("transformation inventory contains duplicate IDs")

    consumed_transformations: dict[str, int] = {
        transformation_id: 0 for transformation_id in transformations_by_id
    }
    entries: list[EvidenceIndexEntry] = []
    for candidate, candidate_ref in candidates:
        if candidate.source_type not in project.admitted_source_types:
            raise ValueError("candidate source type is not admitted by the project")
        blob_store.verify(candidate.content_ref)
        if candidate.role != "future-control":
            _, possible_end = _date_interval(candidate.document_date)
            possible_end_at = datetime.combine(
                possible_end,
                time(23, 59, 59),
                tzinfo=UTC,
            )
            if possible_end_at > project.historical_cutoff_at.astimezone(UTC):
                raise ValueError("candidate date exceeds the project cutoff")

        transformation_ids: list[str] = []
        previous_output: ArtifactRef | None = None
        for reference in candidate.transformation_refs:
            transformation_id = Path(reference.uri).stem
            item = transformations_by_id.get(transformation_id)
            if item is None:
                raise ValueError("candidate transformation reference is missing")
            transformation, discovered_ref = item
            if (
                reference != _without_schema(discovered_ref)
                or transformation.project_id != candidate.project_id
                or transformation.candidate_id != candidate.candidate_id
            ):
                raise ValueError("candidate transformation reference is invalid")
            consumed_transformations[transformation_id] += 1
            blob_store.verify(transformation.input_ref)
            blob_store.verify(transformation.output_ref)
            if previous_output is not None and previous_output != transformation.input_ref:
                raise ValueError("candidate transformation lineage is disconnected")
            previous_output = transformation.output_ref
            transformation_ids.append(transformation_id)
        if candidate.transformation_refs and previous_output != candidate.content_ref:
            raise ValueError("candidate transformation lineage is disconnected")

        candidate_reviews = reviews_by_candidate[candidate.candidate_id]
        try:
            chain = validate_review_chain(
                candidate,
                tuple(review for review, _ in candidate_reviews),
            )
            current = current_review(
                candidate,
                tuple(review for review, _ in candidate_reviews),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence review chain is invalid") from exc
        review_ref = None
        if current is not None:
            review_ref = next(
                reference
                for review, reference in candidate_reviews
                if review.review_id == current.review_id
            )
        entries.append(
            EvidenceIndexEntry.from_dict(
                {
                    "candidate_id": candidate.candidate_id,
                    "candidate_ref": candidate_ref.to_dict(),
                    "effective_status": chain.effective_status,
                    **(
                        {"current_review_ref": review_ref.to_dict()}
                        if review_ref is not None
                        else {}
                    ),
                    "document_date": candidate.document_date,
                    "date_precision": candidate.date_precision,
                    "source_type": candidate.source_type,
                    "role": candidate.role,
                    "domains": list(candidate.domains),
                    "fact_ids": list(candidate.fact_ids),
                    "content_sha256": candidate.content_sha256,
                    "license_class": candidate.retention_policy,
                    "transformation_ids": sorted(transformation_ids),
                }
            )
        )
    if any(count != 1 for count in consumed_transformations.values()):
        raise ValueError("transformation inventory contains an orphan or duplicate record")
    return EvidenceProjectIndex.create(
        project.project_id,
        tuple(sorted(entries, key=lambda item: item.candidate_id)),
    )


def _load_project_snapshot(
    root: EvidenceRoot,
    project_id: str,
    files: _ProjectFiles | None = None,
) -> _ProjectSnapshot:
    files = files or _discover_project(root, project_id)
    project, project_ref, candidates, reviews, transformations = _load_records(root, files)
    expected = _validate_closure(
        root,
        project,
        candidates,
        reviews,
        transformations,
    )
    return _ProjectSnapshot(
        source_inventory=files.source_inventory,
        project=project,
        project_ref=project_ref,
        candidates=tuple(candidates),
        reviews=tuple(reviews),
        transformations=tuple(transformations),
        expected_index=expected,
    )


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return canonical_json(value).encode("utf-8") + b"\n"


def _json_reference(
    uri: str,
    kind: str,
    schema_id: str,
    value: Mapping[str, object],
) -> ArtifactRef:
    data = _canonical_bytes(value)
    return ArtifactRef.from_dict(
        {
            "kind": kind,
            "uri": uri,
            "media_type": "application/json",
            "schema_id": schema_id,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
    )


def _read_persisted_index(
    root: EvidenceRoot,
    project_id: str,
    expected: EvidenceProjectIndex,
) -> ArtifactRef:
    path = root.path / "projects" / project_id / "index.json"
    value, reference, data = _read_discovered_json(
        root,
        path,
        uri=_json_uri(project_id, "index", "index.json"),
        kind="evidence_project_index",
        schema_id=_PROJECT_INDEX_SCHEMA,
        label="project index",
    )
    try:
        EvidenceProjectIndex.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("project index is invalid") from exc
    if data != _canonical_bytes(expected.to_dict()):
        raise ValueError("project index does not match source records")
    return reference


def _derive_registry_once(
    root: EvidenceRoot,
    index_overrides: Mapping[str, ArtifactRef] | None = None,
) -> tuple[
    EvidenceRegistry,
    tuple[_ProjectSnapshot, ...],
    Mapping[str, ArtifactRef],
]:
    overrides = dict(index_overrides or {})
    project_ids = _discover_project_ids(root)
    if set(overrides) - set(project_ids):
        raise ValueError("evidence registry override is invalid")
    files = tuple(_discover_project(root, project_id) for project_id in project_ids)
    snapshots = tuple(_load_project_snapshot(root, item.project_id, item) for item in files)
    index_refs: dict[str, ArtifactRef] = {}
    entries: list[EvidenceRegistryEntry] = []
    for snapshot in snapshots:
        index_ref = overrides.get(snapshot.project.project_id)
        if index_ref is None:
            index_ref = _read_persisted_index(
                root,
                snapshot.project.project_id,
                snapshot.expected_index,
            )
        else:
            expected_ref = _json_reference(
                _json_uri(
                    snapshot.project.project_id,
                    "index",
                    "index.json",
                ),
                "evidence_project_index",
                _PROJECT_INDEX_SCHEMA,
                snapshot.expected_index.to_dict(),
            )
            if index_ref != expected_ref:
                raise ValueError("project index override is invalid")
        index_refs[snapshot.project.project_id] = index_ref
        entries.append(
            EvidenceRegistryEntry.from_dict(
                {
                    "project_id": snapshot.project.project_id,
                    "title": snapshot.project.title,
                    "status": snapshot.project.status,
                    "project_ref": snapshot.project_ref.to_dict(),
                    "index_ref": index_ref.to_dict(),
                }
            )
        )
    return (
        EvidenceRegistry.create(tuple(sorted(entries, key=lambda item: item.project_id))),
        snapshots,
        index_refs,
    )


def _reauthenticate_project_snapshot(
    root: EvidenceRoot,
    expected: _ProjectSnapshot,
) -> _ProjectSnapshot:
    try:
        current = _load_project_snapshot(root, expected.project.project_id)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("evidence source snapshot changed") from exc
    if current != expected:
        raise ValueError("evidence source snapshot changed")
    return current


def _reauthenticate_registry_state(
    root: EvidenceRoot,
    expected: tuple[
        EvidenceRegistry,
        tuple[_ProjectSnapshot, ...],
        Mapping[str, ArtifactRef],
    ],
    index_overrides: Mapping[str, ArtifactRef] | None = None,
) -> tuple[
    EvidenceRegistry,
    tuple[_ProjectSnapshot, ...],
    Mapping[str, ArtifactRef],
]:
    try:
        current = _derive_registry_once(root, index_overrides)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("evidence source snapshot changed") from exc
    if current != expected:
        raise ValueError("evidence source snapshot changed")
    return current


def build_project_index(
    root: EvidenceRoot | str | Path,
    project_id: str,
) -> EvidenceProjectIndex:
    authenticated_root = _as_root(root)
    initial = _load_project_snapshot(authenticated_root, project_id)
    final = _reauthenticate_project_snapshot(authenticated_root, initial)
    return final.expected_index


def rebuild_project_index(
    root: EvidenceRoot | str | Path,
    project_id: str,
) -> ArtifactRef:
    authenticated_root = _as_root(root)
    initial = _load_project_snapshot(authenticated_root, project_id)
    final = _reauthenticate_project_snapshot(authenticated_root, initial)
    reference = write_json_create_or_match(
        authenticated_root,
        _json_uri(project_id, "index", "index.json"),
        "evidence_project_index",
        _PROJECT_INDEX_SCHEMA,
        final.expected_index.to_dict(),
    )
    _reauthenticate_project_snapshot(authenticated_root, initial)
    return reference


def build_registry(root: EvidenceRoot | str | Path) -> EvidenceRegistry:
    authenticated_root = _as_root(root)
    initial = _derive_registry_once(authenticated_root)
    final = _reauthenticate_registry_state(authenticated_root, initial)
    return final[0]


def rebuild_registry(root: EvidenceRoot | str | Path) -> ArtifactRef:
    authenticated_root = _as_root(root)
    initial = _derive_registry_once(authenticated_root)
    final = _reauthenticate_registry_state(authenticated_root, initial)
    reference = write_json_create_or_match(
        authenticated_root,
        "tracelane://evidence/registry.json",
        "evidence_registry",
        _REGISTRY_SCHEMA,
        final[0].to_dict(),
    )
    _reauthenticate_registry_state(authenticated_root, initial)
    return reference


def rebuild_evidence_indexes(
    root: EvidenceRoot | str | Path,
    project_id: str,
) -> tuple[ArtifactRef, ArtifactRef]:
    try:
        _require_project_id(project_id)
    except ValueError as exc:
        raise ValueError("evidence rebuild request is invalid") from exc
    root_path = root.path if isinstance(root, EvidenceRoot) else Path(root)
    with evidence_root_mutation_lock(root_path):
        authenticated_root = _as_root(root_path)
        index_uri = _json_uri(project_id, "index", "index.json")
        index_path = authenticated_root.resolve(index_uri)
        registry_uri = "tracelane://evidence/registry.json"
        registry_path = authenticated_root.resolve(registry_uri)
        index_exists = index_path.exists()
        registry_exists = registry_path.exists()
        if index_exists != registry_exists:
            raise ValueError("evidence derived state conflicts")
        if index_exists:
            snapshots, index_refs, registry_ref = _verified_state(authenticated_root)
            if not any(snapshot.project.project_id == project_id for snapshot in snapshots):
                raise ValueError("evidence rebuild request is invalid")
            return index_refs[project_id], registry_ref

        initial_project = _load_project_snapshot(
            authenticated_root,
            project_id,
        )
        final_project = _reauthenticate_project_snapshot(
            authenticated_root,
            initial_project,
        )
        index_ref = _json_reference(
            index_uri,
            "evidence_project_index",
            _PROJECT_INDEX_SCHEMA,
            final_project.expected_index.to_dict(),
        )
        overrides = {project_id: index_ref}
        initial_registry = _derive_registry_once(
            authenticated_root,
            overrides,
        )
        final_registry = _reauthenticate_registry_state(
            authenticated_root,
            initial_registry,
            overrides,
        )
        registry_ref = _json_reference(
            registry_uri,
            "evidence_registry",
            _REGISTRY_SCHEMA,
            final_registry[0].to_dict(),
        )
        if index_path.exists() or registry_path.exists():
            raise ValueError("evidence derived state changed")
        _reauthenticate_project_snapshot(
            authenticated_root,
            initial_project,
        )
        _reauthenticate_registry_state(
            authenticated_root,
            initial_registry,
            overrides,
        )

        created: list[JsonPublicationReceipt] = []
        try:
            published_index = write_json_create_or_match_receipt(
                authenticated_root,
                index_uri,
                "evidence_project_index",
                _PROJECT_INDEX_SCHEMA,
                final_project.expected_index.to_dict(),
            )
            if published_index.reference != index_ref:
                raise ValueError("project index publication changed")
            if published_index.created_by_this_call:
                created.append(published_index)
            published_registry = write_json_create_or_match_receipt(
                authenticated_root,
                registry_uri,
                "evidence_registry",
                _REGISTRY_SCHEMA,
                final_registry[0].to_dict(),
            )
            if published_registry.reference != registry_ref:
                raise ValueError("evidence registry publication changed")
            if published_registry.created_by_this_call:
                created.append(published_registry)
            verified = verify_evidence_registry(
                authenticated_root,
                project_id,
            )
            if (
                verified.project_index_sha256 != index_ref.sha256
                or verified.registry_sha256 != registry_ref.sha256
            ):
                raise ValueError("evidence derived verification changed")
            return index_ref, registry_ref
        except (OSError, TypeError, ValueError):
            rollback_failed = False
            for receipt in reversed(created):
                try:
                    rollback_json_publication(authenticated_root, receipt)
                except (OSError, TypeError, ValueError):
                    rollback_failed = True
            if rollback_failed:
                raise ValueError("evidence derived rollback failed") from None
            raise ValueError("evidence derived publication failed") from None


def _read_registry(
    root: EvidenceRoot,
    expected: EvidenceRegistry,
) -> ArtifactRef:
    value, reference, data = _read_discovered_json(
        root,
        root.path / "registry.json",
        uri="tracelane://evidence/registry.json",
        kind="evidence_registry",
        schema_id=_REGISTRY_SCHEMA,
        label="evidence registry",
    )
    try:
        EvidenceRegistry.from_dict(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence registry is invalid") from exc
    if data != _canonical_bytes(expected.to_dict()):
        raise ValueError("evidence registry does not match source records")
    return reference


def _verified_state(
    root: EvidenceRoot,
) -> tuple[
    tuple[_ProjectSnapshot, ...],
    Mapping[str, ArtifactRef],
    ArtifactRef,
]:
    initial = _derive_registry_once(root)
    initial_registry_ref = _read_registry(root, initial[0])
    final = _reauthenticate_registry_state(root, initial)
    final_registry_ref = _read_registry(root, final[0])
    if final_registry_ref != initial_registry_ref:
        raise ValueError("evidence source snapshot changed")
    return final[1], final[2], final_registry_ref


def verify_evidence_registry(
    root: EvidenceRoot | str | Path,
    project_id: str | None = None,
) -> VerificationReport:
    if project_id is not None:
        try:
            _require_project_id(project_id)
        except ValueError as exc:
            raise ValueError("project verification request is invalid") from exc
    authenticated_root = _as_root(root)
    snapshots, index_refs, registry_ref = _verified_state(authenticated_root)
    selected = snapshots
    project_index_sha256 = None
    if project_id is not None:
        selected = tuple(item for item in snapshots if item.project.project_id == project_id)
        if not selected:
            raise ValueError("project verification request is invalid")
        project_index_sha256 = index_refs[project_id].sha256
    status_counts = {
        status: sum(snapshot.expected_index.status_counts[status] for snapshot in selected)
        for status in _STATUSES
    }
    return VerificationReport(
        project_count=len(selected),
        candidate_count=sum(len(item.candidates) for item in selected),
        review_count=sum(len(item.reviews) for item in selected),
        future_control_count=sum(
            candidate.role == "future-control"
            for item in selected
            for candidate, _ in item.candidates
        ),
        status_counts=status_counts,
        registry_sha256=registry_ref.sha256,
        project_index_sha256=project_index_sha256,
    )


def _date_interval(value: str) -> tuple[date, date]:
    match = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", value)
    if match is None:
        raise ValueError("date is invalid")
    year = int(match[1])
    month_text = match[2]
    day_text = match[3]
    try:
        if month_text is None:
            return date(year, 1, 1), date(year, 12, 31)
        month = int(month_text)
        if day_text is None:
            return (
                date(year, month, 1),
                date(year, month, calendar.monthrange(year, month)[1]),
            )
        value_date = date(year, month, int(day_text))
    except (ValueError, calendar.IllegalMonthError) as exc:
        raise ValueError("date is invalid") from exc
    return value_date, value_date


def _validated_query(
    query: EvidenceQuery,
) -> tuple[date | None, date | None]:
    try:
        if not isinstance(query, EvidenceQuery):
            raise ValueError
        _require_project_id(query.project_id)
        if (
            isinstance(query.statuses, (str, bytes))
            or not isinstance(query.statuses, tuple)
            or any(not isinstance(item, str) for item in query.statuses)
            or set(query.statuses) - _STATUS_SET
        ):
            raise ValueError
        for value in (query.fact_id, query.domain):
            if value is not None:
                _non_empty(value, "query filter")
        if query.role is not None and query.role not in _ROLES:
            raise ValueError
        if query.source_type is not None and query.source_type not in _SOURCE_TYPES:
            raise ValueError
        if type(query.clean_only) is not bool:
            raise ValueError
        lower = _date_interval(query.date_from)[0] if query.date_from is not None else None
        upper = _date_interval(query.date_to)[1] if query.date_to is not None else None
        if lower is not None and upper is not None and lower > upper:
            raise ValueError
        return lower, upper
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence query is invalid") from exc


def find_evidence(
    root: EvidenceRoot | str | Path,
    query: EvidenceQuery,
) -> tuple[EvidenceIndexEntry, ...]:
    lower, upper = _validated_query(query)
    authenticated_root = _as_root(root)
    snapshots, _, _ = _verified_state(authenticated_root)
    selected = tuple(item for item in snapshots if item.project.project_id == query.project_id)
    if not selected:
        raise ValueError("evidence query is invalid")
    index = selected[0].expected_index
    values: list[EvidenceIndexEntry] = []
    for entry in index.entries:
        start, end = _date_interval(entry.document_date)
        if query.statuses and entry.effective_status not in query.statuses:
            continue
        if query.fact_id is not None and query.fact_id not in entry.fact_ids:
            continue
        if query.domain is not None and query.domain not in entry.domains:
            continue
        if query.role is not None and entry.role != query.role:
            continue
        if query.source_type is not None and entry.source_type != query.source_type:
            continue
        if query.clean_only and entry.role == "future-control":
            continue
        if lower is not None and end < lower:
            continue
        if upper is not None and start > upper:
            continue
        values.append(entry)
    return tuple(values)
