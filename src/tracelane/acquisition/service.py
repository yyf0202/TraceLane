from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from tracelane.contracts import canonical_json
from tracelane.history.contracts import (
    EvidenceRecordV2,
    compute_evidence_provenance_sha256,
)
from tracelane.security import classify_and_redact
from tracelane.v2.contracts import (
    ArtifactRef,
    content_digest,
    validate_transformation_ref,
)
from tracelane.v2.locking import exclusive_file_lock
from tracelane.v2.manifests import artifact_ref_for_file
from tracelane.v2.schema import validate_document, validate_document_date
from tracelane.v2.storage import (
    ArtifactRoot,
    BlobStore,
    atomic_create_bytes,
    atomic_write_bytes,
    retire_authenticated_file,
    secure_read_bytes,
)

from .contracts import (
    CandidateReview,
    EvidenceCandidate,
    canonical_source_url,
    compute_candidate_id,
    source_locator_sha256,
)

_SESSION_ID = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_CANDIDATE_ID = re.compile(r"^candidate_[0-9a-f]{24}$")
_EVIDENCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_CONTENT_BYTES = 5_000_000
_TRANSACTION_FILE = "promotion-transaction.json"


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _candidate_id(value: str) -> str:
    if not isinstance(value, str) or not _CANDIDATE_ID.fullmatch(value):
        raise ValueError("candidate_id is invalid")
    return value


def _candidate_uri(session_id: str, candidate_id: str) -> str:
    return (
        f"tracelane://artifacts/acquisition/{session_id}/"
        f"candidates/{_candidate_id(candidate_id)}.json"
    )


def _review_uri(session_id: str, candidate_id: str) -> str:
    return (
        f"tracelane://artifacts/acquisition/{session_id}/reviews/{_candidate_id(candidate_id)}.json"
    )


def _promoted_uri(session_id: str, evidence_id: str) -> str:
    if not isinstance(evidence_id, str) or not _EVIDENCE_ID.fullmatch(evidence_id):
        raise ValueError("evidence_id is invalid")
    return f"tracelane://artifacts/acquisition/{session_id}/promoted/{evidence_id}.json"


def _read_json_object(path: Path, *, root: Path) -> Mapping[str, object]:
    try:
        value = json.loads(
            secure_read_bytes(
                path,
                root=root,
                label="acquisition JSON document",
            ).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"JSON document is unavailable: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path.name}")
    return value


def _write_json(
    path: Path,
    value: object,
    *,
    root: Path,
    create_new: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = atomic_create_bytes if create_new else atomic_write_bytes
    writer(
        path,
        (canonical_json(value) + "\n").encode(),
        root=root,
        label="acquisition JSON document",
    )


def _json_bytes(value: object) -> bytes:
    return (canonical_json(value) + "\n").encode()


def _json_artifact_ref(
    *,
    uri: str,
    kind: str,
    schema_id: str,
    value: object,
) -> ArtifactRef:
    data = _json_bytes(value)
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


def _review_for_persistence(
    review: CandidateReview,
    candidate: EvidenceCandidate,
    *,
    secrets: Sequence[str] = (),
) -> CandidateReview:
    sanitized = classify_and_redact(
        {
            "reviewer": review.reviewer,
            "license": review.license,
            "reason": review.reason,
        },
        secrets=secrets,
    )
    if not isinstance(sanitized.value, Mapping):
        raise ValueError("redacted candidate review must remain an object")
    value: dict[str, object] = {
        "schema_id": "tracelane://schemas/candidate-review/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "candidate_id": review.candidate_id,
        "candidate_record_sha256": review.candidate_record_sha256,
        "candidate_content_sha256": review.candidate_content_sha256,
        "source_locator_sha256": review.source_locator_sha256,
        "decision": review.decision,
        "reviewer": sanitized.value["reviewer"],
        "reviewed_at": review.reviewed_at,
        "document_date": candidate.document_date,
        "date_precision": candidate.date_precision,
        "available_at": review.available_at,
        "source_type": review.source_type,
        "license": sanitized.value["license"],
        "reason": sanitized.value["reason"],
    }
    value["content_sha256"] = content_digest(value)
    return CandidateReview.from_dict(value)


def _validate_review_candidate_lineage(
    review: CandidateReview,
    candidate: EvidenceCandidate,
) -> None:
    expected_bindings = (
        candidate.candidate_id,
        candidate.record_sha256,
        candidate.content_sha256,
        source_locator_sha256(candidate.source_url),
        candidate.document_date,
        candidate.date_precision,
    )
    review_bindings = (
        review.candidate_id,
        review.candidate_record_sha256,
        review.candidate_content_sha256,
        review.source_locator_sha256,
        review.document_date,
        review.date_precision,
    )
    if review_bindings != expected_bindings:
        raise ValueError("review does not match candidate record")


def _validate_promoted_lineage(
    record: EvidenceRecordV2,
    candidate_ref: ArtifactRef,
    candidate: EvidenceCandidate,
    review_ref: ArtifactRef,
    review: CandidateReview,
) -> None:
    if record.candidate_ref != candidate_ref:
        raise ValueError("promoted evidence candidate_ref lineage mismatch")
    if record.review_ref != review_ref:
        raise ValueError("promoted evidence review_ref lineage mismatch")
    _validate_review_candidate_lineage(review, candidate)
    if review.decision != "approved":
        raise ValueError("promoted evidence review is not approved")
    if (
        record.candidate_id != candidate.candidate_id
        or record.candidate_record_sha256 != candidate.record_sha256
        or record.review_sha256 != review.content_sha256
        or record.content_ref != candidate.content_ref
        or record.source_locator != candidate.source_url
        or record.source_locator_sha256 != source_locator_sha256(candidate.source_url)
        or record.source_title != candidate.title
        or record.document_date != candidate.document_date
        or record.date_precision != candidate.date_precision
        or record.curator != candidate.curator
        or record.transformation_refs != candidate.transformation_refs
        or record.available_at != review.available_at
        or record.source_type != review.source_type
        or record.license != review.license
    ):
        raise ValueError("promoted evidence cross-lineage mismatch")


class ManualAcquisitionService:
    def __init__(
        self,
        artifact_root: str | Path,
        *,
        session_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ValueError("acquisition session_id is invalid")
        self._root = ArtifactRoot(Path(artifact_root))
        self._blob_store = BlobStore(self._root)
        self._session_id = session_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session_dir = self._root.resolve(f"tracelane://artifacts/acquisition/{session_id}")
        self._manifest_path = self._root.resolve(
            f"tracelane://artifacts/acquisition/{session_id}/manifest.json"
        )
        self._transaction_path = self._session_dir / _TRANSACTION_FILE
        self._lock_path = self._root.path / ".locks" / f"acquisition-{self._session_id}.lock"
        with self._session_lock():
            self._session_dir.mkdir(parents=True, exist_ok=True)
            if self._manifest_path.exists():
                self._reload_session_state()
            else:
                created_at = self._now()
                manifest = {
                    "schema_id": "tracelane://schemas/acquisition-session/v2",
                    "schema_version": "2.0.0",
                    "content_sha256": "",
                    "session_id": session_id,
                    "mode": "codex_manual",
                    "created_at": created_at,
                    "network_access_available_to_agent": False,
                    "candidate_refs": [],
                    "review_refs": [],
                    "promoted_record_refs": [],
                }
                manifest["content_sha256"] = content_digest(manifest)
                validate_document("acquisition-session", manifest)
                _write_json(
                    self._manifest_path,
                    manifest,
                    root=self._root.path,
                    create_new=True,
                )
                self._manifest = manifest

    @property
    def session_dir(self) -> Path:
        return self._session_dir

    @contextmanager
    def _session_lock(self) -> Iterator[None]:
        with exclusive_file_lock(self._lock_path, blocking=True):
            yield

    def _reload_session_state(self) -> None:
        manifest = dict(_read_json_object(self._manifest_path, root=self._root.path))
        self._validate_manifest_envelope(manifest)
        if self._transaction_path.exists():
            manifest = self._recover_transaction(manifest)
        self._validate_session_manifest(manifest)
        self._manifest = manifest

    def _validate_manifest_envelope(self, manifest: Mapping[str, object]) -> None:
        validate_document("acquisition-session", manifest)
        if content_digest(manifest) != str(manifest["content_sha256"]):
            raise ValueError("acquisition session content hash mismatch")
        if manifest["session_id"] != self._session_id:
            raise ValueError("acquisition session identity mismatch")

    def _validate_session_manifest(
        self,
        manifest: Mapping[str, object],
        *,
        pending_documents: (Mapping[str, tuple[ArtifactRef, Mapping[str, object]]] | None) = None,
    ) -> None:
        self._validate_manifest_envelope(manifest)
        pending = pending_documents or {}
        for uri, (reference, value) in pending.items():
            if uri != reference.uri:
                raise ValueError("acquisition transaction artifact identity mismatch")
            expected_bytes = _json_bytes(value)
            if (
                len(expected_bytes) != reference.size_bytes
                or hashlib.sha256(expected_bytes).hexdigest() != reference.sha256
            ):
                raise ValueError("acquisition transaction artifact identity mismatch")
            path = self._root.resolve(uri)
            if path.exists() and (
                secure_read_bytes(
                    path,
                    root=self._root.path,
                    label="acquisition transaction artifact",
                )
                != expected_bytes
            ):
                raise ValueError("acquisition transaction artifact is immutable")
        specs = {
            "candidate_refs": (
                "evidence_candidate",
                "tracelane://schemas/evidence-candidate/v2",
                "candidates",
                EvidenceCandidate.from_dict,
            ),
            "review_refs": (
                "candidate_review",
                "tracelane://schemas/candidate-review/v2",
                "reviews",
                CandidateReview.from_dict,
            ),
            "promoted_record_refs": (
                "evidence_record",
                "tracelane://schemas/evidence-record/v2",
                "promoted",
                EvidenceRecordV2.from_dict,
            ),
        }
        inventory_objects: dict[str, list[tuple[ArtifactRef, object]]] = {}
        for field, (kind, schema_id, folder, parser) in specs.items():
            values = manifest[field]
            references = tuple(
                ArtifactRef.from_dict(item)
                for item in values  # type: ignore[union-attr]
            )
            if len({item.uri for item in references}) != len(references):
                raise ValueError(f"acquisition session {field} contains duplicates")
            inventory_objects[field] = []
            referenced_paths: set[Path] = set()
            for reference in references:
                if reference.kind != kind or reference.schema_id != schema_id:
                    raise ValueError(f"acquisition session {field} metadata is invalid")
                path = self._blob_store.verify(reference)
                referenced_paths.add(path)
                parsed = parser(_read_json_object(path, root=self._root.path))
                inventory_objects[field].append((reference, parsed))
                if isinstance(parsed, (EvidenceCandidate, EvidenceRecordV2)):
                    self._blob_store.verify(parsed.content_ref)
                    for transformation in parsed.transformation_refs:
                        self._blob_store.verify(transformation)
            directory = self._session_dir / folder
            actual_paths = set(directory.glob("*.json")) if directory.exists() else set()
            allowed_pending_paths = {
                self._root.resolve(reference.uri)
                for reference, _value in pending.values()
                if reference.kind == kind and reference.schema_id == schema_id
            }
            if (actual_paths - referenced_paths - allowed_pending_paths) or (
                referenced_paths - actual_paths
            ):
                raise ValueError(f"acquisition session {field} inventory is incomplete")

        candidates: dict[str, tuple[ArtifactRef, EvidenceCandidate]] = {}
        for reference, parsed in inventory_objects["candidate_refs"]:
            if not isinstance(parsed, EvidenceCandidate):
                raise ValueError("acquisition candidate inventory is invalid")
            if reference.uri != _candidate_uri(self._session_id, parsed.candidate_id):
                raise ValueError("acquisition candidate path identity is invalid")
            if parsed.candidate_id in candidates:
                raise ValueError("acquisition candidate identity is duplicated")
            candidates[parsed.candidate_id] = (reference, parsed)

        reviews: dict[str, tuple[ArtifactRef, CandidateReview]] = {}
        for reference, parsed in inventory_objects["review_refs"]:
            if not isinstance(parsed, CandidateReview):
                raise ValueError("acquisition review inventory is invalid")
            if reference.uri != _review_uri(self._session_id, parsed.candidate_id):
                raise ValueError("acquisition review path identity is invalid")
            candidate_entry = candidates.get(parsed.candidate_id)
            if candidate_entry is None:
                raise ValueError("acquisition review candidate lineage is missing")
            _validate_review_candidate_lineage(parsed, candidate_entry[1])
            if parsed.candidate_id in reviews:
                raise ValueError("acquisition review identity is duplicated")
            reviews[parsed.candidate_id] = (reference, parsed)

        for reference, parsed in inventory_objects["promoted_record_refs"]:
            if not isinstance(parsed, EvidenceRecordV2):
                raise ValueError("acquisition promoted inventory is invalid")
            if reference.uri != _promoted_uri(self._session_id, parsed.evidence_id):
                raise ValueError("acquisition promoted path identity is invalid")
            candidate_entry = candidates.get(parsed.candidate_id)
            review_entry = reviews.get(parsed.candidate_id)
            if candidate_entry is None or review_entry is None:
                raise ValueError("promoted evidence lineage is missing")
            candidate_ref, candidate = candidate_entry
            review_ref, review = review_entry
            _validate_promoted_lineage(
                parsed,
                candidate_ref,
                candidate,
                review_ref,
                review,
            )

    def _authenticate_transaction(
        self,
    ) -> tuple[
        dict[str, object],
        dict[str, tuple[ArtifactRef, Mapping[str, object]]],
        dict[str, ArtifactRef],
    ]:
        transaction = dict(_read_json_object(self._transaction_path, root=self._root.path))
        common_fields = {
            "operation",
            "content_sha256",
            "session_id",
            "base_manifest_sha256",
            "candidate_ref",
        }
        operation = transaction.get("operation")
        if operation == "ingest":
            expected_fields = common_fields | {"candidate"}
        elif operation == "promote":
            expected_fields = common_fields | {
                "review_ref",
                "record_ref",
                "review",
                "record",
            }
        else:
            raise ValueError("acquisition transaction operation is invalid")
        if set(transaction) != expected_fields:
            raise ValueError("acquisition transaction shape is invalid")
        if transaction["session_id"] != self._session_id:
            raise ValueError("acquisition transaction session identity mismatch")
        base_digest = transaction["base_manifest_sha256"]
        if not isinstance(base_digest, str) or not _SHA256.fullmatch(base_digest):
            raise ValueError("acquisition transaction base manifest digest is invalid")
        if content_digest(transaction) != transaction["content_sha256"]:
            raise ValueError("acquisition transaction content hash mismatch")

        candidate_ref = ArtifactRef.from_dict(transaction["candidate_ref"])  # type: ignore[arg-type]
        pending_documents: dict[str, tuple[ArtifactRef, Mapping[str, object]]] = {}
        intended_refs = {"candidate_refs": candidate_ref}
        if operation == "ingest":
            candidate_value = transaction["candidate"]
            if not isinstance(candidate_value, Mapping):
                raise ValueError("ingest transaction candidate is invalid")
            candidate = EvidenceCandidate.from_dict(candidate_value)
            expected_candidate_ref = _json_artifact_ref(
                uri=_candidate_uri(self._session_id, candidate.candidate_id),
                kind="evidence_candidate",
                schema_id="tracelane://schemas/evidence-candidate/v2",
                value=candidate_value,
            )
            if candidate_ref != expected_candidate_ref:
                raise ValueError("ingest transaction artifact identity mismatch")
            pending_documents[candidate_ref.uri] = (candidate_ref, candidate_value)
            return transaction, pending_documents, intended_refs

        review_ref = ArtifactRef.from_dict(transaction["review_ref"])  # type: ignore[arg-type]
        record_ref = ArtifactRef.from_dict(transaction["record_ref"])  # type: ignore[arg-type]
        review_value = transaction["review"]
        record_value = transaction["record"]
        if not isinstance(review_value, Mapping) or not isinstance(record_value, Mapping):
            raise ValueError("promotion transaction documents are invalid")
        review = CandidateReview.from_dict(review_value)
        record = EvidenceRecordV2.from_dict(record_value)
        expected_review_ref = _json_artifact_ref(
            uri=_review_uri(self._session_id, review.candidate_id),
            kind="candidate_review",
            schema_id="tracelane://schemas/candidate-review/v2",
            value=review_value,
        )
        expected_record_ref = _json_artifact_ref(
            uri=_promoted_uri(self._session_id, record.evidence_id),
            kind="evidence_record",
            schema_id="tracelane://schemas/evidence-record/v2",
            value=record_value,
        )
        if review_ref != expected_review_ref or record_ref != expected_record_ref:
            raise ValueError("promotion transaction artifact identity mismatch")
        pending_documents[review_ref.uri] = (review_ref, review_value)
        pending_documents[record_ref.uri] = (record_ref, record_value)
        intended_refs["review_refs"] = review_ref
        intended_refs["promoted_record_refs"] = record_ref
        return transaction, pending_documents, intended_refs

    @staticmethod
    def _transaction_is_reflected(
        manifest: Mapping[str, object],
        intended_refs: Mapping[str, ArtifactRef],
    ) -> bool:
        try:
            for field, intended in intended_refs.items():
                references = {
                    ArtifactRef.from_dict(item).uri: ArtifactRef.from_dict(item)
                    for item in manifest[field]  # type: ignore[union-attr]
                }
                if references.get(intended.uri) != intended:
                    return False
        except (KeyError, TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _merge_inventory(
        manifest: Mapping[str, object],
        intended_refs: Mapping[str, ArtifactRef],
    ) -> dict[str, object]:
        merged = json.loads(canonical_json(manifest))
        for field, reference in intended_refs.items():
            values = merged[field]
            if not isinstance(values, list):
                raise ValueError(f"acquisition session {field} is invalid")
            existing = {
                ArtifactRef.from_dict(item).uri: ArtifactRef.from_dict(item) for item in values
            }
            prior = existing.get(reference.uri)
            if prior is not None and prior != reference:
                raise ValueError(f"acquisition session {field} reference is immutable")
            existing[reference.uri] = reference
            merged[field] = [existing[uri].to_dict() for uri in sorted(existing)]
        merged["content_sha256"] = content_digest(merged)
        validate_document("acquisition-session", merged)
        return merged

    def _materialize_transaction_documents(
        self,
        pending_documents: Mapping[str, tuple[ArtifactRef, Mapping[str, object]]],
    ) -> None:
        for reference, value in pending_documents.values():
            path = self._root.resolve(reference.uri)
            expected_bytes = _json_bytes(value)
            if path.exists():
                if (
                    secure_read_bytes(
                        path,
                        root=self._root.path,
                        label="acquisition transaction artifact",
                    )
                    != expected_bytes
                ):
                    raise ValueError("acquisition transaction artifact is immutable")
            else:
                _write_json(
                    path,
                    value,
                    root=self._root.path,
                    create_new=True,
                )

    def _delete_authenticated_transaction(
        self,
        transaction: Mapping[str, object],
    ) -> None:
        retire_authenticated_file(
            self._transaction_path,
            _json_bytes(transaction),
            root=self._root.path,
            label="acquisition transaction journal",
        )

    def _recover_transaction(
        self,
        manifest: dict[str, object],
    ) -> dict[str, object]:
        transaction, pending_documents, intended_refs = self._authenticate_transaction()
        if transaction["base_manifest_sha256"] != manifest["content_sha256"]:
            if not self._transaction_is_reflected(manifest, intended_refs):
                raise ValueError("acquisition transaction base manifest digest mismatch")
            self._validate_session_manifest(manifest)
            self._delete_authenticated_transaction(transaction)
            return manifest

        self._validate_session_manifest(
            manifest,
            pending_documents=pending_documents,
        )
        candidate_ref = intended_refs["candidate_refs"]
        if transaction["operation"] == "ingest":
            candidate_value = pending_documents[candidate_ref.uri][1]
            candidate = EvidenceCandidate.from_dict(candidate_value)
            self._blob_store.verify(candidate.content_ref)
            for transformation in candidate.transformation_refs:
                self._blob_store.verify(transformation)
        else:
            candidate_inventory = {
                ArtifactRef.from_dict(item).uri: ArtifactRef.from_dict(item)
                for item in manifest["candidate_refs"]  # type: ignore[union-attr]
            }
            if candidate_inventory.get(candidate_ref.uri) != candidate_ref:
                raise ValueError("promotion transaction candidate inventory mismatch")
            candidate_path = self._blob_store.verify(candidate_ref)
            candidate = EvidenceCandidate.from_dict(
                _read_json_object(candidate_path, root=self._root.path)
            )
            review_ref = intended_refs["review_refs"]
            record_ref = intended_refs["promoted_record_refs"]
            review = CandidateReview.from_dict(pending_documents[review_ref.uri][1])
            record = EvidenceRecordV2.from_dict(pending_documents[record_ref.uri][1])
            _validate_promoted_lineage(
                record,
                candidate_ref,
                candidate,
                review_ref,
                review,
            )

        self._materialize_transaction_documents(pending_documents)
        merged = self._merge_inventory(manifest, intended_refs)
        self._validate_session_manifest(merged)
        _write_json(self._manifest_path, merged, root=self._root.path)
        published = dict(_read_json_object(self._manifest_path, root=self._root.path))
        if published != merged:
            raise ValueError("acquisition session manifest changed during publication")
        self._validate_session_manifest(published)
        self._delete_authenticated_transaction(transaction)
        return published

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("acquisition clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def candidate_path(self, candidate_id: str) -> Path:
        return self._root.resolve(_candidate_uri(self._session_id, candidate_id))

    def _write_or_load_candidate(
        self,
        candidate: EvidenceCandidate,
    ) -> EvidenceCandidate:
        path = self.candidate_path(candidate.candidate_id)
        if path.exists():
            existing = EvidenceCandidate.from_dict(_read_json_object(path, root=self._root.path))
            if existing.to_dict() != candidate.to_dict():
                raise ValueError("candidate identity collision")
            self._blob_store.verify(existing.content_ref)
            for transformation in existing.transformation_refs:
                self._blob_store.verify(transformation)
            candidate = existing
        candidate_value = candidate.to_dict()
        candidate_ref = _json_artifact_ref(
            uri=_candidate_uri(self._session_id, candidate.candidate_id),
            kind="evidence_candidate",
            schema_id="tracelane://schemas/evidence-candidate/v2",
            value=candidate_value,
        )
        transaction: dict[str, object] = {
            "operation": "ingest",
            "session_id": self._session_id,
            "base_manifest_sha256": self._manifest["content_sha256"],
            "candidate_ref": candidate_ref.to_dict(),
            "candidate": candidate_value,
            "content_sha256": "",
        }
        transaction["content_sha256"] = content_digest(transaction)
        _write_json(
            self._transaction_path,
            transaction,
            root=self._root.path,
            create_new=True,
        )
        self._manifest = self._recover_transaction(dict(self._manifest))
        return candidate

    def ingest(
        self,
        *,
        query: str,
        title: str,
        source_url: str,
        document_date: str,
        date_precision: str,
        curated_text: str,
        curator: str,
        transformation_refs: Sequence[ArtifactRef] = (),
        secrets: Sequence[str] = (),
    ) -> EvidenceCandidate:
        with self._session_lock():
            self._reload_session_state()
            return self._ingest_locked(
                query=query,
                title=title,
                source_url=source_url,
                document_date=document_date,
                date_precision=date_precision,
                curated_text=curated_text,
                curator=curator,
                transformation_refs=transformation_refs,
                secrets=secrets,
            )

    def _ingest_locked(
        self,
        *,
        query: str,
        title: str,
        source_url: str,
        document_date: str,
        date_precision: str,
        curated_text: str,
        curator: str,
        transformation_refs: Sequence[ArtifactRef] = (),
        secrets: Sequence[str] = (),
    ) -> EvidenceCandidate:
        query = _non_empty(query, "acquisition query")
        title = _non_empty(title, "candidate title")
        source_url = canonical_source_url(source_url, secrets=secrets)
        metadata = classify_and_redact(
            {"query": query, "title": title, "curator": _non_empty(curator, "candidate curator")},
            secrets=secrets,
        )
        if not isinstance(metadata.value, Mapping):
            raise ValueError("redacted candidate metadata must remain an object")
        query = str(metadata.value["query"])
        title = str(metadata.value["title"])
        curator = str(metadata.value["curator"])
        source_check = classify_and_redact(source_url, secrets=secrets)
        if source_check.redaction_applied:
            raise ValueError("source URL contains sensitive data")
        try:
            validate_document_date(document_date, date_precision)
        except ValueError as exc:
            raise ValueError(f"candidate {exc}") from exc
        redacted = classify_and_redact(
            _non_empty(curated_text, "curated text"),
            secrets=secrets,
        )
        if not isinstance(redacted.value, str):
            raise ValueError("redacted curated text must remain text")
        body = redacted.value.encode("utf-8")
        if len(body) > _MAX_CONTENT_BYTES:
            raise ValueError("candidate body size is invalid")
        transformations = tuple(transformation_refs)
        if len({item.uri for item in transformations}) != len(transformations):
            raise ValueError("candidate transformation_refs must be unique")
        for transformation in transformations:
            validate_transformation_ref(
                transformation,
                label="candidate transformation reference",
            )
            self._blob_store.verify(transformation)
        content_ref = self._blob_store.put_bytes(
            body,
            "text/plain",
            "evidence_blob",
        )
        candidate_id = compute_candidate_id(
            query=query,
            title=title,
            source_url=source_url,
            document_date=document_date,
            date_precision=date_precision,
            content_sha256=content_ref.sha256,
        )
        candidate = EvidenceCandidate.create(
            candidate_id=candidate_id,
            query=query,
            title=title,
            source_url=source_url,
            document_date=document_date,
            date_precision=date_precision,  # type: ignore[arg-type]
            retrieved_at=self._now(),
            curator=curator,
            transformation_refs=transformations,
            content_ref=content_ref,
        )
        return self._write_or_load_candidate(candidate)

    def promote(
        self,
        candidate_id: str,
        review: CandidateReview,
        *,
        evidence_id: str,
        known_by_cutoff: str,
        excerpt_kind: str,
        fact_ids: Sequence[str],
        secrets: Sequence[str] = (),
    ) -> ArtifactRef:
        with self._session_lock():
            try:
                self._reload_session_state()
            except ValueError as exc:
                message = str(exc)
                candidate_suffix = f"/candidates/{candidate_id}.json"
                if (
                    isinstance(candidate_id, str)
                    and _CANDIDATE_ID.fullmatch(candidate_id)
                    and candidate_suffix in message
                    and ("size mismatch" in message or "hash mismatch" in message)
                ):
                    raise ValueError("review does not match candidate record") from exc
                raise
            return self._promote_locked(
                candidate_id,
                review,
                evidence_id=evidence_id,
                known_by_cutoff=known_by_cutoff,
                excerpt_kind=excerpt_kind,
                fact_ids=fact_ids,
                secrets=secrets,
            )

    def _promote_locked(
        self,
        candidate_id: str,
        review: CandidateReview,
        *,
        evidence_id: str,
        known_by_cutoff: str,
        excerpt_kind: str,
        fact_ids: Sequence[str],
        secrets: Sequence[str] = (),
    ) -> ArtifactRef:
        candidate_id = _candidate_id(candidate_id)
        review = CandidateReview.from_dict(review.to_dict())
        if review.candidate_id != candidate_id:
            raise ValueError("review candidate identity does not match")
        if review.decision != "approved":
            raise ValueError("candidate must be approved before promotion")
        candidate_path = self.candidate_path(candidate_id)
        try:
            candidate = EvidenceCandidate.from_dict(
                _read_json_object(candidate_path, root=self._root.path)
            )
        except ValueError as exc:
            if "unavailable" in str(exc):
                raise ValueError("candidate is unavailable") from exc
            raise
        self._blob_store.verify(candidate.content_ref)
        for transformation in candidate.transformation_refs:
            self._blob_store.verify(transformation)
        _validate_review_candidate_lineage(review, candidate)
        persisted_review = _review_for_persistence(
            review,
            candidate,
            secrets=secrets,
        )
        review_value = persisted_review.to_dict()
        candidate_ref = artifact_ref_for_file(
            self._root.path,
            candidate_path,
            "evidence_candidate",
            "tracelane://schemas/evidence-candidate/v2",
        )
        review_path = self._root.resolve(_review_uri(self._session_id, candidate_id))
        if review_path.exists():
            existing = CandidateReview.from_dict(
                _read_json_object(review_path, root=self._root.path)
            )
            if existing.to_dict() != review_value:
                raise ValueError("candidate review is immutable")
        review_ref = _json_artifact_ref(
            uri=_review_uri(self._session_id, candidate_id),
            kind="candidate_review",
            schema_id="tracelane://schemas/candidate-review/v2",
            value=review_value,
        )
        record_value: dict[str, object] = {
            "schema_id": "tracelane://schemas/evidence-record/v2",
            "schema_version": "2.0.0",
            "evidence_id": evidence_id,
            "document_date": candidate.document_date,
            "date_precision": candidate.date_precision,
            "available_at": persisted_review.available_at,
            "known_by_cutoff": known_by_cutoff,
            "source_type": persisted_review.source_type,
            "source_title": candidate.title,
            "source_locator": candidate.source_url,
            "source_locator_sha256": source_locator_sha256(candidate.source_url),
            "curator": candidate.curator,
            "candidate_id": candidate.candidate_id,
            "candidate_record_sha256": candidate.record_sha256,
            "review_sha256": persisted_review.content_sha256,
            "candidate_ref": candidate_ref.to_dict(),
            "review_ref": review_ref.to_dict(),
            "license": persisted_review.license,
            "excerpt_kind": excerpt_kind,
            "content_ref": candidate.content_ref.to_dict(),
            "fact_ids": list(fact_ids),
            "transformation_refs": [item.to_dict() for item in candidate.transformation_refs],
        }
        record_value["provenance_sha256"] = compute_evidence_provenance_sha256(record_value)
        record = EvidenceRecordV2.from_dict(record_value)
        record_path = self._root.resolve(_promoted_uri(self._session_id, evidence_id))
        if record_path.exists():
            existing_record = EvidenceRecordV2.from_dict(
                _read_json_object(record_path, root=self._root.path)
            )
            if existing_record.to_dict() != record.to_dict():
                raise ValueError("promoted evidence record is immutable")
        record_ref = _json_artifact_ref(
            uri=_promoted_uri(self._session_id, evidence_id),
            kind="evidence_record",
            schema_id="tracelane://schemas/evidence-record/v2",
            value=record.to_dict(),
        )
        transaction: dict[str, object] = {
            "operation": "promote",
            "content_sha256": "",
            "session_id": self._session_id,
            "base_manifest_sha256": self._manifest["content_sha256"],
            "candidate_ref": candidate_ref.to_dict(),
            "review_ref": review_ref.to_dict(),
            "record_ref": record_ref.to_dict(),
            "review": review_value,
            "record": record.to_dict(),
        }
        transaction["content_sha256"] = content_digest(transaction)
        _write_json(
            self._transaction_path,
            transaction,
            root=self._root.path,
            create_new=True,
        )
        self._manifest = self._recover_transaction(dict(self._manifest))
        return record_ref
