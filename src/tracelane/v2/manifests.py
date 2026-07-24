from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from tracelane.contracts import canonical_json, parse_utc, sha256_json
from tracelane.history.contracts import EvidenceManifest, HistoryCase
from tracelane.security import assert_safe_tree
from tracelane.v2 import storage
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.schema import validate_document
from tracelane.v2.storage import ArtifactRoot, secure_read_bytes
from tracelane.v2.tracing import read_trace_bytes

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_STATUS = frozenset({"created", "running", "completed", "failed"})
_TERMINAL_RUN_STATUS = frozenset({"completed", "failed"})
_FINGERPRINT_FIELDS = frozenset(
    {
        "case_sha256",
        "evidence_manifest_sha256",
        "harness_config_sha256",
        "runtime_config_sha256",
        "grader_set_sha256",
        "repeat",
        "code_revision",
    }
)
_RUN_INPUT_SLOTS = {
    "case_ref": (
        "case",
        "tracelane://schemas/case/v2",
        "application/json",
        "case_sha256",
    ),
    "evidence_manifest_ref": (
        "evidence_manifest",
        "tracelane://schemas/evidence-manifest/v2",
        "application/json",
        "evidence_manifest_sha256",
    ),
    "harness_config_ref": (
        "harness_config",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
        "harness_config_sha256",
    ),
    "runtime_config_ref": (
        "runtime_config",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
        "runtime_config_sha256",
    ),
    "grader_set_ref": (
        "grader_set",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
        "grader_set_sha256",
    ),
}
_RUN_SINGLE_REFERENCE_SLOTS = {
    "trace_ref": (
        "trace",
        "tracelane://schemas/trace-event/v2",
        "application/x-ndjson",
    ),
    "diagnosis_ref": (
        "diagnosis",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
    ),
    "grade_report_ref": (
        "grade_report",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
    ),
    "failure_ref": (
        "failure_record",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
    ),
}
_RUN_SEQUENCE_REFERENCE_SLOTS = {
    "checkpoint_refs": (
        "checkpoint",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
    ),
    "output_refs": (
        "output",
        "tracelane://schemas/object-envelope/v2",
        "application/json",
    ),
}
_OBJECT_SCHEMA_BY_KIND = {
    "harness_config": "tracelane://schemas/harness-config/v2",
    "runtime_config": "tracelane://schemas/runtime-config/v2",
    "grader_set": "tracelane://schemas/grader-set/v2",
    "checkpoint": "tracelane://schemas/checkpoint/v2",
    "diagnosis": "tracelane://schemas/diagnosis/v2",
    "output": "tracelane://schemas/output/v2",
    "grade_report": "tracelane://schemas/grade-report/v2",
    "failure_record": "tracelane://schemas/failure-record/v2",
}


def _digest(value: str, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _non_empty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ExecutionFingerprint:
    case_sha256: str
    evidence_manifest_sha256: str
    harness_config_sha256: str
    runtime_config_sha256: str
    grader_set_sha256: str
    repeat: int
    code_revision: str

    def __post_init__(self) -> None:
        for field_name in (
            "case_sha256",
            "evidence_manifest_sha256",
            "harness_config_sha256",
            "runtime_config_sha256",
            "grader_set_sha256",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name))
        if isinstance(self.repeat, bool) or not isinstance(self.repeat, int) or self.repeat < 1:
            raise ValueError("repeat must be a positive integer")
        object.__setattr__(self, "code_revision", _non_empty(self.code_revision, "code_revision"))

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ExecutionFingerprint:
        if not isinstance(value, Mapping) or set(value) != _FINGERPRINT_FIELDS:
            raise ValueError("execution fingerprint fields are invalid")
        string_fields = _FINGERPRINT_FIELDS - {"repeat"}
        if any(not isinstance(value[field], str) for field in string_fields):
            raise ValueError("execution fingerprint string fields must be strings")
        repeat = value["repeat"]
        if isinstance(repeat, bool) or not isinstance(repeat, int):
            raise ValueError("execution fingerprint repeat must be an integer")
        return cls(
            case_sha256=value["case_sha256"],  # type: ignore[arg-type]
            evidence_manifest_sha256=value["evidence_manifest_sha256"],  # type: ignore[arg-type]
            harness_config_sha256=value["harness_config_sha256"],  # type: ignore[arg-type]
            runtime_config_sha256=value["runtime_config_sha256"],  # type: ignore[arg-type]
            grader_set_sha256=value["grader_set_sha256"],  # type: ignore[arg-type]
            repeat=repeat,
            code_revision=value["code_revision"],  # type: ignore[arg-type]
        )

    def to_dict(self) -> dict[str, object]:
        return json.loads(canonical_json(asdict(self)))

    @property
    def run_id(self) -> str:
        return sha256_json(self.to_dict())


@dataclass(frozen=True)
class RunManifest:
    schema_id: str
    schema_version: str
    content_sha256: str
    run_id: str
    lifecycle_status: str
    started_at: datetime
    completed_at: datetime | None
    case_ref: ArtifactRef
    evidence_manifest_ref: ArtifactRef
    harness_config_ref: ArtifactRef
    runtime_config_ref: ArtifactRef
    grader_set_ref: ArtifactRef
    execution_fingerprint: ExecutionFingerprint
    environment_fingerprint: str
    semantic_convention_version: str
    redaction_policy_id: str
    trace_ref: ArtifactRef | None
    checkpoint_refs: tuple[ArtifactRef, ...]
    diagnosis_ref: ArtifactRef | None
    output_refs: tuple[ArtifactRef, ...]
    grade_report_ref: ArtifactRef | None
    failure_ref: ArtifactRef | None
    parent_run_id: str | None
    branch_id: str | None
    checksums_ref: ArtifactRef

    @classmethod
    def create(cls, **values: object) -> RunManifest:
        manifest = cls(
            schema_id="tracelane://schemas/run-manifest/v2",
            schema_version="2.0.0",
            content_sha256="",
            **values,
        )
        finalized = replace(manifest, content_sha256=content_digest(manifest._raw_dict()))
        finalized.to_dict()
        return finalized

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RunManifest:
        validate_document("run-manifest", value)
        manifest = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            content_sha256=str(value["content_sha256"]),
            run_id=str(value["run_id"]),
            lifecycle_status=str(value["lifecycle_status"]),
            started_at=parse_utc(str(value["started_at"])),
            completed_at=(
                parse_utc(str(value["completed_at"])) if value["completed_at"] is not None else None
            ),
            case_ref=ArtifactRef.from_dict(value["case_ref"]),  # type: ignore[arg-type]
            evidence_manifest_ref=ArtifactRef.from_dict(
                value["evidence_manifest_ref"]  # type: ignore[arg-type]
            ),
            harness_config_ref=ArtifactRef.from_dict(
                value["harness_config_ref"]  # type: ignore[arg-type]
            ),
            runtime_config_ref=ArtifactRef.from_dict(
                value["runtime_config_ref"]  # type: ignore[arg-type]
            ),
            grader_set_ref=ArtifactRef.from_dict(value["grader_set_ref"]),  # type: ignore[arg-type]
            execution_fingerprint=ExecutionFingerprint.from_dict(
                value["execution_fingerprint"]  # type: ignore[arg-type]
            ),
            environment_fingerprint=str(value["environment_fingerprint"]),
            semantic_convention_version=str(value["semantic_convention_version"]),
            redaction_policy_id=str(value["redaction_policy_id"]),
            trace_ref=(
                ArtifactRef.from_dict(value["trace_ref"])  # type: ignore[arg-type]
                if value["trace_ref"] is not None
                else None
            ),
            checkpoint_refs=tuple(
                ArtifactRef.from_dict(item)
                for item in value["checkpoint_refs"]  # type: ignore[union-attr]
            ),
            diagnosis_ref=(
                ArtifactRef.from_dict(value["diagnosis_ref"])  # type: ignore[arg-type]
                if value["diagnosis_ref"] is not None
                else None
            ),
            output_refs=tuple(
                ArtifactRef.from_dict(item)
                for item in value["output_refs"]  # type: ignore[union-attr]
            ),
            grade_report_ref=(
                ArtifactRef.from_dict(value["grade_report_ref"])  # type: ignore[arg-type]
                if value["grade_report_ref"] is not None
                else None
            ),
            failure_ref=(
                ArtifactRef.from_dict(value["failure_ref"])  # type: ignore[arg-type]
                if value["failure_ref"] is not None
                else None
            ),
            parent_run_id=(
                str(value["parent_run_id"]) if value["parent_run_id"] is not None else None
            ),
            branch_id=str(value["branch_id"]) if value["branch_id"] is not None else None,
            checksums_ref=ArtifactRef.from_dict(value["checksums_ref"]),  # type: ignore[arg-type]
        )
        manifest._validate_invariants()
        return manifest

    def _raw_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "content_sha256": self.content_sha256,
            "run_id": self.run_id,
            "lifecycle_status": self.lifecycle_status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "case_ref": self.case_ref.to_dict(),
            "evidence_manifest_ref": self.evidence_manifest_ref.to_dict(),
            "harness_config_ref": self.harness_config_ref.to_dict(),
            "runtime_config_ref": self.runtime_config_ref.to_dict(),
            "grader_set_ref": self.grader_set_ref.to_dict(),
            "execution_fingerprint": self.execution_fingerprint.to_dict(),
            "environment_fingerprint": self.environment_fingerprint,
            "semantic_convention_version": self.semantic_convention_version,
            "redaction_policy_id": self.redaction_policy_id,
            "trace_ref": self.trace_ref.to_dict() if self.trace_ref is not None else None,
            "checkpoint_refs": [reference.to_dict() for reference in self.checkpoint_refs],
            "diagnosis_ref": (
                self.diagnosis_ref.to_dict() if self.diagnosis_ref is not None else None
            ),
            "output_refs": [reference.to_dict() for reference in self.output_refs],
            "grade_report_ref": (
                self.grade_report_ref.to_dict() if self.grade_report_ref is not None else None
            ),
            "failure_ref": self.failure_ref.to_dict() if self.failure_ref is not None else None,
            "parent_run_id": self.parent_run_id,
            "branch_id": self.branch_id,
            "checksums_ref": self.checksums_ref.to_dict(),
        }
        return json.loads(canonical_json(value))

    def _validate_invariants(self) -> None:
        _digest(self.run_id, "run_id")
        _digest(self.content_sha256, "content_sha256")
        if self.lifecycle_status not in _RUN_STATUS:
            raise ValueError("run lifecycle status is invalid")
        if content_digest(self._raw_dict()) != self.content_sha256:
            raise ValueError("run manifest content hash mismatch")
        if self.run_id != self.execution_fingerprint.run_id:
            raise ValueError("run identity does not match execution fingerprint")
        if self.lifecycle_status in {"created", "running"} and self.completed_at is not None:
            raise ValueError("non-terminal run cannot have completed_at")
        if self.lifecycle_status in {"completed", "failed"} and self.completed_at is None:
            raise ValueError("terminal run must have completed_at")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("run completion cannot precede start")
        if self.lifecycle_status == "completed":
            if self.trace_ref is None or self.grade_report_ref is None:
                raise ValueError("completed run must reference trace and grade report")
            if not self.output_refs:
                raise ValueError("completed run must reference at least one output")
            if self.failure_ref is not None:
                raise ValueError("completed run cannot reference a failure record")
        if self.lifecycle_status == "failed" and (
            self.trace_ref is None or self.failure_ref is None
        ):
            raise ValueError("failed run must reference trace and failure record")

    def to_dict(self) -> dict[str, object]:
        self._validate_invariants()
        value = self._raw_dict()
        validate_document("run-manifest", value)
        return value


def _media_type(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".jsonl":
        return "application/x-ndjson"
    return "application/octet-stream"


def artifact_ref_for_file(
    artifact_root: str | Path,
    path: str | Path,
    kind: str,
    schema_id: str | None,
) -> ArtifactRef:
    root = ArtifactRoot(Path(artifact_root))
    resolved = Path(path).resolve(strict=True)
    try:
        relative = resolved.relative_to(root.path).as_posix()
    except ValueError as exc:
        raise ValueError("artifact file is outside the artifact root") from exc
    uri = f"tracelane://artifacts/{relative}"
    if root.resolve(uri) != resolved:
        raise ValueError("artifact file path is not portable")
    data = secure_read_bytes(resolved, root=root.path, label="artifact file")
    value: dict[str, object] = {
        "kind": kind,
        "uri": uri,
        "media_type": _media_type(resolved),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    if schema_id is not None:
        value["schema_id"] = schema_id
    return ArtifactRef.from_dict(value)


def _authoritative_run_files(run_dir: Path) -> tuple[Path, ...]:
    run_dir = Path(run_dir).resolve(strict=True)
    excluded = {"manifest.json", "checksums.json"}
    paths = tuple(
        path.resolve(strict=True)
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and path.relative_to(run_dir).as_posix() not in excluded
    )
    _record_file_identities(paths)
    return paths


def _file_identity(path: Path) -> tuple[int, int] | None:
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_ino == 0:
        return None
    return metadata.st_dev, metadata.st_ino


def _record_file_identities(paths: tuple[Path, ...] | list[Path]) -> dict[Path, tuple[int, int]]:
    identities: dict[Path, tuple[int, int]] = {}
    seen: dict[tuple[int, int], Path] = {}
    for path in paths:
        identity = _file_identity(path)
        if identity is None:
            continue
        previous = seen.get(identity)
        if previous is not None and previous != path:
            raise ValueError(
                f"run files share the same file identity (hard link): {previous.name}, {path.name}"
            )
        seen[identity] = path
        identities[path] = identity
    return identities


def _read_stable_bytes(path: Path, expected_identity: tuple[int, int] | None = None) -> bytes:
    identity = _file_identity(path)
    if expected_identity is not None and identity != expected_identity:
        raise ValueError(f"file identity changed while validating: {path.name}")
    data = secure_read_bytes(path, label="run artifact")
    if _file_identity(path) != identity:
        raise ValueError(f"file changed while validating: {path.name}")
    return data


def write_checksums(run_dir: Path) -> ArtifactRef:
    supplied_run_dir = Path(run_dir)
    assert_safe_tree(supplied_run_dir)
    run_dir = supplied_run_dir.resolve(strict=True)
    if run_dir.parent.name != "runs" or not _SHA256.fullmatch(run_dir.name):
        raise ValueError("run directory must be artifacts/runs/<run-id>")
    artifact_root = run_dir.parents[1]
    checksums_path = run_dir / "checksums.json"
    if checksums_path.exists():
        raise ValueError("run checksums are already finalized")

    entries: list[dict[str, object]] = []
    authoritative_paths = _authoritative_run_files(run_dir)
    identities = _record_file_identities(authoritative_paths)
    for path in authoritative_paths:
        try:
            relative = path.relative_to(run_dir).as_posix()
        except ValueError as exc:
            raise ValueError("authoritative file is outside the run directory") from exc
        data = _read_stable_bytes(path, identities.get(path))
        entries.append(
            {
                "uri": f"tracelane://artifacts/runs/{run_dir.name}/{relative}",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    value: dict[str, object] = {
        "schema_id": "tracelane://schemas/checksums/v2",
        "schema_version": "2.0.0",
        "entries": entries,
        "root_sha256": sha256_json(entries),
        "content_sha256": "",
    }
    value["content_sha256"] = content_digest(value)
    validate_document("checksums", value)
    payload = (canonical_json(value) + "\n").encode("utf-8")
    path = _write_new_bytes(
        checksums_path,
        payload,
        "run checksums",
        root=artifact_root,
    )
    return artifact_ref_for_file(
        artifact_root,
        path,
        "checksums",
        "tracelane://schemas/checksums/v2",
    )


def _write_new_bytes(
    target: Path,
    data: bytes,
    label: str,
    *,
    root: Path,
) -> Path:
    try:
        storage.atomic_create_bytes(
            target,
            data,
            root=root,
            label=label,
        )
    except FileExistsError as exc:
        raise ValueError(f"{label} are already finalized") from exc
    except ValueError as exc:
        if "already exists" in str(exc):
            raise ValueError(f"{label} are already finalized") from exc
        raise
    return target


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(secure_read_bytes(path, label="JSON artifact").decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"artifact is not valid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"artifact must be a JSON object: {path.name}")
    return value


def _read_json_bytes(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"artifact is not valid JSON: {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"artifact must be a JSON object: {label}")
    return value


def _normalized_artifact_uri(root: ArtifactRoot, path: Path) -> str:
    return f"tracelane://artifacts/{path.relative_to(root.path).as_posix()}"


def _resolve_run_reference(
    root: ArtifactRoot,
    run_dir: Path,
    reference: ArtifactRef,
    label: str,
    authenticated_files: Mapping[Path, bytes],
) -> tuple[Path, bytes]:
    path = root.resolve(reference.uri)
    if reference.uri != _normalized_artifact_uri(root, path):
        raise ValueError(f"{label} URI is not normalized")
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError(f"{label} reference is outside the run directory") from exc
    try:
        data = authenticated_files[path]
    except KeyError as exc:
        raise ValueError(f"{label} is missing from checksum coverage") from exc
    if len(data) != reference.size_bytes:
        raise ValueError(f"{label} size mismatch")
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ValueError(f"{label} digest mismatch")
    return path, data


def _validate_json_domain(
    data: bytes,
    *,
    label: str,
    expected_kind: str,
    expected_schema: str,
) -> object:
    value = _read_json_bytes(data, label)
    if expected_schema == "tracelane://schemas/case/v2":
        return HistoryCase.from_dict(value)
    if expected_schema == "tracelane://schemas/evidence-manifest/v2":
        return EvidenceManifest.from_dict(value)
    if expected_schema != "tracelane://schemas/object-envelope/v2":
        raise ValueError(f"{label} schema has no semantic validator")
    validate_document("object-envelope", value)
    if content_digest(value) != value["content_sha256"]:
        raise ValueError(f"{label} content hash mismatch")
    if value["schema_id"] != _OBJECT_SCHEMA_BY_KIND[expected_kind]:
        raise ValueError(f"{label} document schema mismatch")
    return value


def _validate_reference_policy(
    reference: ArtifactRef,
    label: str,
    expected_kind: str,
    expected_schema: str,
    expected_media_type: str,
) -> None:
    if reference.kind != expected_kind:
        raise ValueError(f"{label} kind mismatch")
    if reference.schema_id != expected_schema:
        raise ValueError(f"{label} schema mismatch")
    if reference.media_type != expected_media_type:
        raise ValueError(f"{label} media type mismatch")


def _manifest_run_references(
    manifest: RunManifest,
) -> tuple[tuple[str, ArtifactRef, str, str, str], ...]:
    references: list[tuple[str, ArtifactRef, str, str, str]] = []
    for field_name, policy in _RUN_SINGLE_REFERENCE_SLOTS.items():
        reference = getattr(manifest, field_name)
        if reference is not None:
            references.append((field_name.removesuffix("_ref"), reference, *policy))
    for field_name, policy in _RUN_SEQUENCE_REFERENCE_SLOTS.items():
        label = field_name.removesuffix("_refs")
        references.extend(
            (f"{label}[{index}]", reference, *policy)
            for index, reference in enumerate(getattr(manifest, field_name))
        )
    return tuple(references)


def _resolve_safe_run_dir(run_dir: Path) -> Path:
    supplied_run_dir = Path(run_dir)
    assert_safe_tree(supplied_run_dir)
    resolved = supplied_run_dir.resolve(strict=True)
    if resolved.parent.name != "runs" or not _SHA256.fullmatch(resolved.name):
        raise ValueError("run directory must be artifacts/runs/<run-id>")
    return resolved


def write_run_manifest(run_dir: Path, manifest: RunManifest) -> Path:
    """Finalize a run manifest once through the supported filesystem API.

    This prevents accidental or normal-API rewrites. Coordinated direct filesystem
    replacement of every self-consistent run artifact is outside the local trust model.
    """

    run_dir = _resolve_safe_run_dir(run_dir)
    if manifest.lifecycle_status not in _TERMINAL_RUN_STATUS:
        raise ValueError("only a terminal run manifest (completed or failed) can be finalized")
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        raise ValueError("run manifest is already finalized")
    value = manifest.to_dict()
    _validate_run_contents(run_dir, manifest)
    payload = (canonical_json(value) + "\n").encode("utf-8")
    return _write_new_bytes(
        manifest_path,
        payload,
        "run manifest",
        root=run_dir.parents[1],
    )


def validate_run(run_dir: Path) -> None:
    run_dir = _resolve_safe_run_dir(run_dir)
    manifest_data = _read_stable_bytes(run_dir / "manifest.json")
    manifest = RunManifest.from_dict(_read_json_bytes(manifest_data, "manifest.json"))
    _validate_run_contents(run_dir, manifest)


def _validate_run_contents(run_dir: Path, manifest: RunManifest) -> None:
    if run_dir.parent.name != "runs" or not _SHA256.fullmatch(run_dir.name):
        raise ValueError("run directory must be artifacts/runs/<run-id>")
    artifact_root = run_dir.parents[1]
    root = ArtifactRoot(artifact_root)

    if manifest.checksums_ref.kind != "checksums":
        raise ValueError("run checksums reference kind mismatch")
    if manifest.checksums_ref.schema_id != "tracelane://schemas/checksums/v2":
        raise ValueError("run checksums reference schema mismatch")
    if manifest.checksums_ref.media_type != "application/json":
        raise ValueError("run checksums reference media type mismatch")
    checksums_path = root.resolve(manifest.checksums_ref.uri)
    if checksums_path != run_dir / "checksums.json":
        raise ValueError("run checksums reference is invalid")
    if manifest.checksums_ref.uri != _normalized_artifact_uri(root, checksums_path):
        raise ValueError("run checksums reference URI is not normalized")
    try:
        checksums_data = _read_stable_bytes(checksums_path)
    except OSError as exc:
        raise ValueError("run checksums artifact is unavailable") from exc
    if (
        len(checksums_data) != manifest.checksums_ref.size_bytes
        or hashlib.sha256(checksums_data).hexdigest() != manifest.checksums_ref.sha256
    ):
        raise ValueError("run checksums reference hash mismatch")

    checksums = _read_json_bytes(checksums_data, checksums_path.name)
    validate_document("checksums", checksums)
    if content_digest(checksums) != checksums["content_sha256"]:
        raise ValueError("run checksums content hash mismatch")
    entries = checksums["entries"]
    if sha256_json(entries) != checksums["root_sha256"]:
        raise ValueError("run checksums root mismatch")
    seen_uris: set[str] = set()
    seen_paths: set[Path] = set()
    checksum_paths: list[Path] = []
    for entry in entries:  # type: ignore[union-attr]
        uri = str(entry["uri"])
        if uri in seen_uris:
            raise ValueError("run checksums contain duplicate URIs")
        seen_uris.add(uri)
        path = root.resolve(uri)
        try:
            relative = path.relative_to(run_dir)
        except ValueError as exc:
            raise ValueError("run checksum entry escapes the run directory") from exc
        normalized_uri = f"tracelane://artifacts/runs/{run_dir.name}/{relative.as_posix()}"
        if uri != normalized_uri:
            raise ValueError("run checksum URI is not normalized")
        if path in seen_paths:
            raise ValueError("run checksums contain duplicate paths")
        seen_paths.add(path)
        checksum_paths.append(path)
        if path in {run_dir / "manifest.json", run_dir / "checksums.json"}:
            raise ValueError("run checksum entry creates a hash cycle")

    authoritative_paths = _authoritative_run_files(run_dir)
    if set(checksum_paths) != set(authoritative_paths):
        raise ValueError("run checksum coverage does not match authoritative files")
    checksum_identities = _record_file_identities(checksum_paths)

    authenticated_files: dict[Path, bytes] = {}
    for entry, path in zip(entries, checksum_paths, strict=True):  # type: ignore[arg-type]
        try:
            data = _read_stable_bytes(path, checksum_identities.get(path))
        except OSError as exc:
            raise ValueError(f"run checksum file is unavailable: {entry['uri']}") from exc
        if len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"run checksum mismatch: {entry['uri']}")
        authenticated_files[path] = data

    covered = set(checksum_paths)
    input_documents: dict[str, object] = {}
    for slot, (
        expected_kind,
        expected_schema,
        expected_media_type,
        digest_field,
    ) in _RUN_INPUT_SLOTS.items():
        reference = getattr(manifest, slot)
        label = slot.removesuffix("_ref").replace("_", " ")
        _validate_reference_policy(
            reference,
            label,
            expected_kind,
            expected_schema,
            expected_media_type,
        )
        path, data = _resolve_run_reference(
            root,
            run_dir,
            reference,
            label,
            authenticated_files,
        )
        if path not in covered:
            raise ValueError(f"{label} is missing from checksum coverage")
        if hashlib.sha256(data).hexdigest() != getattr(
            manifest.execution_fingerprint, digest_field
        ):
            raise ValueError(f"{label} digest does not match execution fingerprint")
        input_documents[slot] = _validate_json_domain(
            data,
            label=label,
            expected_kind=expected_kind,
            expected_schema=expected_schema,
        )

    case = input_documents["case_ref"]
    evidence_manifest = input_documents["evidence_manifest_ref"]
    if not isinstance(case, HistoryCase) or not isinstance(evidence_manifest, EvidenceManifest):
        raise ValueError("run input domain documents are invalid")
    if case.case_id != evidence_manifest.case_id or case.cutoff_at != evidence_manifest.cutoff_at:
        raise ValueError("run case and evidence manifest domain mismatch")
    case_evidence_identity = (
        case.evidence_manifest_ref.kind,
        case.evidence_manifest_ref.schema_id,
        case.evidence_manifest_ref.media_type,
        case.evidence_manifest_ref.size_bytes,
        case.evidence_manifest_ref.sha256,
    )
    run_evidence_identity = (
        manifest.evidence_manifest_ref.kind,
        manifest.evidence_manifest_ref.schema_id,
        manifest.evidence_manifest_ref.media_type,
        manifest.evidence_manifest_ref.size_bytes,
        manifest.evidence_manifest_ref.sha256,
    )
    if case_evidence_identity != run_evidence_identity:
        raise ValueError("run case evidence manifest identity binding mismatch")

    for (
        label,
        reference,
        expected_kind,
        expected_schema,
        expected_media_type,
    ) in _manifest_run_references(manifest):
        _validate_reference_policy(
            reference,
            label,
            expected_kind,
            expected_schema,
            expected_media_type,
        )
        path, data = _resolve_run_reference(
            root,
            run_dir,
            reference,
            label,
            authenticated_files,
        )
        if path not in covered:
            raise ValueError(f"{label} is missing from checksum coverage")
        if expected_schema == "tracelane://schemas/trace-event/v2":
            events = read_trace_bytes(data, expected_run_id=manifest.run_id)
            if not events:
                raise ValueError("terminal run trace must be non-empty")
            started = [event for event in events if event.event_type == "run.started"]
            completed = [event for event in events if event.event_type == "run.completed"]
            if len(started) != 1 or events[0] is not started[0]:
                raise ValueError("terminal run trace must contain exactly one initial run.started")
            if manifest.lifecycle_status == "completed":
                if len(completed) != 1 or events[-1] is not completed[0]:
                    raise ValueError(
                        "completed run trace must contain exactly one final run.completed"
                    )
            elif completed:
                raise ValueError("failed run trace must not contain run.completed")
        else:
            _validate_json_domain(
                data,
                label=label,
                expected_kind=expected_kind,
                expected_schema=expected_schema,
            )

    if manifest.run_id != run_dir.name:
        raise ValueError("run manifest identity mismatch")
