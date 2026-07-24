from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.normpath(first)) == os.path.normcase(os.path.normpath(second))


def _is_link_or_reparse(metadata: os.stat_result) -> bool:
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
        raise ValueError(f"artifact lock {label} is unavailable") from exc
    if _is_link_or_reparse(metadata):
        raise ValueError(f"artifact lock {label} must not be a link or reparse point")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"artifact lock {label} must be a directory")
    return metadata


def _validate_lock_target(
    metadata: os.stat_result,
) -> None:
    if _is_link_or_reparse(metadata):
        raise ValueError("artifact lock file must not be a link or reparse point")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("artifact lock file must be regular")
    if metadata.st_nlink != 1:
        raise ValueError("artifact lock file must not have multiple links")


def _prepare_lock_path(path: Path) -> tuple[Path, tuple[int, int], tuple[int, int]]:
    supplied = Path(path)
    lock_dir = _absolute(supplied.parent)
    artifact_root = lock_dir.parent
    if lock_dir.name != ".locks":
        raise ValueError("artifact lock path must be under artifact_root/.locks")

    root_metadata = _validate_directory(artifact_root, "artifact root")
    resolved_root = artifact_root.resolve(strict=True)
    if not _same_path(artifact_root, resolved_root):
        raise ValueError("artifact lock root must not contain a link or reparse point")

    lock_path = _absolute(supplied)
    expected_path = resolved_root / ".locks" / supplied.name
    if not _same_path(lock_path, expected_path):
        raise ValueError("artifact lock path escapes the artifact root")
    try:
        lock_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("artifact lock path escapes the artifact root") from exc

    try:
        lock_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ValueError("artifact lock directory is unavailable") from exc

    lock_dir_metadata = _validate_directory(lock_dir, "lock directory")
    if not _same_path(lock_dir.resolve(strict=True), resolved_root / ".locks"):
        raise ValueError("artifact lock directory escapes the artifact root")
    if _identity(_validate_directory(resolved_root, "artifact root")) != _identity(root_metadata):
        raise ValueError("artifact lock root changed while opening the lock")
    return lock_path, _identity(root_metadata), _identity(lock_dir_metadata)


def _open_lock_file(
    path: Path,
    *,
    root_identity: tuple[int, int],
    lock_dir_identity: tuple[int, int],
) -> int:
    try:
        before = path.lstat()
    except FileNotFoundError:
        before = None
    except OSError as exc:
        raise ValueError("artifact lock file is unavailable") from exc
    if before is not None:
        _validate_lock_target(before)

    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if before is None:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if before is not None:
            raise ValueError("artifact lock file changed while opening") from None
        try:
            before = path.lstat()
            _validate_lock_target(before)
            descriptor = os.open(
                path,
                os.O_RDWR
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            raise ValueError("artifact lock file is unavailable") from exc
    except OSError as exc:
        raise ValueError("artifact lock file is unavailable") from exc

    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        _validate_lock_target(opened)
        _validate_lock_target(current)
        if before is not None and _identity(current) != _identity(before):
            raise ValueError("artifact lock file changed while opening")
        if _identity(opened) != _identity(current):
            raise ValueError("artifact lock file identity is invalid")
        if _identity(_validate_directory(path.parent.parent, "artifact root")) != root_identity:
            raise ValueError("artifact lock root changed while opening the lock")
        if _identity(_validate_directory(path.parent, "lock directory")) != lock_dir_identity:
            raise ValueError("artifact lock directory changed while opening the lock")
    except OSError as exc:
        os.close(descriptor)
        raise ValueError("artifact lock file is unavailable") from exc
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _validate_acquired_lock(
    descriptor: int,
    path: Path,
    *,
    root_identity: tuple[int, int],
    lock_dir_identity: tuple[int, int],
) -> None:
    try:
        opened = os.fstat(descriptor)
        current = path.lstat()
        _validate_lock_target(opened)
        _validate_lock_target(current)
        if _identity(opened) != _identity(current):
            raise ValueError("artifact lock file identity is invalid")
        if _identity(_validate_directory(path.parent.parent, "artifact root")) != root_identity:
            raise ValueError("artifact lock root changed while held")
        if _identity(_validate_directory(path.parent, "lock directory")) != lock_dir_identity:
            raise ValueError("artifact lock directory changed while held")
    except OSError as exc:
        raise ValueError("artifact lock file is unavailable") from exc


@contextmanager
def exclusive_file_lock(path: Path, *, blocking: bool = False) -> Iterator[None]:
    lock_path, root_identity, lock_dir_identity = _prepare_lock_path(Path(path))
    descriptor = _open_lock_file(
        lock_path,
        root_identity=root_identity,
        lock_dir_identity=lock_dir_identity,
    )
    with os.fdopen(descriptor, "r+b", buffering=0) as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt

                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
            else:
                import fcntl

                mode = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
                fcntl.flock(handle.fileno(), mode)
            acquired = True
        except OSError as exc:
            raise ValueError("artifact lock is unavailable") from exc
        try:
            _validate_acquired_lock(
                handle.fileno(),
                lock_path,
                root_identity=root_identity,
                lock_dir_identity=lock_dir_identity,
            )
            try:
                yield
            finally:
                _validate_acquired_lock(
                    handle.fileno(),
                    lock_path,
                    root_identity=root_identity,
                    lock_dir_identity=lock_dir_identity,
                )
        finally:
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
