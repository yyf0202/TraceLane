from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from tracelane.contracts import canonical_json, sha256_json

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _validate_digest(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _validate_model_id(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("model_id must be a non-empty string")
    return value.strip()


def _validate_repeat(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("repeat must be a positive integer")
    return value


def compute_run_id(
    task_sha256: str,
    bundle_sha256: str,
    config_sha256: str,
    model_id: str,
    repeat: int,
) -> str:
    identity = {
        "bundle_sha256": _validate_digest(bundle_sha256, "bundle_sha256"),
        "config_sha256": _validate_digest(config_sha256, "config_sha256"),
        "model_id": _validate_model_id(model_id),
        "repeat": _validate_repeat(repeat),
        "task_sha256": _validate_digest(task_sha256, "task_sha256"),
    }
    return sha256_json(identity)


@dataclass(frozen=True)
class RunIdentity:
    task_sha256: str
    bundle_sha256: str
    config_sha256: str
    model_id: str
    repeat: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "task_sha256",
            _validate_digest(self.task_sha256, "task_sha256"),
        )
        object.__setattr__(
            self,
            "bundle_sha256",
            _validate_digest(self.bundle_sha256, "bundle_sha256"),
        )
        object.__setattr__(
            self,
            "config_sha256",
            _validate_digest(self.config_sha256, "config_sha256"),
        )
        object.__setattr__(self, "model_id", _validate_model_id(self.model_id))
        object.__setattr__(self, "repeat", _validate_repeat(self.repeat))

    @property
    def run_id(self) -> str:
        return compute_run_id(
            self.task_sha256,
            self.bundle_sha256,
            self.config_sha256,
            self.model_id,
            self.repeat,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_sha256": self.task_sha256,
            "bundle_sha256": self.bundle_sha256,
            "config_sha256": self.config_sha256,
            "model_id": self.model_id,
            "repeat": self.repeat,
        }

    @classmethod
    def from_dict(cls, value: object) -> RunIdentity:
        if not isinstance(value, dict):
            raise ValueError("run identity must be a JSON object")
        expected_keys = {
            "task_sha256",
            "bundle_sha256",
            "config_sha256",
            "model_id",
            "repeat",
        }
        if set(value) != expected_keys:
            raise ValueError("run identity fields are invalid")
        return cls(
            task_sha256=value["task_sha256"],
            bundle_sha256=value["bundle_sha256"],
            config_sha256=value["config_sha256"],
            model_id=value["model_id"],
            repeat=value["repeat"],
        )


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return path.is_symlink()
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass(frozen=True)
class RunStore:
    artifact_root: Path
    run_id: str
    run_dir: Path

    @classmethod
    def create(cls, artifact_root: str | Path, run_id: str) -> RunStore:
        if not isinstance(run_id, str) or not _SHA256.fullmatch(run_id):
            raise ValueError("run_id must be a lowercase SHA-256 digest")
        root = Path(artifact_root).resolve()
        run_dir = (root / "runs" / run_id).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        return cls(artifact_root=root, run_id=run_id, run_dir=run_dir)

    def path_for(self, name: str | Path) -> Path:
        if not isinstance(name, (str, Path)):
            raise ValueError("artifact path must be relative")
        relative = Path(name)
        raw_name = str(name)
        if relative.is_absolute() or relative.drive or _WINDOWS_DRIVE_PATH.match(raw_name):
            raise ValueError("artifact path must be relative")
        if not relative.parts or any(part == ".." for part in relative.parts):
            raise ValueError("artifact path escapes the run directory")

        candidate = (self.run_dir / relative).resolve(strict=False)
        try:
            candidate.relative_to(self.run_dir)
        except ValueError as exc:
            raise ValueError("artifact path escapes the run directory") from exc

        current = self.run_dir
        for part in relative.parts[:-1]:
            current /= part
            if current.exists() and _is_reparse_point(current):
                raise ValueError("artifact path escapes through a reparse point")
        return candidate

    def write_json(self, name: str | Path, value: object) -> Path:
        return self.write_bytes(name, (canonical_json(value) + "\n").encode("utf-8"))

    def write_bytes(self, name: str | Path, value: bytes) -> Path:
        if not isinstance(value, bytes):
            raise ValueError("artifact bytes must be bytes")
        target = self.path_for(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def append_jsonl(self, name: str | Path, value: object) -> Path:
        target = self.path_for(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = (canonical_json(value) + "\n").encode("utf-8")
        with target.open("ab") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return target

    def read_json(self, name: str | Path) -> object:
        target = self.path_for(name)
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"artifact is not valid JSON: {name}") from exc
