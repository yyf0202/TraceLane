from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.experiments.runner import inspect_run
from tracelane.security import assert_safe_tree
from tracelane.v2 import storage
from tracelane.v2.contracts import content_digest
from tracelane.v2.locking import exclusive_file_lock
from tracelane.v2.schema import validate_document
from tracelane.v2.storage import secure_read_bytes

_SAFE_ENTRY_PATH = re.compile(r"^[a-z0-9][a-zA-Z0-9._/-]*$")
_TREE_ROOT_UNAVAILABLE = "migration tree root is unavailable"
_TREE_CHANGED_DURING_SNAPSHOT = "migration tree changed during snapshot"


def _entry_path(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ENTRY_PATH.fullmatch(value):
        raise ValueError("migration entry path is invalid")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("migration entry path is unsafe")
    if Path(*parts).as_posix() != value:
        raise ValueError("migration entry path is not normalized")
    return value


def _validated_entries(
    entries: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    paths = tuple(_entry_path(entry.get("path")) for entry in entries)
    if len(paths) != len(set(paths)):
        raise ValueError("migration entry paths must be unique")
    if paths != tuple(sorted(paths)):
        raise ValueError("migration entry paths must be sorted")
    return entries


def _tree_entries(root: Path) -> tuple[dict[str, object], ...]:
    def require_root(path: Path) -> Path:
        try:
            return path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(_TREE_ROOT_UNAVAILABLE) from exc

    try:
        assert_safe_tree(root)
    except OSError as exc:
        require_root(root)
        raise ValueError(_TREE_CHANGED_DURING_SNAPSHOT) from exc
    resolved_root = require_root(root)
    entries: list[dict[str, object]] = []

    def visit(directory: Path) -> None:
        try:
            with os.scandir(directory) as scanner:
                descendants = sorted(scanner, key=lambda item: item.name)
        except OSError as exc:
            if directory == resolved_root:
                raise ValueError(_TREE_ROOT_UNAVAILABLE) from exc
            raise ValueError(_TREE_CHANGED_DURING_SNAPSHOT) from exc
        for descendant in descendants:
            path = Path(descendant.path)
            try:
                metadata = path.lstat()
            except OSError as exc:
                raise ValueError(_TREE_CHANGED_DURING_SNAPSHOT) from exc
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            attributes = getattr(metadata, "st_file_attributes", 0) or 0
            if stat.S_ISLNK(metadata.st_mode) or attributes & reparse_flag:
                raise ValueError(f"migration tree contains a link or reparse point: {path.name}")
            try:
                path.resolve(strict=True).relative_to(resolved_root)
            except OSError as exc:
                raise ValueError(_TREE_CHANGED_DURING_SNAPSHOT) from exc
            except ValueError as exc:
                raise ValueError(
                    f"migration tree descendant escapes the root: {path.name}"
                ) from exc
            if stat.S_ISDIR(metadata.st_mode):
                visit(path)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            try:
                data = secure_read_bytes(
                    path,
                    root=resolved_root,
                    label="migration source file",
                )
            except OSError as exc:
                raise ValueError(_TREE_CHANGED_DURING_SNAPSHOT) from exc
            except ValueError as exc:
                if isinstance(exc.__cause__, OSError):
                    raise ValueError(_TREE_CHANGED_DURING_SNAPSHOT) from exc
                raise
            entries.append(
                {
                    "path": _entry_path(path.relative_to(resolved_root).as_posix()),
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )

    visit(resolved_root)
    try:
        assert_safe_tree(resolved_root)
    except OSError as exc:
        require_root(resolved_root)
        raise ValueError(_TREE_CHANGED_DURING_SNAPSHOT) from exc
    return _validated_entries(tuple(entries))


def _source_root_sha256(source: Path) -> str:
    normalized = os.path.normcase(os.path.normpath(str(source.resolve(strict=True))))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _require_source_snapshot(
    source: Path,
    expected_entries: tuple[dict[str, object], ...],
    *,
    phase: str,
) -> None:
    if _tree_entries(source) != expected_entries:
        raise ValueError(f"migration source root changed {phase}")


@dataclass(frozen=True)
class MigrationManifest:
    schema_id: str
    schema_version: str
    content_sha256: str
    import_id: str
    source_format: str
    source_run_id: str
    imported_at: datetime
    entries: tuple[dict[str, object], ...]
    source_root_sha256: str
    payload_root_sha256: str

    @classmethod
    def create(
        cls,
        *,
        import_id: str,
        source_run_id: str,
        imported_at: datetime,
        entries: tuple[dict[str, object], ...],
        source_root_sha256: str,
    ) -> MigrationManifest:
        manifest = cls(
            schema_id="tracelane://schemas/migration-manifest/v2",
            schema_version="2.0.0",
            content_sha256="",
            import_id=import_id,
            source_format="tracelane-v1",
            source_run_id=source_run_id,
            imported_at=imported_at.astimezone(UTC),
            entries=_validated_entries(entries),
            source_root_sha256=source_root_sha256,
            payload_root_sha256=sha256_json(entries),
        )
        return replace(manifest, content_sha256=content_digest(manifest._raw_dict()))

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> MigrationManifest:
        validate_document("migration-manifest", value)
        entries = _validated_entries(
            tuple(dict(item) for item in value["entries"])  # type: ignore[union-attr]
        )
        manifest = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            content_sha256=str(value["content_sha256"]),
            import_id=str(value["import_id"]),
            source_format=str(value["source_format"]),
            source_run_id=str(value["source_run_id"]),
            imported_at=parse_utc(str(value["imported_at"])),
            entries=entries,
            source_root_sha256=str(value["source_root_sha256"]),
            payload_root_sha256=str(value["payload_root_sha256"]),
        )
        if content_digest(manifest._raw_dict()) != manifest.content_sha256:
            raise ValueError("migration manifest content hash mismatch")
        if sha256_json(manifest.entries) != manifest.payload_root_sha256:
            raise ValueError("migration payload root hash mismatch")
        return manifest

    def _raw_dict(self) -> dict[str, object]:
        return json.loads(canonical_json(self))

    def to_dict(self) -> dict[str, object]:
        value = self._raw_dict()
        validate_document("migration-manifest", value)
        if content_digest(value) != self.content_sha256:
            raise ValueError("migration manifest content hash mismatch")
        return value


@dataclass(frozen=True)
class MigrationResult:
    import_dir: Path
    payload_dir: Path
    manifest: MigrationManifest


def _read_manifest(path: Path) -> MigrationManifest:
    try:
        value = json.loads(secure_read_bytes(path, label="migration manifest").decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("migration manifest is invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("migration manifest must be an object")
    return MigrationManifest.from_dict(value)


def _import_id(
    source_run_id: str,
    source_root_sha256: str,
    entries: tuple[dict[str, object], ...],
) -> str:
    return sha256_json(
        {
            "source_format": "tracelane-v1",
            "source_run_id": source_run_id,
            "source_root_sha256": source_root_sha256,
            "entries": entries,
        }
    )[:24]


def _validate_existing_import(
    *,
    import_dir: Path,
    payload_dir: Path,
    expected_import_id: str,
    expected_source_run_id: str,
    expected_source_root_sha256: str,
    expected_entries: tuple[dict[str, object], ...],
) -> MigrationResult:
    assert_safe_tree(import_dir)
    manifest = _read_manifest(import_dir / "manifest.json")
    if manifest.import_id != expected_import_id:
        raise ValueError("existing v1 import identity does not match source")
    if manifest.source_run_id != expected_source_run_id:
        raise ValueError("existing v1 import source run does not match source")
    if manifest.entries != expected_entries:
        raise ValueError("existing v1 import entries do not match source")
    if manifest.source_root_sha256 != expected_source_root_sha256:
        raise ValueError("existing v1 import source root does not match source")
    if manifest.payload_root_sha256 != sha256_json(expected_entries):
        raise ValueError("existing v1 import payload root does not match source")
    if _tree_entries(payload_dir) != expected_entries:
        raise ValueError("existing v1 import payload does not match source")
    return MigrationResult(import_dir, payload_dir, manifest)


def _publish_identical(
    path: Path,
    data: bytes,
    *,
    root: Path,
    label: str,
    mismatch_message: str,
) -> None:
    if path.exists():
        if secure_read_bytes(path, root=root, label=label) != data:
            raise ValueError(mismatch_message)
        return
    try:
        storage.atomic_create_bytes(
            path,
            data,
            root=root,
            label=label,
        )
    except (FileExistsError, ValueError) as exc:
        if not path.exists():
            raise
        try:
            existing = secure_read_bytes(path, root=root, label=label)
        except ValueError:
            raise
        if existing != data:
            raise ValueError(mismatch_message) from exc
    if secure_read_bytes(path, root=root, label=label) != data:
        raise ValueError(mismatch_message)


def import_v1_run(
    source_run_dir: Path,
    artifact_root: Path,
    *,
    clock: Callable[[], datetime] | None = None,
) -> MigrationResult:
    source = Path(source_run_dir)
    assert_safe_tree(source)
    source = source.resolve(strict=True)
    target_root = Path(artifact_root)
    target_absolute = Path(os.path.abspath(target_root)).resolve(strict=False)
    try:
        target_absolute.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("migration target must not be inside source")
    entries = _tree_entries(source)
    inspect_run(source)
    if _tree_entries(source) != entries:
        raise ValueError("migration source root changed during semantic inspection")
    source_run_id = source.name
    source_root_sha256 = _source_root_sha256(source)
    import_id = _import_id(source_run_id, source_root_sha256, entries)
    target_root.mkdir(parents=True, exist_ok=True)
    assert_safe_tree(target_root)
    target_root = target_root.resolve(strict=True)
    import_dir = target_root / "imports" / "v1" / import_id
    payload_dir = import_dir / "payload"
    manifest_path = import_dir / "manifest.json"
    lock_path = target_root / ".locks" / f"migration-{import_id}.lock"
    with exclusive_file_lock(lock_path, blocking=True):
        _require_source_snapshot(source, entries, phase="while waiting for the import lock")
        if manifest_path.exists():
            result = _validate_existing_import(
                import_dir=import_dir,
                payload_dir=payload_dir,
                expected_import_id=import_id,
                expected_source_run_id=source_run_id,
                expected_source_root_sha256=source_root_sha256,
                expected_entries=entries,
            )
            _require_source_snapshot(
                source,
                entries,
                phase="during existing import authentication",
            )
            return result

        if import_dir.exists():
            assert_safe_tree(import_dir)
        payload_dir.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            source_path = source / Path(*str(entry["path"]).split("/"))
            target_path = payload_dir / Path(*str(entry["path"]).split("/"))
            target_path.parent.mkdir(parents=True, exist_ok=True)
            data = secure_read_bytes(
                source_path,
                root=source,
                label="migration source file",
            )
            _publish_identical(
                target_path,
                data,
                root=target_root,
                label="migration payload file",
                mismatch_message="partial v1 import payload does not match source",
            )
        if _tree_entries(source) != entries:
            raise ValueError("migration source root changed during import")
        if _tree_entries(payload_dir) != entries:
            raise ValueError("v1 import bytes do not match source")

        imported_at = (clock or (lambda: datetime.now(UTC)))()
        if imported_at.tzinfo is None or imported_at.utcoffset() is None:
            raise ValueError("migration clock must return a timezone-aware datetime")
        manifest = MigrationManifest.create(
            import_id=import_id,
            source_run_id=source_run_id,
            imported_at=imported_at,
            entries=entries,
            source_root_sha256=source_root_sha256,
        )
        import_dir.mkdir(parents=True, exist_ok=True)
        _publish_identical(
            manifest_path,
            (canonical_json(manifest.to_dict()) + "\n").encode("utf-8"),
            root=target_root,
            label="migration manifest",
            mismatch_message="existing migration manifest does not match requested import",
        )
        result = _validate_existing_import(
            import_dir=import_dir,
            payload_dir=payload_dir,
            expected_import_id=import_id,
            expected_source_run_id=source_run_id,
            expected_source_root_sha256=source_root_sha256,
            expected_entries=entries,
        )
        _require_source_snapshot(source, entries, phase="before returning the import result")
        return result
