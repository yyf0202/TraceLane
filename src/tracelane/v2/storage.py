from __future__ import annotations

import hashlib
import os
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import FileIO
from pathlib import Path, PurePosixPath

from tracelane.v2.contracts import ArtifactRef

_ARTIFACT_PREFIX = "tracelane://artifacts/"


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _is_link_or_reparse_metadata(metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(metadata, "st_file_attributes", 0) & reparse_flag)


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _validate_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ValueError(f"{label} directory is unavailable") from exc
    if _is_link_or_reparse_metadata(metadata):
        raise ValueError(f"{label} directory must not be a link or reparse point")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"{label} directory must be a directory")
    absolute = Path(os.path.abspath(path))
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} directory is unavailable") from exc
    if os.path.normcase(os.path.normpath(absolute)) != os.path.normcase(os.path.normpath(resolved)):
        raise ValueError(f"{label} directory must not contain a link or reparse point")
    return metadata


def _validate_regular(metadata: os.stat_result, label: str) -> None:
    if _is_link_or_reparse_metadata(metadata):
        raise ValueError(f"{label} must not be a link or reparse point")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} must be a regular file")
    if metadata.st_nlink != 1:
        raise ValueError(f"{label} must not have multiple links")


def _validate_root_containment(path: Path, root: Path | None, label: str) -> None:
    if root is None:
        return
    root_path = Path(root)
    _validate_directory(root_path, f"{label} root")
    resolved_root = root_path.resolve(strict=True)
    absolute_path = Path(os.path.abspath(path))
    try:
        absolute_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes its root") from exc


def _write_new_temporary(path: Path, data: bytes, label: str) -> tuple[int, int]:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"{label} temporary file is unavailable") from exc
    try:
        with os.fdopen(descriptor, "wb", buffering=0) as handle:
            remaining = memoryview(data)
            while remaining:
                written = handle.write(remaining)
                if written is None or written <= 0:
                    raise OSError("temporary file write was incomplete")
                remaining = remaining[written:]
            handle.flush()
            os.fsync(handle.fileno())
            metadata = os.fstat(handle.fileno())
            _validate_regular(metadata, f"{label} temporary file")
            identity = _identity(metadata)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return identity


def secure_read_bytes(
    path: str | Path,
    *,
    root: str | Path | None = None,
    label: str = "file",
) -> bytes:
    target = Path(path)
    parent = target.parent
    parent_before = _validate_directory(parent, label)
    _validate_root_containment(target, Path(root) if root is not None else None, label)
    try:
        before = target.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    _validate_regular(before, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    try:
        opened = os.fstat(descriptor)
        current = target.lstat()
        _validate_regular(opened, label)
        _validate_regular(current, label)
        if _identity(opened) != _identity(before) or _identity(opened) != _identity(current):
            raise ValueError(f"{label} identity changed while opening")
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed while opening")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current_after = target.lstat()
        _validate_regular(after, label)
        _validate_regular(current_after, label)
        if (
            _identity(after) != _identity(opened)
            or _identity(current_after) != _identity(opened)
            or after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
        ):
            raise ValueError(f"{label} changed while reading")
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed while reading")
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    finally:
        os.close(descriptor)


def retire_authenticated_file(
    path: str | Path,
    expected_data: bytes,
    *,
    root: str | Path | None = None,
    label: str = "file",
) -> None:
    target = Path(path)
    if not isinstance(expected_data, bytes):
        raise ValueError(f"{label} expected data must be bytes")
    parent = target.parent
    parent_before = _validate_directory(parent, label)
    _validate_root_containment(target, Path(root) if root is not None else None, label)
    try:
        before = target.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    _validate_regular(before, label)
    if secure_read_bytes(target, root=root, label=label) != expected_data:
        raise ValueError(f"{label} changed during retirement")
    try:
        current = target.lstat()
    except OSError as exc:
        raise ValueError(f"{label} changed during retirement") from exc
    _validate_regular(current, label)
    if _identity(current) != _identity(before):
        raise ValueError(f"{label} changed during retirement")
    if _identity(_validate_directory(parent, label)) != _identity(parent_before):
        raise ValueError(f"{label} parent changed during retirement")

    tombstone = target.with_name(f".{target.name}.{uuid.uuid4().hex}.retired")
    try:
        os.replace(target, tombstone)
    except OSError as exc:
        raise ValueError(f"{label} could not be retired") from exc

    try:
        retired = tombstone.lstat()
        _validate_regular(retired, f"{label} retired file")
        if _identity(retired) != _identity(before):
            raise ValueError(f"{label} changed during retirement")
        if (
            secure_read_bytes(
                tombstone,
                root=root,
                label=f"{label} retired file",
            )
            != expected_data
        ):
            raise ValueError(f"{label} changed during retirement")
        try:
            target.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ValueError(f"{label} changed during retirement") from exc
        else:
            raise ValueError(f"{label} changed during retirement")
        retired_current = tombstone.lstat()
        _validate_regular(retired_current, f"{label} retired file")
        if _identity(retired_current) != _identity(before):
            raise ValueError(f"{label} changed during retirement")
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed during retirement")
        tombstone.unlink()
    except BaseException:
        try:
            target.lstat()
        except FileNotFoundError:
            try:
                os.link(tombstone, target)
                tombstone.unlink()
            except OSError:
                pass
        except OSError:
            pass
        raise


def validate_open_file(
    handle: FileIO,
    path: str | Path,
    *,
    root: str | Path | None = None,
    label: str = "file",
) -> os.stat_result:
    target = Path(path)
    _validate_root_containment(target, Path(root) if root is not None else None, label)
    try:
        opened = os.fstat(handle.fileno())
        current = target.lstat()
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    _validate_regular(opened, label)
    _validate_regular(current, label)
    if _identity(opened) != _identity(current):
        raise ValueError(f"{label} identity changed while open")
    _validate_directory(target.parent, label)
    return opened


@contextmanager
def secure_open_append(
    path: str | Path,
    *,
    root: str | Path | None = None,
    label: str = "file",
) -> Iterator[FileIO]:
    target = Path(path)
    parent = target.parent
    parent_before = _validate_directory(parent, label)
    _validate_root_containment(target, Path(root) if root is not None else None, label)
    try:
        before = target.lstat()
    except FileNotFoundError:
        before = None
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if before is not None:
        _validate_regular(before, label)
    flags = (
        os.O_RDWR
        | os.O_APPEND
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if before is None:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    try:
        opened = os.fstat(descriptor)
        current = target.lstat()
        _validate_regular(opened, label)
        _validate_regular(current, label)
        if before is not None and _identity(opened) != _identity(before):
            raise ValueError(f"{label} identity changed while opening")
        if _identity(opened) != _identity(current):
            raise ValueError(f"{label} identity changed while opening")
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed while opening")
    except BaseException:
        os.close(descriptor)
        raise
    with os.fdopen(descriptor, "r+b", buffering=0) as handle:
        yield handle


def atomic_write_bytes(
    target: Path,
    data: bytes,
    *,
    root: str | Path | None = None,
    label: str = "file",
) -> None:
    target = Path(target)
    if not isinstance(data, bytes):
        raise ValueError(f"{label} data must be bytes")
    parent = target.parent
    parent_before = _validate_directory(parent, label)
    _validate_root_containment(target, Path(root) if root is not None else None, label)
    try:
        target_before = target.lstat()
    except FileNotFoundError:
        target_before = None
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if target_before is not None:
        _validate_regular(target_before, label)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary_identity: tuple[int, int] | None = None
    try:
        temporary_identity = _write_new_temporary(temporary, data, label)
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed while writing")
        try:
            target_current = target.lstat()
        except FileNotFoundError:
            target_current = None
        if (target_before is None) != (target_current is None):
            raise ValueError(f"{label} target changed while writing")
        if target_before is not None and target_current is not None:
            _validate_regular(target_current, label)
            if _identity(target_current) != _identity(target_before):
                raise ValueError(f"{label} target changed while writing")
        temporary_current = temporary.lstat()
        _validate_regular(temporary_current, f"{label} temporary file")
        if _identity(temporary_current) != temporary_identity:
            raise ValueError(f"{label} temporary file identity changed")
        os.replace(temporary, target)
        published = target.lstat()
        _validate_regular(published, label)
        if temporary_identity is not None and _identity(published) != temporary_identity:
            raise ValueError(f"{label} published identity is invalid")
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed while publishing")
    finally:
        temporary.unlink(missing_ok=True)


def atomic_create_bytes_with_identity(
    target: Path,
    data: bytes,
    *,
    root: str | Path | None = None,
    label: str = "file",
) -> tuple[int, int]:
    target = Path(target)
    if not isinstance(data, bytes):
        raise ValueError(f"{label} data must be bytes")
    parent = target.parent
    parent_before = _validate_directory(parent, label)
    _validate_root_containment(target, Path(root) if root is not None else None, label)
    try:
        target_before = target.lstat()
    except FileNotFoundError:
        target_before = None
    except OSError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if target_before is not None:
        _validate_regular(target_before, label)
        raise ValueError(f"{label} already exists")

    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary_identity: tuple[int, int] | None = None
    linked = False
    try:
        temporary_identity = _write_new_temporary(temporary, data, label)
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed while writing")
        try:
            target.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ValueError(f"{label} is unavailable") from exc
        else:
            raise ValueError(f"{label} already exists")
        temporary_current = temporary.lstat()
        _validate_regular(temporary_current, f"{label} temporary file")
        if _identity(temporary_current) != temporary_identity:
            raise ValueError(f"{label} temporary file identity changed")
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise ValueError(f"{label} already exists") from exc
        except OSError as exc:
            raise ValueError(f"{label} could not be published") from exc
        linked = True
        published = target.lstat()
        temporary_current = temporary.lstat()
        if (
            _is_link_or_reparse_metadata(published)
            or not stat.S_ISREG(published.st_mode)
            or _identity(published) != temporary_identity
            or _identity(temporary_current) != temporary_identity
            or published.st_nlink != 2
            or temporary_current.st_nlink != 2
        ):
            raise ValueError(f"{label} published identity is invalid")
        temporary.unlink()
        published = target.lstat()
        _validate_regular(published, label)
        if _identity(published) != temporary_identity:
            raise ValueError(f"{label} published identity is invalid")
        if _identity(_validate_directory(parent, label)) != _identity(parent_before):
            raise ValueError(f"{label} parent changed while publishing")
        return temporary_identity
    except BaseException:
        if linked and temporary_identity is not None:
            try:
                published = target.lstat()
                if _identity(published) == temporary_identity:
                    target.unlink()
            except OSError:
                pass
        raise
    finally:
        temporary.unlink(missing_ok=True)


def atomic_create_bytes(
    target: Path,
    data: bytes,
    *,
    root: str | Path | None = None,
    label: str = "file",
) -> None:
    atomic_create_bytes_with_identity(
        target,
        data,
        root=root,
        label=label,
    )


@dataclass(frozen=True)
class ArtifactRoot:
    path: Path

    def __post_init__(self) -> None:
        supplied = Path(self.path)
        if _is_link_or_reparse(supplied):
            raise ValueError("artifact root must not be a link or reparse point")
        supplied.mkdir(parents=True, exist_ok=True)
        resolved = supplied.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("artifact root must be a directory")
        object.__setattr__(self, "path", resolved)

    def resolve(self, uri: str) -> Path:
        if not isinstance(uri, str) or not uri.startswith(_ARTIFACT_PREFIX):
            raise ValueError("artifact URI has the wrong root")
        raw_relative = uri.removeprefix(_ARTIFACT_PREFIX)
        if "\\" in raw_relative or "%" in raw_relative:
            raise ValueError("artifact URI escapes artifact root")
        relative = PurePosixPath(raw_relative)
        if (
            not relative.parts
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_relative.split("/"))
        ):
            raise ValueError("artifact URI escapes artifact root")

        candidate = (self.path / Path(*relative.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.path)
        except ValueError as exc:
            raise ValueError("artifact URI escapes artifact root") from exc

        current = self.path
        for part in relative.parts:
            current /= part
            if current.exists() and _is_link_or_reparse(current):
                raise ValueError("artifact path contains a link or reparse point")
        return candidate


class BlobStore:
    def __init__(self, root: ArtifactRoot) -> None:
        self.root = root

    def put_bytes(self, data: bytes, media_type: str, kind: str) -> ArtifactRef:
        if not isinstance(data, bytes):
            raise ValueError("blob data must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        uri = f"{_ARTIFACT_PREFIX}blobs/sha256/{digest[:2]}/{digest}.blob"
        target = self.root.resolve(uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = self.root.resolve(uri)
        if not target.exists():
            try:
                atomic_create_bytes(
                    target,
                    data,
                    root=self.root.path,
                    label="artifact file",
                )
            except ValueError as exc:
                if "already exists" not in str(exc):
                    raise
        reference = ArtifactRef.from_dict(
            {
                "kind": kind,
                "uri": uri,
                "media_type": media_type,
                "sha256": digest,
                "size_bytes": len(data),
            }
        )
        self.verify(reference)
        return reference

    def verify(self, reference: ArtifactRef) -> Path:
        target = self.root.resolve(reference.uri)
        try:
            data = secure_read_bytes(
                target,
                root=self.root.path,
                label="artifact file",
            )
        except ValueError as exc:
            raise ValueError(f"artifact is unavailable: {reference.uri}: {exc}") from exc
        if len(data) != reference.size_bytes:
            raise ValueError(f"artifact size mismatch: {reference.uri}")
        if hashlib.sha256(data).hexdigest() != reference.sha256:
            raise ValueError(f"artifact hash mismatch: {reference.uri}")
        return target
