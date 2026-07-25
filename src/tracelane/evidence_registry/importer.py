from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import uuid
from contextlib import suppress
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
    rebuild_project_index,
    rebuild_registry,
)
from tracelane.evidence_registry.index import (
    _build_project_index as build_project_index,
)
from tracelane.evidence_registry.index import (
    _build_registry as build_registry,
)
from tracelane.evidence_registry.index import (
    _verify_evidence_registry as verify_evidence_registry,
)
from tracelane.evidence_registry.storage import (
    EvidenceBlobStore,
    EvidenceRoot,
    JsonReplacementReceipt,
    commit_json_replacement,
    evidence_root_mutation_lock,
    replace_json_publication,
    rollback_json_replacement,
    write_json_create_or_match,
)
from tracelane.security import assert_safe_tree, classify_and_redact
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.schema import validate_document
from tracelane.v2.storage import ArtifactRoot, secure_read_bytes

_PROJECT_SCHEMA = "tracelane://schemas/evidence-project/v1"
_CANDIDATE_SCHEMA = "tracelane://schemas/project-evidence-candidate/v1"
_REGISTRY_SCHEMA = "tracelane://schemas/evidence-registry/v1"
_CONTROL_ROOT_NAMES = (".tracelane-staging", ".tracelane-locks")
_IMPORT_PLATFORM = os.name
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
        "evidence import is unavailable on this platform",
        "evidence import quarantine requires maintenance",
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


@dataclass(frozen=True)
class _ProjectDirectoryPublicationReceipt:
    target: Path
    directory_identity: tuple[int, int]
    tree_snapshot: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class _DirectoryOwnershipReceipt:
    path: Path
    directory_identity: tuple[int, int]


@dataclass(frozen=True)
class _RetirementDirectoryReceipt:
    path: Path
    directory_identity: tuple[int, int]
    authenticated_identity: tuple[int, int]
    evidence_root: Path
    handle: int


class _DirectoryRetirementMaintenanceError(ValueError):
    pass


class _ProjectQuarantineMaintenanceError(ValueError):
    def __init__(self) -> None:
        super().__init__("evidence import quarantine requires maintenance")


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


def _target_overlaps_control_roots(target: Path) -> bool:
    for ancestor in (target.parent, *target.parent.parents):
        for name in _CONTROL_ROOT_NAMES:
            if _paths_overlap(target, ancestor / name):
                return True
    return False


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


def _staging_location(target: Path) -> tuple[Path, Path]:
    namespace = ArtifactRoot(target.parent / ".tracelane-staging").path
    prefix = _staging_prefix(target)
    return namespace, namespace / f"{prefix}{uuid.uuid4().hex}"


def _staging_prefix(target: Path) -> str:
    stable_name = hashlib.sha256(
        os.path.normcase(os.path.normpath(str(target))).encode("utf-8")
    ).hexdigest()[:24]
    return f"{target.name}-{stable_name}-"


def _safe_remove_stage(
    stage_path: Path,
    staging_namespace: Path,
    target: Path,
    receipt: _DirectoryOwnershipReceipt,
    retirement_receipt: _RetirementDirectoryReceipt,
) -> None:
    expected_namespace = target.parent / ".tracelane-staging"
    prefix = _staging_prefix(target)
    suffix = stage_path.name.removeprefix(prefix)
    if (
        staging_namespace != expected_namespace
        or stage_path.parent != staging_namespace
        or not stage_path.name.startswith(prefix)
        or re.fullmatch(r"[0-9a-f]{32}", suffix) is None
        or stage_path.is_symlink()
    ):
        raise ValueError("evidence import staging identity is invalid")
    if receipt.path != stage_path:
        raise ValueError("evidence import staging identity is invalid")
    try:
        _retire_owned_directory(
            receipt,
            retirement_receipt,
            prefix="stage",
        )
    except _DirectoryRetirementMaintenanceError as exc:
        raise _ProjectQuarantineMaintenanceError from exc
    except (OSError, TypeError, ValueError):
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


def _publish_registry(root: EvidenceRoot) -> JsonReplacementReceipt:
    expected = build_registry(root)
    expected_bytes = _canonical_bytes(expected.to_dict())
    registry_path = root.resolve("tracelane://evidence/registry.json")
    receipt = None
    try:
        receipt = replace_json_publication(
            root,
            "tracelane://evidence/registry.json",
            "evidence_registry",
            _REGISTRY_SCHEMA,
            expected.to_dict(),
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
    except (OSError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if receipt is not None:
            try:
                rollback_json_replacement(root, receipt)
            except (OSError, TypeError, ValueError):
                raise ValueError("evidence import target is invalid") from None
        raise ValueError("evidence import target is invalid") from exc
    return receipt


def _project_directory_snapshot(path: Path) -> tuple[tuple[object, ...], ...]:
    path = Path(path)
    try:
        assert_safe_tree(path)
        entries: list[tuple[object, ...]] = []
        for current in (path, *sorted(path.rglob("*"))):
            metadata = current.lstat()
            relative = current.relative_to(path).as_posix() or "."
            if stat.S_ISDIR(metadata.st_mode):
                entries.append(
                    (
                        relative,
                        "directory",
                        metadata.st_dev,
                        metadata.st_ino,
                    )
                )
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise ValueError("project publication tree is invalid")
            data = secure_read_bytes(
                current,
                root=path,
                label="evidence import project publication",
            )
            entries.append(
                (
                    relative,
                    "file",
                    metadata.st_dev,
                    metadata.st_ino,
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                )
            )
        return tuple(entries)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("project publication tree is invalid") from exc


def _directory_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = Path(path).lstat()
    except OSError as exc:
        raise ValueError("owned directory is unavailable") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
        or not stat.S_ISDIR(metadata.st_mode)
    ):
        raise ValueError("owned directory identity is invalid")
    return metadata.st_dev, metadata.st_ino


def _entry_exists_no_follow(path: Path) -> bool:
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("owned directory live path is unavailable") from exc
    return True


def _windows_directory_handle_identity(handle: int) -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes

    class _HandleInformation(ctypes.Structure):
        _fields_ = [
            ("file_attributes", wintypes.DWORD),
            ("creation_time", wintypes.FILETIME),
            ("last_access_time", wintypes.FILETIME),
            ("last_write_time", wintypes.FILETIME),
            ("volume_serial_number", wintypes.DWORD),
            ("file_size_high", wintypes.DWORD),
            ("file_size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_HandleInformation),
    ]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    information = _HandleInformation()
    if not kernel32.GetFileInformationByHandle(
        wintypes.HANDLE(handle),
        ctypes.byref(information),
    ):
        raise ValueError("retirement directory handle is unavailable")
    if not information.file_attributes & 0x00000010 or information.file_attributes & 0x00000400:
        raise ValueError("retirement directory handle is invalid")
    file_index = (information.file_index_high << 32) | information.file_index_low
    return information.volume_serial_number, file_index


def _windows_directory_handle_stat_identity(handle: int) -> tuple[int, int]:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.DuplicateHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    process = kernel32.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not kernel32.DuplicateHandle(
        process,
        wintypes.HANDLE(handle),
        process,
        ctypes.byref(duplicate),
        0,
        False,
        0x00000002,
    ):
        raise ValueError("retirement directory handle is unavailable")
    duplicate_value = int(duplicate.value)
    try:
        descriptor = msvcrt.open_osfhandle(duplicate_value, os.O_RDONLY)
    except (OSError, OverflowError, ValueError) as exc:
        kernel32.CloseHandle(wintypes.HANDLE(duplicate_value))
        raise ValueError("retirement directory handle is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise ValueError("retirement directory handle is unavailable") from exc
    finally:
        os.close(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError("retirement directory handle is invalid")
    return metadata.st_dev, metadata.st_ino


def _retirement_handle_identity(handle: int) -> tuple[int, int]:
    if _IMPORT_PLATFORM != "nt":
        raise ValueError("evidence import is unavailable on this platform")
    return _windows_directory_handle_identity(handle)


def _open_retirement_directory(
    path: Path,
    evidence_root: Path,
) -> _RetirementDirectoryReceipt:
    if _IMPORT_PLATFORM != "nt":
        raise ValueError("evidence import is unavailable on this platform")

    import ctypes
    from ctypes import wintypes

    authenticated_root = EvidenceRoot.create(path)
    safe_path = authenticated_root.path
    expected_evidence_root = Path(os.path.abspath(evidence_root))
    expected_path = expected_evidence_root.parent / ".tracelane-staging" / "retired"
    if safe_path != expected_path:
        raise ValueError("retirement directory containment is invalid")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateFileW(
        str(safe_path),
        0x0001 | 0x0004 | 0x0080 | 0x00100000,
        0x00000007,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle is None or int(handle) == invalid_handle:
        raise ValueError("retirement directory handle is unavailable")
    handle_value = int(handle)
    try:
        identity = _windows_directory_handle_identity(handle_value)
        stat_identity = _windows_directory_handle_stat_identity(handle_value)
        if stat_identity != authenticated_root._opened_identity or identity[1] != stat_identity[1]:
            raise ValueError("retirement directory changed during handle acquisition")
        receipt = _RetirementDirectoryReceipt(
            path=safe_path,
            directory_identity=identity,
            authenticated_identity=authenticated_root._opened_identity,
            evidence_root=expected_evidence_root,
            handle=handle_value,
        )
        if not _retirement_directory_path_matches(receipt):
            raise ValueError("retirement directory changed during handle acquisition")
    except BaseException:
        kernel32.CloseHandle(wintypes.HANDLE(handle_value))
        raise
    return receipt


def _close_retirement_directory(receipt: _RetirementDirectoryReceipt) -> None:
    if _IMPORT_PLATFORM != "nt":
        raise ValueError("evidence import is unavailable on this platform")

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    if not kernel32.CloseHandle(wintypes.HANDLE(receipt.handle)):
        raise ValueError("retirement directory handle could not be closed")


def _retirement_directory_path_matches(
    receipt: _RetirementDirectoryReceipt,
) -> bool:
    expected_path = receipt.evidence_root.parent / ".tracelane-staging" / "retired"
    if receipt.path != expected_path:
        return False
    try:
        metadata = receipt.path.lstat()
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            return False
        current_identity = metadata.st_dev, metadata.st_ino
        if (
            current_identity != receipt.authenticated_identity
            or current_identity[1] != receipt.directory_identity[1]
        ):
            return False
        resolved = receipt.path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return False
    return os.path.normcase(os.path.normpath(str(resolved))) == os.path.normcase(
        os.path.normpath(str(receipt.path))
    )


def _windows_move_directory_no_replace_at(
    source: Path,
    destination_handle: int,
    destination_name: str,
    source_identity: tuple[int, int] | None,
) -> None:
    import ctypes
    from ctypes import wintypes

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("status_or_pointer", ctypes.c_void_p),
            ("information", ctypes.c_size_t),
        ]

    class _FileRenameInformation(ctypes.Structure):
        _fields_ = [
            ("replace_if_exists", ctypes.c_ubyte),
            ("root_directory", wintypes.HANDLE),
            ("file_name_length", wintypes.DWORD),
            ("file_name", wintypes.WCHAR * 1),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ntdll.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    ntdll.NtSetInformationFile.restype = ctypes.c_long

    source_handle = kernel32.CreateFileW(
        str(source),
        0x00010000 | 0x0080 | 0x00100000,
        0x00000007,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if source_handle is None or int(source_handle) == invalid_handle:
        raise OSError(errno.EACCES, "project directory publication failed")
    source_handle_value = int(source_handle)
    moved = False
    try:
        opened_identity = _windows_directory_handle_identity(source_handle_value)
        if source_identity is not None and opened_identity[1] != source_identity[1]:
            raise ValueError("owned directory changed before retirement")

        encoded_name = destination_name.encode("utf-16-le")
        file_name_offset = _FileRenameInformation.file_name.offset
        buffer = ctypes.create_string_buffer(file_name_offset + len(encoded_name))
        information = ctypes.cast(
            buffer,
            ctypes.POINTER(_FileRenameInformation),
        ).contents
        information.replace_if_exists = 0
        information.root_directory = wintypes.HANDLE(destination_handle)
        information.file_name_length = len(encoded_name)
        ctypes.memmove(
            ctypes.addressof(buffer) + file_name_offset,
            encoded_name,
            len(encoded_name),
        )
        io_status = _IoStatusBlock()
        status = ntdll.NtSetInformationFile(
            wintypes.HANDLE(source_handle_value),
            ctypes.byref(io_status),
            ctypes.cast(buffer, ctypes.c_void_p),
            len(buffer),
            10,
        )
        if status < 0:
            unsigned_status = status & 0xFFFFFFFF
            if unsigned_status == 0xC0000035:
                raise FileExistsError(
                    errno.EEXIST,
                    "project destination already exists",
                )
            raise OSError(errno.EACCES, "project directory publication failed")
        moved = True
    finally:
        closed = kernel32.CloseHandle(wintypes.HANDLE(source_handle_value))
        if not closed and not moved:
            raise OSError(errno.EACCES, "project directory publication failed")


def _move_project_directory_no_replace(
    source: Path,
    target: Path,
    *,
    retirement_receipt: _RetirementDirectoryReceipt | None = None,
    source_identity: tuple[int, int] | None = None,
) -> None:
    source = Path(source)
    target = Path(target)
    if retirement_receipt is not None:
        if (
            target.parent != retirement_receipt.path
            or target.name in {"", ".", ".."}
            or Path(target.name).name != target.name
        ):
            raise ValueError("retirement destination is invalid")
        if (
            _retirement_handle_identity(retirement_receipt.handle)
            != retirement_receipt.directory_identity
        ):
            raise ValueError("retirement directory handle changed")
        path_matched_before = _retirement_directory_path_matches(retirement_receipt)
        _windows_move_directory_no_replace_at(
            source,
            retirement_receipt.handle,
            target.name,
            source_identity,
        )
        try:
            if (
                _retirement_handle_identity(retirement_receipt.handle)
                != retirement_receipt.directory_identity
                or not path_matched_before
                or not _retirement_directory_path_matches(retirement_receipt)
            ):
                raise ValueError("retirement directory changed during movement")
        except (OSError, TypeError, ValueError) as exc:
            raise _DirectoryRetirementMaintenanceError from exc
        return
    if _IMPORT_PLATFORM != "nt":
        raise OSError(
            errno.ENOTSUP,
            "evidence import is unavailable on this platform",
        )

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file_ex = kernel32.MoveFileExW
    move_file_ex.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_ulong,
    ]
    move_file_ex.restype = ctypes.c_int
    if not move_file_ex(str(source), str(target), 0):
        error = ctypes.get_last_error()
        if target.exists() or target.is_symlink():
            raise FileExistsError(error, "project destination already exists")
        raise OSError(error, "project directory publication failed")


def _retire_owned_directory(
    receipt: _DirectoryOwnershipReceipt,
    retirement_receipt: _RetirementDirectoryReceipt,
    *,
    prefix: str,
    tree_snapshot: tuple[tuple[object, ...], ...] | None = None,
) -> Path:
    if not isinstance(receipt, _DirectoryOwnershipReceipt):
        raise ValueError("directory ownership receipt is invalid")
    source = receipt.path
    if _directory_identity(source) != receipt.directory_identity:
        raise ValueError("owned directory changed before retirement")
    if tree_snapshot is not None and _project_directory_snapshot(source) != tree_snapshot:
        raise ValueError("owned directory tree changed before retirement")

    retired = retirement_receipt.path / f"{prefix}-{uuid.uuid4().hex}"
    _move_project_directory_no_replace(
        source,
        retired,
        retirement_receipt=retirement_receipt,
        source_identity=receipt.directory_identity,
    )

    try:
        if not _retirement_directory_path_matches(retirement_receipt):
            raise ValueError("retirement directory changed")
        if _directory_identity(retired) != receipt.directory_identity:
            raise ValueError("retired directory identity changed")
        if tree_snapshot is not None and _project_directory_snapshot(retired) != tree_snapshot:
            raise ValueError("retired directory tree changed")
        if _directory_identity(retired) != receipt.directory_identity:
            raise ValueError("retired directory identity changed")
        if _entry_exists_no_follow(source):
            raise ValueError("owned directory retirement did not clear its live path")
        if not _retirement_directory_path_matches(retirement_receipt):
            raise ValueError("retirement directory changed")
    except (OSError, TypeError, ValueError) as exc:
        raise _DirectoryRetirementMaintenanceError from exc
    return retired


def _publish_project_directory_no_replace(
    source: Path,
    target: Path,
    retirement_receipt: _RetirementDirectoryReceipt | None = None,
) -> _ProjectDirectoryPublicationReceipt:
    source = Path(source)
    target = Path(target)
    source_snapshot = _project_directory_snapshot(source)
    root_entry = source_snapshot[0]
    source_identity = int(root_entry[2]), int(root_entry[3])
    _move_project_directory_no_replace(source, target)
    receipt = _ProjectDirectoryPublicationReceipt(
        target=target,
        directory_identity=source_identity,
        tree_snapshot=source_snapshot,
    )
    try:
        if _project_directory_snapshot(target) != source_snapshot:
            raise ValueError("project publication tree changed")
    except (OSError, TypeError, ValueError):
        try:
            _rollback_project_directory_publication(
                receipt,
                retirement_receipt,
            )
        except _ProjectQuarantineMaintenanceError:
            raise
        except (OSError, TypeError, ValueError):
            raise ValueError("project directory publication rollback failed") from None
        raise ValueError("project directory publication changed") from None
    return receipt


def _rollback_project_directory_publication(
    receipt: _ProjectDirectoryPublicationReceipt,
    retirement_receipt: _RetirementDirectoryReceipt | None = None,
) -> None:
    if not isinstance(receipt, _ProjectDirectoryPublicationReceipt):
        raise ValueError("project publication receipt is invalid")
    target = receipt.target
    try:
        current_snapshot = _project_directory_snapshot(target)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("project publication rollback target changed") from exc
    if (
        not current_snapshot
        or current_snapshot[0][2:] != receipt.directory_identity
        or current_snapshot != receipt.tree_snapshot
    ):
        raise ValueError("project publication rollback target changed")
    managed_retirement_receipt: _RetirementDirectoryReceipt | None = None
    if retirement_receipt is None:
        if target.parent.name == "projects":
            evidence_root = target.parent.parent
            namespace = evidence_root.parent / ".tracelane-staging" / "retired"
        else:
            evidence_root = target
            namespace = target.parent / ".tracelane-staging" / "retired"
        managed_retirement_receipt = _open_retirement_directory(
            namespace,
            evidence_root,
        )
        retirement_receipt = managed_retirement_receipt
    try:
        _retire_owned_directory(
            _DirectoryOwnershipReceipt(
                path=target,
                directory_identity=receipt.directory_identity,
            ),
            retirement_receipt,
            prefix="project",
            tree_snapshot=receipt.tree_snapshot,
        )
    except _DirectoryRetirementMaintenanceError as exc:
        raise _ProjectQuarantineMaintenanceError from exc
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("project publication rollback failed") from exc
    finally:
        if managed_retirement_receipt is not None:
            with suppress(OSError, TypeError, ValueError):
                _close_retirement_directory(managed_retirement_receipt)


def _import_acquisition_project(
    source_root: str | Path,
    target_root: str | Path,
    project: EvidenceProject,
    metadata: EvidenceImportMetadata,
) -> EvidenceImportReport:
    if _IMPORT_PLATFORM != "nt":
        raise ValueError("evidence import is unavailable on this platform")

    project, metadata = _validated_inputs(project, metadata)
    source_path = _canonical_absolute_path(source_root)
    target_path = _canonical_absolute_path(target_root)
    if _paths_overlap(source_path, target_path):
        raise ValueError("acquisition import source and target overlap")
    if _target_overlaps_control_roots(target_path):
        raise ValueError("evidence import target is invalid")
    service, authenticated = _authenticate_source(source_path, metadata)
    committed_report: EvidenceImportReport | None = None
    retirement_receipt: _RetirementDirectoryReceipt | None = None
    try:
        with evidence_root_mutation_lock(target_path):
            staging_namespace, stage_path = _staging_location(target_path)
            retirement_receipt = _open_retirement_directory(
                staging_namespace / "retired",
                target_path,
            )
            stage_receipt: _DirectoryOwnershipReceipt | None = None
            project_receipt: _ProjectDirectoryPublicationReceipt | None = None
            registry_receipt: JsonReplacementReceipt | None = None
            transaction_failed = False
            try:
                staged_root = EvidenceRoot.create(stage_path)
                stage_receipt = _DirectoryOwnershipReceipt(
                    path=staged_root.path,
                    directory_identity=staged_root._opened_identity,
                )
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
                _assert_source_unchanged(
                    service,
                    source_path,
                    authenticated,
                    metadata.session_id,
                )
                if project_exists:
                    _authenticate_existing_project(
                        target,
                        project,
                        expected_index,
                    )
                else:
                    try:
                        project_receipt = _publish_project_directory_no_replace(
                            stage.path / "projects" / project.project_id,
                            final_project,
                            retirement_receipt,
                        )
                    except OSError:
                        raise ValueError("evidence import target changed") from None
                    _authenticate_existing_project(
                        target,
                        project,
                        expected_index,
                    )
                registry_receipt = _publish_registry(target)
                verified = verify_evidence_registry(target, project.project_id)
                if verified.project_index_sha256 is None:
                    raise ValueError("evidence import target is invalid")
                committed_report = EvidenceImportReport(
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
            except BaseException:
                transaction_failed = True
                if committed_report is None:
                    rollback_failed = False
                    quarantine_requires_maintenance = False
                    if registry_receipt is not None:
                        try:
                            rollback_json_replacement(target, registry_receipt)
                        except (OSError, TypeError, ValueError):
                            rollback_failed = True
                    if project_receipt is not None:
                        try:
                            _rollback_project_directory_publication(
                                project_receipt,
                                retirement_receipt,
                            )
                        except _ProjectQuarantineMaintenanceError:
                            quarantine_requires_maintenance = True
                        except (OSError, TypeError, ValueError):
                            rollback_failed = True
                    if quarantine_requires_maintenance:
                        raise ValueError(
                            "evidence import quarantine requires maintenance"
                        ) from None
                    if rollback_failed:
                        raise ValueError("evidence import target is invalid") from None
                raise
            finally:
                if stage_receipt is not None:
                    try:
                        _safe_remove_stage(
                            stage_path,
                            staging_namespace,
                            target_path,
                            stage_receipt,
                            retirement_receipt,
                        )
                    except _ProjectQuarantineMaintenanceError:
                        raise
                    except ValueError:
                        if committed_report is None and not transaction_failed:
                            raise
            if registry_receipt is None:
                raise ValueError("evidence import target is invalid")
            with suppress(OSError, TypeError, ValueError):
                commit_json_replacement(registry_receipt)
    except (OSError, TypeError, ValueError):
        if committed_report is None:
            raise
    finally:
        if retirement_receipt is not None:
            with suppress(OSError, TypeError, ValueError):
                _close_retirement_directory(retirement_receipt)
    if committed_report is None:
        raise ValueError("evidence import target is invalid")
    return committed_report


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
