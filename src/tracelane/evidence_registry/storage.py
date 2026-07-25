from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from tracelane.contracts import canonical_json
from tracelane.security import assert_safe_tree
from tracelane.v2.contracts import ArtifactRef
from tracelane.v2.storage import atomic_create_bytes, secure_read_bytes

_EVIDENCE_PREFIX = "tracelane://evidence/"
_MAX_DISK_COMPONENT_CHARS = 255
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_URI_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(Path(path)))


def _path_components(path: Path) -> tuple[Path, ...]:
    parts = path.parts
    if not parts:
        return ()
    current = Path(parts[0])
    components = [current]
    for part in parts[1:]:
        current /= part
        components.append(current)
    return tuple(components)


def _validate_directory_metadata(metadata: os.stat_result, category: str) -> None:
    if _is_link_or_reparse(metadata):
        raise ValueError(f"{category} contains a link or reparse point")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{category} is unavailable")


def _validate_existing_ancestors(path: Path, *, include_target: bool) -> None:
    components = _path_components(path)
    if not include_target:
        components = components[:-1]
    for component in components:
        try:
            metadata = component.lstat()
        except OSError as exc:
            raise ValueError("evidence root is unavailable") from exc
        _validate_directory_metadata(metadata, "evidence root")


def _validate_root_tree(path: Path) -> os.stat_result:
    _validate_existing_ancestors(path, include_target=True)
    try:
        metadata = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError("evidence root is unavailable") from exc
    _validate_directory_metadata(metadata, "evidence root")
    if os.path.normcase(os.path.normpath(path)) != os.path.normcase(os.path.normpath(resolved)):
        raise ValueError("evidence root contains a link or reparse point")
    try:
        assert_safe_tree(path)
    except (OSError, ValueError) as exc:
        raise ValueError("evidence root contains a link or reparse point") from exc
    return metadata


def _validate_directory_chain_path(
    base: Path,
    base_identity: tuple[int, int],
    descendants: list[tuple[str, tuple[int, int]]],
    *,
    windows_identity: bool,
) -> None:
    current = base
    try:
        base_current = current.lstat()
    except OSError as exc:
        raise ValueError("evidence path changed during creation") from exc
    _validate_directory_metadata(base_current, "evidence path")
    if (
        base_current.st_ino != base_identity[1]
        if windows_identity
        else _identity(base_current) != base_identity
    ):
        raise ValueError("evidence path changed during creation")
    for name, expected_identity in descendants:
        current /= name
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ValueError("evidence path changed during creation") from exc
        _validate_directory_metadata(metadata, "evidence path")
        if (
            metadata.st_ino != expected_identity[1]
            if windows_identity
            else _identity(metadata) != expected_identity
        ):
            raise ValueError("evidence path changed during creation")
    _validate_existing_ancestors(current, include_target=True)


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _WindowsHandleInformation(ctypes.Structure):
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

    class _WindowsUnicodeString(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.USHORT),
            ("maximum_length", wintypes.USHORT),
            ("buffer", wintypes.LPWSTR),
        ]

    class _WindowsObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("length", wintypes.ULONG),
            ("root_directory", wintypes.HANDLE),
            ("object_name", ctypes.POINTER(_WindowsUnicodeString)),
            ("attributes", wintypes.ULONG),
            ("security_descriptor", wintypes.LPVOID),
            ("security_quality_of_service", wintypes.LPVOID),
        ]

    class _WindowsIoStatusBlock(ctypes.Structure):
        _fields_ = [
            ("status_or_pointer", ctypes.c_void_p),
            ("information", ctypes.c_size_t),
        ]

    class _WindowsFileDispositionInformation(ctypes.Structure):
        _fields_ = [("delete_file", ctypes.c_ubyte)]

    _WINDOWS_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WINDOWS_NTDLL = ctypes.WinDLL("ntdll")
    _WINDOWS_KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _WINDOWS_KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _WINDOWS_KERNEL32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsHandleInformation),
    ]
    _WINDOWS_KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _WINDOWS_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _WINDOWS_KERNEL32.CloseHandle.restype = wintypes.BOOL
    _WINDOWS_NTDLL.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_WindowsObjectAttributes),
        ctypes.POINTER(_WindowsIoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    ]
    _WINDOWS_NTDLL.NtCreateFile.restype = ctypes.c_long
    _WINDOWS_NTDLL.NtSetInformationFile.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsIoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    _WINDOWS_NTDLL.NtSetInformationFile.restype = ctypes.c_long

    _WINDOWS_DELETE = 0x00010000
    _WINDOWS_FILE_LIST_DIRECTORY = 0x0001
    _WINDOWS_FILE_READ_ATTRIBUTES = 0x0080
    _WINDOWS_SYNCHRONIZE = 0x00100000
    _WINDOWS_SHARE_ALL = 0x00000007
    _WINDOWS_OPEN_EXISTING = 3
    _WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _WINDOWS_FILE_ATTRIBUTE_NORMAL = 0x00000080
    _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _WINDOWS_OBJ_CASE_INSENSITIVE = 0x00000040
    _WINDOWS_FILE_OPEN_IF = 3
    _WINDOWS_FILE_DIRECTORY_FILE = 0x00000001
    _WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
    _WINDOWS_FILE_OPEN_REPARSE_POINT = 0x00200000
    _WINDOWS_FILE_CREATED = 2
    _WINDOWS_FILE_OPENED = 1
    _WINDOWS_FILE_DISPOSITION_INFORMATION = 13
    _WINDOWS_INVALID_HANDLE = ctypes.c_void_p(-1).value

    def _windows_handle_identity(handle: int) -> tuple[int, int]:
        information = _WindowsHandleInformation()
        if not _WINDOWS_KERNEL32.GetFileInformationByHandle(
            wintypes.HANDLE(handle),
            ctypes.byref(information),
        ):
            raise ValueError("evidence path handle is unavailable")
        if not information.file_attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY:
            raise ValueError("evidence path is unavailable")
        if information.file_attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("evidence path contains a link or reparse point")
        file_index = (information.file_index_high << 32) | information.file_index_low
        return information.volume_serial_number, file_index

    def _windows_close_handle(handle: int) -> None:
        if not _WINDOWS_KERNEL32.CloseHandle(wintypes.HANDLE(handle)):
            raise ValueError("evidence path handle could not be closed")

    def _windows_open_directory_path(path: Path) -> tuple[int, tuple[int, int]]:
        handle = _WINDOWS_KERNEL32.CreateFileW(
            str(path),
            _WINDOWS_FILE_LIST_DIRECTORY
            | _WINDOWS_FILE_READ_ATTRIBUTES
            | _WINDOWS_SYNCHRONIZE,
            _WINDOWS_SHARE_ALL,
            None,
            _WINDOWS_OPEN_EXISTING,
            _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS
            | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        handle_value = int(handle)
        if handle_value == _WINDOWS_INVALID_HANDLE:
            raise ValueError("evidence path handle is unavailable")
        try:
            return handle_value, _windows_handle_identity(handle_value)
        except BaseException:
            _WINDOWS_KERNEL32.CloseHandle(wintypes.HANDLE(handle_value))
            raise

    def _windows_delete_directory_handle(handle: int) -> None:
        disposition = _WindowsFileDispositionInformation(delete_file=1)
        io_status = _WindowsIoStatusBlock()
        status = _WINDOWS_NTDLL.NtSetInformationFile(
            wintypes.HANDLE(handle),
            ctypes.byref(io_status),
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
            _WINDOWS_FILE_DISPOSITION_INFORMATION,
        )
        if status < 0:
            raise ValueError("evidence path rollback failed")

    def _windows_create_directory_at(
        parent_handle: int,
        name: str,
    ) -> tuple[int, bool, tuple[int, int]]:
        name_buffer = ctypes.create_unicode_buffer(name)
        encoded_length = len(name.encode("utf-16-le"))
        unicode_name = _WindowsUnicodeString(
            length=encoded_length,
            maximum_length=encoded_length + 2,
            buffer=ctypes.cast(name_buffer, wintypes.LPWSTR),
        )
        object_attributes = _WindowsObjectAttributes(
            length=ctypes.sizeof(_WindowsObjectAttributes),
            root_directory=wintypes.HANDLE(parent_handle),
            object_name=ctypes.pointer(unicode_name),
            attributes=_WINDOWS_OBJ_CASE_INSENSITIVE,
            security_descriptor=None,
            security_quality_of_service=None,
        )
        io_status = _WindowsIoStatusBlock()
        child_handle = wintypes.HANDLE()
        status = _WINDOWS_NTDLL.NtCreateFile(
            ctypes.byref(child_handle),
            _WINDOWS_DELETE
            | _WINDOWS_FILE_LIST_DIRECTORY
            | _WINDOWS_FILE_READ_ATTRIBUTES
            | _WINDOWS_SYNCHRONIZE,
            ctypes.byref(object_attributes),
            ctypes.byref(io_status),
            None,
            _WINDOWS_FILE_ATTRIBUTE_NORMAL,
            _WINDOWS_SHARE_ALL,
            _WINDOWS_FILE_OPEN_IF,
            _WINDOWS_FILE_DIRECTORY_FILE
            | _WINDOWS_FILE_SYNCHRONOUS_IO_NONALERT
            | _WINDOWS_FILE_OPEN_REPARSE_POINT,
            None,
            0,
        )
        if status < 0 or child_handle.value is None:
            raise ValueError("evidence path could not be created")
        handle_value = int(child_handle.value)
        created = io_status.information == _WINDOWS_FILE_CREATED
        if not created and io_status.information != _WINDOWS_FILE_OPENED:
            _WINDOWS_KERNEL32.CloseHandle(child_handle)
            raise ValueError("evidence path creation result is invalid")
        try:
            identity = _windows_handle_identity(handle_value)
        except BaseException:
            if created:
                with suppress(ValueError):
                    _windows_delete_directory_handle(handle_value)
            _WINDOWS_KERNEL32.CloseHandle(child_handle)
            raise
        return handle_value, created, identity

    def _secure_ensure_directory_chain(
        base: Path,
        base_identity: tuple[int, int],
        parts: tuple[str, ...],
    ) -> None:
        handles: list[tuple[int, bool]] = []
        descendants: list[tuple[str, tuple[int, int]]] = []
        committed = False
        cleanup_error: ValueError | None = None
        try:
            base_handle, opened_base_identity = _windows_open_directory_path(base)
            handles.append((base_handle, False))
            if opened_base_identity[1] != base_identity[1]:
                raise ValueError("evidence path changed before creation")
            current_handle = base_handle
            for part in parts:
                child_handle, created, child_identity = _windows_create_directory_at(
                    current_handle,
                    part,
                )
                handles.append((child_handle, created))
                descendants.append((part, child_identity))
                current_handle = child_handle
            _validate_directory_chain_path(
                base,
                base_identity,
                descendants,
                windows_identity=True,
            )
            committed = True
        finally:
            for handle, created in reversed(handles):
                if not committed and created:
                    try:
                        _windows_delete_directory_handle(handle)
                    except ValueError as exc:
                        cleanup_error = exc
                try:
                    _windows_close_handle(handle)
                except ValueError as exc:
                    cleanup_error = exc
            if cleanup_error is not None:
                raise ValueError("evidence path rollback failed") from cleanup_error

else:

    def _posix_create_directory_at(
        parent_handle: int,
        name: str,
    ) -> tuple[int, bool, tuple[int, int]]:
        created = False
        try:
            os.mkdir(name, dir_fd=parent_handle)
            created = True
        except FileExistsError:
            pass
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            child_handle = os.open(name, flags, dir_fd=parent_handle)
        except OSError as exc:
            if created:
                with suppress(OSError):
                    os.rmdir(name, dir_fd=parent_handle)
            raise ValueError("evidence path could not be opened") from exc
        try:
            metadata = os.fstat(child_handle)
            _validate_directory_metadata(metadata, "evidence path")
        except BaseException:
            os.close(child_handle)
            if created:
                with suppress(OSError):
                    os.rmdir(name, dir_fd=parent_handle)
            raise
        return child_handle, created, _identity(metadata)

    def _secure_ensure_directory_chain(
        base: Path,
        base_identity: tuple[int, int],
        parts: tuple[str, ...],
    ) -> None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        handles: list[tuple[int, int | None, str | None, bool]] = []
        descendants: list[tuple[str, tuple[int, int]]] = []
        committed = False
        cleanup_error: ValueError | None = None
        try:
            try:
                base_handle = os.open(base, flags)
            except OSError as exc:
                raise ValueError("evidence path handle is unavailable") from exc
            handles.append((base_handle, None, None, False))
            opened_base_identity = _identity(os.fstat(base_handle))
            if opened_base_identity != base_identity:
                raise ValueError("evidence path changed before creation")
            current_handle = base_handle
            for part in parts:
                child_handle, created, child_identity = _posix_create_directory_at(
                    current_handle,
                    part,
                )
                handles.append((child_handle, current_handle, part, created))
                descendants.append((part, child_identity))
                current_handle = child_handle
            _validate_directory_chain_path(
                base,
                base_identity,
                descendants,
                windows_identity=False,
            )
            committed = True
        finally:
            for handle, parent_handle, name, created in reversed(handles):
                try:
                    os.close(handle)
                except OSError as exc:
                    cleanup_error = ValueError("evidence path handle could not be closed")
                    cleanup_error.__cause__ = exc
                if not committed and created and parent_handle is not None and name is not None:
                    try:
                        os.rmdir(name, dir_fd=parent_handle)
                    except OSError as exc:
                        cleanup_error = ValueError("evidence path rollback failed")
                        cleanup_error.__cause__ = exc
            if cleanup_error is not None:
                raise cleanup_error


def _safe_reference_dict(reference: ArtifactRef, category: str) -> dict[str, object]:
    if not isinstance(reference, ArtifactRef):
        raise ValueError(f"{category} reference metadata is invalid")
    try:
        return reference.to_dict()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{category} reference metadata is invalid") from exc


def _secure_read(path: Path, root: EvidenceRoot, category: str) -> bytes:
    try:
        return secure_read_bytes(path, root=root.path, label=category)
    except ValueError as exc:
        message = str(exc)
        if "link" in message:
            public = f"{category} contains an unsafe link"
        elif "changed" in message or "identity" in message:
            public = f"{category} changed during access"
        else:
            public = f"{category} is unavailable"
        raise ValueError(public) from exc


@dataclass(frozen=True)
class EvidenceRoot:
    path: Path
    _opened_identity: tuple[int, int] = field(repr=False)

    @classmethod
    def open(cls, path: str | Path) -> EvidenceRoot:
        supplied = _absolute(path)
        metadata = _validate_root_tree(supplied)
        return cls(path=supplied, _opened_identity=_identity(metadata))

    @classmethod
    def create(cls, path: str | Path) -> EvidenceRoot:
        supplied = _absolute(path)
        _validate_existing_ancestors(supplied, include_target=False)
        parent = supplied.parent
        try:
            parent_before = parent.lstat()
        except OSError as exc:
            raise ValueError("evidence root is unavailable") from exc
        _validate_directory_metadata(parent_before, "evidence root")
        try:
            supplied_before = supplied.lstat()
        except FileNotFoundError:
            supplied_before = None
        except OSError as exc:
            raise ValueError("evidence root is unavailable") from exc
        if supplied_before is None:
            try:
                _secure_ensure_directory_chain(
                    parent,
                    _identity(parent_before),
                    (supplied.name,),
                )
            except ValueError as exc:
                raise ValueError("evidence root changed during creation") from exc
        else:
            _validate_directory_metadata(supplied_before, "evidence root")
        try:
            parent_after = parent.lstat()
        except OSError as exc:
            raise ValueError("evidence root is unavailable") from exc
        _validate_directory_metadata(parent_after, "evidence root")
        if _identity(parent_after) != _identity(parent_before):
            raise ValueError("evidence root changed during creation")
        return cls.open(supplied)

    def _validate_open_identity(self) -> None:
        metadata = _validate_root_tree(self.path)
        if _identity(metadata) != self._opened_identity:
            raise ValueError("evidence root changed after opening")

    def resolve(self, uri: str, *, must_exist: bool = False) -> Path:
        self._validate_open_identity()
        if not isinstance(uri, str) or not uri.startswith(_EVIDENCE_PREFIX):
            raise ValueError("evidence URI has the wrong root")
        raw_relative = uri.removeprefix(_EVIDENCE_PREFIX)
        if not raw_relative or "\\" in raw_relative or "%" in raw_relative:
            raise ValueError("evidence URI is unsafe")
        parts = raw_relative.split("/")
        if any(
            part in {"", ".", ".."}
            or len(part) > _MAX_DISK_COMPONENT_CHARS
            or _URI_COMPONENT.fullmatch(part) is None
            for part in parts
        ):
            raise ValueError("evidence URI is unsafe")

        physical_parts = parts
        is_project_document = (
            len(parts) == 3
            and parts[0] == "projects"
            and parts[2] in {"index.json", "project.json"}
        )
        is_project_record = (
            len(parts) == 4
            and parts[0] == "projects"
            and parts[2] in {"candidates", "reviews", "transformations"}
        )
        if parts == ["registry.json"] or is_project_document or is_project_record:
            pass
        elif parts[0] == "blobs":
            if (
                len(parts) != 3
                or parts[1] != "sha256"
                or _SHA256.fullmatch(parts[2]) is None
            ):
                raise ValueError("evidence URI is unsafe")
            digest = parts[2]
            physical_parts = ["blobs", "sha256", digest[:2], f"{digest}.blob"]
        else:
            raise ValueError("evidence URI is outside the permitted namespaces")

        candidate = self.path.joinpath(*physical_parts)
        try:
            candidate.relative_to(self.path)
        except ValueError as exc:
            raise ValueError("evidence URI escapes the evidence root") from exc

        snapshots: list[tuple[Path, tuple[int, int]]] = []
        current = self.path
        missing = False
        for index, part in enumerate(physical_parts):
            current /= part
            if missing:
                continue
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                missing = True
                continue
            except OSError as exc:
                raise ValueError("evidence path is unavailable") from exc
            if _is_link_or_reparse(metadata):
                raise ValueError("evidence path contains a link or reparse point")
            is_last = index == len(physical_parts) - 1
            if not is_last and not stat.S_ISDIR(metadata.st_mode):
                raise ValueError("evidence path is unavailable")
            if is_last and stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
                raise ValueError("evidence path contains an unsafe link")
            snapshots.append((current, _identity(metadata)))

        if must_exist and missing:
            raise ValueError("evidence path is unavailable")
        for descendant, expected_identity in snapshots:
            try:
                current_metadata = descendant.lstat()
            except OSError as exc:
                raise ValueError("evidence path changed during resolution") from exc
            if _is_link_or_reparse(current_metadata):
                raise ValueError("evidence path contains a link or reparse point")
            if _identity(current_metadata) != expected_identity:
                raise ValueError("evidence path changed during resolution")
        self._validate_open_identity()
        return candidate

    def ensure_parent(self, target: Path) -> None:
        supplied_parent = Path(target).parent
        try:
            raw_relative_parent = supplied_parent.relative_to(self.path)
        except ValueError as exc:
            raise ValueError("evidence path escapes the evidence root") from exc
        if any(part in {".", ".."} for part in raw_relative_parent.parts):
            raise ValueError("evidence path is unsafe")
        normalized_parent = _absolute(supplied_parent)
        try:
            relative_parent = normalized_parent.relative_to(self.path)
        except ValueError as exc:
            raise ValueError("evidence path escapes the evidence root") from exc
        self._validate_open_identity()
        _secure_ensure_directory_chain(
            self.path,
            self._opened_identity,
            relative_parent.parts,
        )
        self._validate_open_identity()


class EvidenceBlobStore:
    def __init__(self, root: EvidenceRoot) -> None:
        if not isinstance(root, EvidenceRoot):
            raise ValueError("evidence root is invalid")
        self.root = root

    def put_bytes(self, data: bytes, media_type: str, kind: str) -> ArtifactRef:
        if not isinstance(data, bytes):
            raise ValueError("evidence blob data must be bytes")
        if kind != "evidence_blob":
            raise ValueError("evidence blob reference metadata is invalid")
        digest = hashlib.sha256(data).hexdigest()
        uri = f"{_EVIDENCE_PREFIX}blobs/sha256/{digest}"
        try:
            reference = ArtifactRef.from_dict(
                {
                    "kind": kind,
                    "uri": uri,
                    "media_type": media_type,
                    "sha256": digest,
                    "size_bytes": len(data),
                }
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("evidence blob reference metadata is invalid") from exc
        target = self.root.resolve(uri)
        self.root.ensure_parent(target)
        target = self.root.resolve(uri)
        try:
            atomic_create_bytes(
                target,
                data,
                root=self.root.path,
                label="evidence blob",
            )
        except ValueError as exc:
            try:
                existing = _secure_read(target, self.root, "evidence blob")
            except ValueError:
                raise ValueError("evidence blob publication failed") from exc
            if existing != data:
                raise ValueError("evidence blob conflicts with existing content") from exc
        self.verify(reference)
        return reference

    def verify(self, reference: ArtifactRef) -> Path:
        _safe_reference_dict(reference, "evidence blob")
        if (
            reference.kind != "evidence_blob"
            or reference.schema_id is not None
            or not reference.uri.startswith(f"{_EVIDENCE_PREFIX}blobs/sha256/")
        ):
            raise ValueError("evidence blob reference metadata is invalid")
        digest = reference.uri.removeprefix(f"{_EVIDENCE_PREFIX}blobs/sha256/")
        if _SHA256.fullmatch(digest) is None or reference.sha256 != digest:
            raise ValueError("evidence blob reference metadata is invalid")
        target = self.root.resolve(reference.uri, must_exist=True)
        data = _secure_read(target, self.root, "evidence blob")
        if len(data) != reference.size_bytes:
            raise ValueError("evidence blob size mismatch")
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ValueError("evidence blob hash mismatch")
        return target


def write_json_create_or_match(
    root: EvidenceRoot,
    uri: str,
    kind: str,
    schema_id: str,
    value: Mapping[str, object],
) -> ArtifactRef:
    if not isinstance(root, EvidenceRoot):
        raise ValueError("evidence root is invalid")
    if not isinstance(value, Mapping):
        raise ValueError("evidence JSON must be an object")
    try:
        data = canonical_json(value).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence JSON is invalid") from exc
    digest = hashlib.sha256(data).hexdigest()
    try:
        reference = ArtifactRef.from_dict(
            {
                "kind": kind,
                "uri": uri,
                "media_type": "application/json",
                "sha256": digest,
                "size_bytes": len(data),
                "schema_id": schema_id,
            }
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("evidence JSON reference metadata is invalid") from exc
    target = root.resolve(uri)
    root.ensure_parent(target)
    target = root.resolve(uri)
    try:
        atomic_create_bytes(target, data, root=root.path, label="evidence JSON")
    except ValueError as exc:
        try:
            existing = _secure_read(target, root, "evidence JSON")
        except ValueError:
            raise ValueError("evidence JSON publication failed") from exc
        if existing != data:
            raise ValueError("evidence JSON conflicts with existing content") from exc
    read_json_object(
        root,
        reference,
        expected_kind=kind,
        expected_schema_id=schema_id,
    )
    return reference


def read_json_object(
    root: EvidenceRoot,
    reference: ArtifactRef,
    *,
    expected_kind: str,
    expected_schema_id: str,
) -> dict[str, object]:
    if not isinstance(root, EvidenceRoot):
        raise ValueError("evidence root is invalid")
    _safe_reference_dict(reference, "evidence JSON")
    if (
        reference.kind != expected_kind
        or reference.schema_id != expected_schema_id
        or reference.media_type != "application/json"
    ):
        raise ValueError("evidence JSON reference metadata is invalid")
    target = root.resolve(reference.uri, must_exist=True)
    data = _secure_read(target, root, "evidence JSON")
    if len(data) != reference.size_bytes:
        raise ValueError("evidence JSON size mismatch")
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ValueError("evidence JSON hash mismatch")
    try:
        decoded = data.decode("utf-8")
        parsed = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("evidence JSON is invalid") from exc
    if not isinstance(parsed, dict):
        raise ValueError("evidence JSON must be an object")
    try:
        expected_data = canonical_json(parsed).encode("utf-8") + b"\n"
    except ValueError as exc:
        raise ValueError("evidence JSON is invalid") from exc
    if data != expected_data:
        raise ValueError("evidence JSON is not canonical")
    return parsed
