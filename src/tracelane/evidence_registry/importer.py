from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from tracelane.acquisition import (
    AcquisitionCandidateClosure,
    ManualAcquisitionService,
)
from tracelane.contracts import canonical_json
from tracelane.evidence_registry.contracts import (
    EvidenceImportMetadata,
    EvidenceImportRow,
    EvidenceProject,
    ProjectEvidenceCandidate,
)
from tracelane.evidence_registry.index import (
    EvidenceProjectIndex,
    EvidenceRegistry,
    build_project_index,
    build_registry,
    rebuild_project_index,
    rebuild_registry,
    verify_evidence_registry,
)
from tracelane.evidence_registry.storage import (
    EvidenceBlobStore,
    EvidenceRoot,
    write_json_create_or_match,
)
from tracelane.security import classify_and_redact
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.locking import exclusive_file_lock
from tracelane.v2.schema import validate_document
from tracelane.v2.storage import (
    ArtifactRoot,
    atomic_write_bytes,
    secure_read_bytes,
)

_PROJECT_SCHEMA = "tracelane://schemas/evidence-project/v1"
_CANDIDATE_SCHEMA = "tracelane://schemas/project-evidence-candidate/v1"
_REGISTRY_SCHEMA = "tracelane://schemas/evidence-registry/v1"
_PUBLIC_IMPORT_ERRORS = frozenset(
    {
        "acquisition import metadata is invalid",
        "acquisition import metadata manifest does not match source",
        "acquisition import source and target are invalid",
        "acquisition import source and target overlap",
        "acquisition import source changed",
        "acquisition import source content is invalid",
        "acquisition import source content is restricted",
        "acquisition import source is invalid",
        "acquisition import source is unavailable",
        "acquisition transformations are not importable",
        "evidence import staging cleanup failed",
        "evidence import staging identity is invalid",
        "evidence import target changed",
        "evidence import target conflicts",
        "evidence import target conflicts with existing project",
        "evidence import target is invalid",
    }
)


@dataclass(frozen=True)
class EvidenceImportReport:
    project_id: str
    candidate_count: int
    pending_count: int
    future_control_count: int
    source_manifest_sha256: str
    project_index_sha256: str
    registry_sha256: str
    source_candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class _AuthenticatedImport:
    manifest_sha256: str
    closures: tuple[AcquisitionCandidateClosure, ...]
    rows: tuple[EvidenceImportRow, ...]


def _canonical_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8") + b"\n"


def _canonical_absolute_path(value: str | Path) -> Path:
    try:
        return Path(value).resolve(strict=False)
    except (OSError, RuntimeError, TypeError, ValueError):
        raise ValueError("acquisition import source and target are invalid") from None


def _paths_overlap(first: Path, second: Path) -> bool:
    first_key = os.path.normcase(os.path.normpath(str(first)))
    second_key = os.path.normcase(os.path.normpath(str(second)))
    try:
        shared = os.path.commonpath((first_key, second_key))
    except ValueError:
        return False
    return shared in {first_key, second_key}


def _source_manifest_sha256(
    source_root: str | Path,
    session_id: str,
) -> str:
    supplied = Path(source_root)
    manifest_relative = Path("acquisition") / session_id / "manifest.json"
    if not supplied.is_dir() or not (supplied / manifest_relative).is_file():
        raise ValueError("acquisition import source is unavailable")
    try:
        root = ArtifactRoot(supplied)
        manifest_path = root.resolve(
            f"tracelane://artifacts/acquisition/{session_id}/manifest.json"
        )
        data = secure_read_bytes(
            manifest_path,
            root=root.path,
            label="acquisition import manifest",
        )
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("manifest must be an object")
        validate_document("acquisition-session", value)
        if value["session_id"] != session_id:
            raise ValueError("manifest session identity is invalid")
        if content_digest(value) != value["content_sha256"]:
            raise ValueError("manifest digest is invalid")
        if data != _canonical_bytes(value):
            raise ValueError("manifest serialization is invalid")
        return str(value["content_sha256"])
    except (OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("acquisition import source is invalid") from exc


def _validated_inputs(
    project: EvidenceProject,
    metadata: EvidenceImportMetadata,
) -> tuple[EvidenceProject, EvidenceImportMetadata]:
    try:
        validated_project = EvidenceProject.from_dict(project.to_dict())
        validated_metadata = EvidenceImportMetadata.from_dict(metadata.to_dict())
    except (TypeError, ValueError) as exc:
        raise ValueError("acquisition import metadata is invalid") from exc
    if validated_project.project_id != validated_metadata.project_id:
        raise ValueError("acquisition import metadata is invalid")
    return validated_project, validated_metadata


def _validate_snapshot(
    closures: tuple[AcquisitionCandidateClosure, ...],
    metadata: EvidenceImportMetadata,
) -> _AuthenticatedImport:
    rows = {item.candidate_id: item for item in metadata.candidates}
    candidates = {item.candidate.candidate_id: item for item in closures}
    if len(candidates) != len(closures) or set(candidates) != set(rows):
        raise ValueError("acquisition import metadata candidates do not match source")
    for candidate_id, closure in candidates.items():
        candidate = closure.candidate
        row = rows[candidate_id]
        if (
            closure.candidate_ref.kind != "evidence_candidate"
            or closure.candidate_ref.schema_id != "tracelane://schemas/evidence-candidate/v2"
        ):
            raise ValueError("acquisition import candidate reference is invalid")
        if (
            closure.candidate_bytes != _canonical_bytes(candidate.to_dict())
            or hashlib.sha256(closure.candidate_bytes).hexdigest() != closure.candidate_ref.sha256
            or len(closure.candidate_bytes) != closure.candidate_ref.size_bytes
            or hashlib.sha256(closure.content_bytes).hexdigest() != candidate.content_sha256
            or len(closure.content_bytes) != candidate.content_ref.size_bytes
            or row.candidate_record_sha256 != candidate.record_sha256
            or row.candidate_content_sha256 != candidate.content_sha256
        ):
            raise ValueError("acquisition import metadata candidate identity mismatch")
        try:
            content_text = closure.content_bytes.decode("utf-8")
            content_check = classify_and_redact(content_text)
        except (TypeError, UnicodeDecodeError, ValueError) as exc:
            raise ValueError("acquisition import source content is invalid") from exc
        if (
            content_check.redaction_applied
            or not isinstance(content_check.value, str)
            or content_check.value != content_text
        ):
            raise ValueError("acquisition import source content is restricted")
        if candidate.transformation_refs or closure.transformations:
            raise ValueError("acquisition transformations are not importable")
    return _AuthenticatedImport(
        manifest_sha256=metadata.manifest_sha256,
        closures=tuple(sorted(closures, key=lambda item: item.candidate.candidate_id)),
        rows=tuple(metadata.candidates),
    )


def _authenticate_source(
    source_root: str | Path,
    metadata: EvidenceImportMetadata,
) -> tuple[ManualAcquisitionService, _AuthenticatedImport]:
    initial_manifest = _source_manifest_sha256(source_root, metadata.session_id)
    if initial_manifest != metadata.manifest_sha256:
        raise ValueError("acquisition import metadata manifest does not match source")
    try:
        service = ManualAcquisitionService(
            source_root,
            session_id=metadata.session_id,
        )
        closures = service.snapshot_candidates()
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("acquisition import source is invalid") from exc
    final_manifest = _source_manifest_sha256(source_root, metadata.session_id)
    if final_manifest != initial_manifest:
        raise ValueError("acquisition import source changed")
    try:
        authenticated = _validate_snapshot(closures, metadata)
    except ValueError as exc:
        if str(exc) in {
            "acquisition import source content is invalid",
            "acquisition import source content is restricted",
            "acquisition transformations are not importable",
        }:
            raise
        raise ValueError("acquisition import metadata is invalid") from exc
    return service, authenticated


def _assert_source_unchanged(
    service: ManualAcquisitionService,
    source_path: Path,
    authenticated: _AuthenticatedImport,
    session_id: str,
) -> None:
    try:
        closures = tuple(
            sorted(
                service.snapshot_candidates(),
                key=lambda item: item.candidate.candidate_id,
            )
        )
        manifest_sha256 = _source_manifest_sha256(source_path, session_id)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("acquisition import source changed") from exc
    if closures != authenticated.closures or manifest_sha256 != authenticated.manifest_sha256:
        raise ValueError("acquisition import source changed")


def _candidate_from_source(
    project_id: str,
    closure: AcquisitionCandidateClosure,
    row: EvidenceImportRow,
    content_ref: ArtifactRef,
    session_id: str,
) -> ProjectEvidenceCandidate:
    candidate = closure.candidate
    return ProjectEvidenceCandidate.create(
        project_id=project_id,
        candidate_id=candidate.candidate_id,
        source_spec_id=row.source_spec_id,
        query=candidate.query,
        title=candidate.title,
        source_url=candidate.source_url,
        document_date=candidate.document_date,
        date_precision=candidate.date_precision,
        retrieved_at=candidate.retrieved_at,
        curator=candidate.curator,
        source_type=row.source_type,
        role=row.role,
        domains=row.domains,
        fact_ids=row.fact_ids,
        content_ref=content_ref,
        transformation_refs=(),
        content_authorship=row.content_authorship,
        retention_policy=row.retention_policy,
        license_basis=row.license_basis,
        acquisition_session_id=session_id,
        source_candidate_uri=closure.candidate_ref.uri,
        source_candidate_id=candidate.candidate_id,
        source_candidate_record_sha256=candidate.record_sha256,
        source_candidate_content_sha256=candidate.content_sha256,
    )


def _build_staged_project(
    stage_path: Path,
    project: EvidenceProject,
    authenticated: _AuthenticatedImport,
    session_id: str,
) -> tuple[EvidenceRoot, EvidenceProjectIndex]:
    try:
        stage = EvidenceRoot.create(stage_path)
        blob_store = EvidenceBlobStore(stage)
        write_json_create_or_match(
            stage,
            f"tracelane://evidence/projects/{project.project_id}/project.json",
            "evidence_project",
            _PROJECT_SCHEMA,
            project.to_dict(),
        )
        rows = {item.candidate_id: item for item in authenticated.rows}
        for closure in authenticated.closures:
            content_ref = blob_store.put_bytes(
                closure.content_bytes,
                closure.candidate.content_ref.media_type,
                "evidence_blob",
            )
            candidate = _candidate_from_source(
                project.project_id,
                closure,
                rows[closure.candidate.candidate_id],
                content_ref,
                session_id,
            )
            write_json_create_or_match(
                stage,
                (
                    f"tracelane://evidence/projects/{project.project_id}/"
                    f"candidates/{candidate.candidate_id}.json"
                ),
                "evidence_candidate",
                _CANDIDATE_SCHEMA,
                candidate.to_dict(),
            )
        index_ref = rebuild_project_index(stage, project.project_id)
        rebuild_registry(stage)
        report = verify_evidence_registry(stage, project.project_id)
        if report.project_index_sha256 != index_ref.sha256:
            raise ValueError("staged index identity is invalid")
        return stage, build_project_index(stage, project.project_id)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("acquisition import metadata is invalid") from exc


def _target_identity(target: Path) -> str:
    return hashlib.sha256(
        os.path.normcase(os.path.normpath(str(target))).encode("utf-8")
    ).hexdigest()[:24]


def _staging_location(target: Path) -> tuple[Path, Path]:
    namespace = ArtifactRoot(target.parent / ".tracelane-staging").path
    prefix = f"{target.name}-{_target_identity(target)}-"
    return namespace, namespace / f"{prefix}{uuid.uuid4().hex}"


def _safe_remove_stage(
    stage_path: Path,
    staging_namespace: Path,
    target: Path,
) -> None:
    expected_namespace = target.parent / ".tracelane-staging"
    prefix = f"{target.name}-{_target_identity(target)}-"
    suffix = stage_path.name.removeprefix(prefix)
    if (
        staging_namespace != expected_namespace
        or stage_path.parent != staging_namespace
        or not stage_path.name.startswith(prefix)
        or re.fullmatch(r"[0-9a-f]{32}", suffix) is None
        or stage_path.is_symlink()
    ):
        raise ValueError("evidence import staging identity is invalid")
    if stage_path.exists():
        try:
            shutil.rmtree(stage_path)
        except OSError:
            raise ValueError("evidence import staging cleanup failed") from None


def _authenticate_existing_project(
    root: EvidenceRoot,
    project: EvidenceProject,
    expected_index: EvidenceProjectIndex,
) -> None:
    try:
        project_path = root.resolve(
            f"tracelane://evidence/projects/{project.project_id}/project.json",
            must_exist=True,
        )
        index_path = root.resolve(
            f"tracelane://evidence/projects/{project.project_id}/index.json",
            must_exist=True,
        )
        project_bytes = secure_read_bytes(
            project_path,
            root=root.path,
            label="evidence import existing project",
        )
        index_bytes = secure_read_bytes(
            index_path,
            root=root.path,
            label="evidence import existing index",
        )
        derived_index = build_project_index(root, project.project_id)
        if (
            project_bytes != _canonical_bytes(project.to_dict())
            or derived_index != expected_index
            or index_bytes != _canonical_bytes(expected_index.to_dict())
        ):
            raise ValueError("existing project differs")
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("evidence import target conflicts with existing project") from exc


def _validate_existing_registry(
    root: EvidenceRoot,
    expected: EvidenceRegistry,
) -> None:
    registry_path = root.resolve("tracelane://evidence/registry.json")
    if not registry_path.exists():
        return
    data = secure_read_bytes(
        registry_path,
        root=root.path,
        label="evidence import existing registry",
    )
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("existing registry must be an object")
    parsed = EvidenceRegistry.from_dict(value)
    if data != _canonical_bytes(parsed.to_dict()):
        raise ValueError("existing registry is not canonical")
    expected_entries = {item.project_id: item for item in expected.projects}
    if any(expected_entries.get(item.project_id) != item for item in parsed.projects):
        raise ValueError("existing registry is not a recoverable stale registry")


def _preflight_existing_target(
    target_path: Path,
    project: EvidenceProject,
    expected_index: EvidenceProjectIndex,
) -> tuple[EvidenceRoot | None, bool]:
    try:
        target_path.lstat()
    except FileNotFoundError:
        return None, False
    except OSError:
        raise ValueError("evidence import target is invalid") from None
    try:
        root = EvidenceRoot.open(target_path)
        expected_registry = build_registry(root)
        _validate_existing_registry(root, expected_registry)
    except (OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError("evidence import target is invalid") from None
    final_project = root.path / "projects" / project.project_id
    if final_project.exists():
        _authenticate_existing_project(root, project, expected_index)
        return root, True
    return root, False


def _publish_registry(root: EvidenceRoot) -> ArtifactRef:
    expected = build_registry(root)
    expected_bytes = _canonical_bytes(expected.to_dict())
    registry_path = root.resolve("tracelane://evidence/registry.json")
    if not registry_path.exists():
        return write_json_create_or_match(
            root,
            "tracelane://evidence/registry.json",
            "evidence_registry",
            _REGISTRY_SCHEMA,
            expected.to_dict(),
        )
    try:
        current = secure_read_bytes(
            registry_path,
            root=root.path,
            label="evidence import registry",
        )
        value = json.loads(current.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("registry must be an object")
        parsed = EvidenceRegistry.from_dict(value)
        if current != _canonical_bytes(parsed.to_dict()):
            raise ValueError("registry is not canonical")
        if current != expected_bytes:
            if (
                secure_read_bytes(
                    registry_path,
                    root=root.path,
                    label="evidence import registry",
                )
                != current
            ):
                raise ValueError("registry changed before repair")
            atomic_write_bytes(
                registry_path,
                expected_bytes,
                root=root.path,
                label="evidence import registry",
            )
        published = secure_read_bytes(
            registry_path,
            root=root.path,
            label="evidence import registry",
        )
        published_value = json.loads(published.decode("utf-8"))
        if (
            not isinstance(published_value, dict)
            or EvidenceRegistry.from_dict(published_value) != expected
            or published != expected_bytes
            or build_registry(root) != expected
        ):
            raise ValueError("registry publication is invalid")
        return ArtifactRef.from_dict(
            {
                "kind": "evidence_registry",
                "uri": "tracelane://evidence/registry.json",
                "media_type": "application/json",
                "schema_id": _REGISTRY_SCHEMA,
                "sha256": hashlib.sha256(published).hexdigest(),
                "size_bytes": len(published),
            }
        )
    except (OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("evidence import target is invalid") from exc


def _target_lock_path(target: Path) -> Path:
    lock_root = ArtifactRoot(target.parent / ".tracelane-locks")
    return lock_root.path / ".locks" / f"evidence-import-{_target_identity(target)}.lock"


def _import_acquisition_project(
    source_root: str | Path,
    target_root: str | Path,
    project: EvidenceProject,
    metadata: EvidenceImportMetadata,
) -> EvidenceImportReport:
    project, metadata = _validated_inputs(project, metadata)
    source_path = _canonical_absolute_path(source_root)
    target_path = _canonical_absolute_path(target_root)
    if _paths_overlap(source_path, target_path):
        raise ValueError("acquisition import source and target overlap")
    service, authenticated = _authenticate_source(source_path, metadata)
    with exclusive_file_lock(_target_lock_path(target_path), blocking=True):
        staging_namespace, stage_path = _staging_location(target_path)
        try:
            stage, expected_index = _build_staged_project(
                stage_path,
                project,
                authenticated,
                metadata.session_id,
            )
            _assert_source_unchanged(
                service,
                source_path,
                authenticated,
                metadata.session_id,
            )

            target, project_exists = _preflight_existing_target(
                target_path,
                project,
                expected_index,
            )
            try:
                target = target or EvidenceRoot.create(target_path)
                target_blobs = EvidenceBlobStore(target)
                for closure in authenticated.closures:
                    target_blobs.put_bytes(
                        closure.content_bytes,
                        closure.candidate.content_ref.media_type,
                        "evidence_blob",
                    )
            except (OSError, TypeError, ValueError) as exc:
                raise ValueError("evidence import target conflicts") from exc

            final_project = target.path / "projects" / project.project_id
            target.ensure_parent(final_project)
            if project_exists:
                _authenticate_existing_project(
                    target,
                    project,
                    expected_index,
                )
            else:
                try:
                    os.rename(
                        stage.path / "projects" / project.project_id,
                        final_project,
                    )
                except OSError:
                    raise ValueError("evidence import target changed") from None
                _authenticate_existing_project(
                    target,
                    project,
                    expected_index,
                )
            _assert_source_unchanged(
                service,
                source_path,
                authenticated,
                metadata.session_id,
            )
            _publish_registry(target)
            _assert_source_unchanged(
                service,
                source_path,
                authenticated,
                metadata.session_id,
            )
            verified = verify_evidence_registry(target, project.project_id)
            if verified.project_index_sha256 is None:
                raise ValueError("evidence import target is invalid")
            _assert_source_unchanged(
                service,
                source_path,
                authenticated,
                metadata.session_id,
            )
            return EvidenceImportReport(
                project_id=project.project_id,
                candidate_count=len(authenticated.closures),
                pending_count=expected_index.status_counts["pending"],
                future_control_count=sum(
                    item.role == "future-control" for item in expected_index.entries
                ),
                source_manifest_sha256=authenticated.manifest_sha256,
                project_index_sha256=verified.project_index_sha256,
                registry_sha256=verified.registry_sha256,
                source_candidate_ids=tuple(
                    item.candidate.candidate_id for item in authenticated.closures
                ),
            )
        finally:
            if stage_path.exists() or stage_path.is_symlink():
                _safe_remove_stage(
                    stage_path,
                    staging_namespace,
                    target_path,
                )


def import_acquisition_project(
    source_root: str | Path,
    target_root: str | Path,
    project: EvidenceProject,
    metadata: EvidenceImportMetadata,
) -> EvidenceImportReport:
    try:
        return _import_acquisition_project(
            source_root,
            target_root,
            project,
            metadata,
        )
    except ValueError as exc:
        message = str(exc)
        if message not in _PUBLIC_IMPORT_ERRORS:
            message = "evidence import failed"
        raise ValueError(message) from None
    except (OSError, TypeError):
        raise ValueError("evidence import failed") from None
