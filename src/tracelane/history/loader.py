from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from tracelane.acquisition import CandidateReview, EvidenceCandidate
from tracelane.security import assert_safe_tree
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.schema import validate_document
from tracelane.v2.source import source_locator_sha256
from tracelane.v2.storage import (
    ArtifactRoot,
    atomic_create_bytes,
    secure_read_bytes,
)

from .contracts import (
    EvidenceManifest,
    EvidenceRecordV2,
    FrozenHistoryBundle,
    HistoryCase,
    HistoryScenarioEntry,
)

_FIXTURE_PREFIX = "tracelane://fixtures/v0.2/"
_ARTIFACT_PREFIX = "tracelane://artifacts/"


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        data = secure_read_bytes(path, label="fixture file")
    except ValueError as exc:
        raise ValueError(f"fixture is not valid JSON: {path.name}") from exc
    return _read_json_bytes(data, path.name)


def _read_json_bytes(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"fixture is not valid JSON: {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"fixture must be a JSON object: {label}")
    return value


def _resolve_and_verify_fixture_bytes(
    root: Path,
    reference: ArtifactRef,
) -> tuple[Path, bytes]:
    root = Path(root).resolve(strict=True)
    uri = reference.uri
    if uri.startswith(_ARTIFACT_PREFIX):
        try:
            candidate = ArtifactRoot(root).resolve(uri)
        except ValueError as exc:
            raise ValueError("fixture reference escapes its root") from exc
    elif uri.startswith(_FIXTURE_PREFIX):
        raw_relative = uri.removeprefix(_FIXTURE_PREFIX)
        if "\\" in raw_relative or "%" in raw_relative:
            raise ValueError("fixture reference escapes its root")
        relative = PurePosixPath(raw_relative)
        if (
            not relative.parts
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_relative.split("/"))
        ):
            raise ValueError("fixture reference escapes its root")
        candidate = (root / Path(*relative.parts)).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("fixture reference escapes its root") from exc
        current = root
        for part in relative.parts:
            current /= part
            if current.exists() and _is_link_or_reparse(current):
                raise ValueError("fixture reference contains a link or reparse point")
    else:
        raise ValueError("fixture reference URI has the wrong root")
    try:
        data = secure_read_bytes(
            candidate,
            root=root,
            label="fixture reference",
        )
    except ValueError as exc:
        raise ValueError("fixture reference is unavailable") from exc
    if len(data) != reference.size_bytes:
        raise ValueError("fixture reference size mismatch")
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ValueError("fixture reference hash mismatch")
    return candidate, data


def _resolve_fixture_reference(
    root: Path,
    reference: ArtifactRef,
    *,
    expected_kind: str,
    expected_schema_id: str | None,
) -> tuple[Path, bytes]:
    if reference.kind != expected_kind:
        raise ValueError("fixture reference kind mismatch")
    if reference.schema_id != expected_schema_id:
        raise ValueError("fixture reference schema mismatch")
    return _resolve_and_verify_fixture_bytes(root, reference)


def resolve_fixture_ref(
    root: Path,
    reference: ArtifactRef,
    *,
    expected_kind: str,
    expected_schema_id: str | None,
) -> Path:
    path, _data = _resolve_fixture_reference(
        root,
        reference,
        expected_kind=expected_kind,
        expected_schema_id=expected_schema_id,
    )
    return path


def _find_fixture_root(path: Path) -> Path | None:
    for ancestor in path.parents:
        if (
            (ancestor / "manifest.json").is_file()
            and (ancestor / "splits").is_dir()
            and (ancestor / "history").is_dir()
        ):
            return ancestor.resolve(strict=True)
    return None


def load_history_case(path: Path) -> HistoryCase:
    resolved = Path(path).resolve(strict=True)
    return HistoryCase.from_dict(
        _read_json_bytes(
            secure_read_bytes(resolved, label="history case"),
            resolved.name,
        )
    )


def load_evidence_manifest(path: Path) -> EvidenceManifest:
    resolved = Path(path).resolve(strict=True)
    return EvidenceManifest.from_dict(
        _read_json_bytes(
            secure_read_bytes(resolved, label="evidence manifest"),
            resolved.name,
        ),
        fixture_root=_find_fixture_root(resolved),
        source_path=resolved,
    )


def load_history_suite(root: Path, split: str) -> tuple[HistoryScenarioEntry, ...]:
    root = Path(root).resolve(strict=True)
    assert_safe_tree(root)
    manifest = _read_json_object(root / "manifest.json")
    validate_document("suite-manifest", manifest)
    if content_digest(manifest) != manifest["content_sha256"]:
        raise ValueError("suite manifest content hash mismatch")
    splits = manifest["splits"]
    if not isinstance(splits, Mapping) or split not in splits:
        raise ValueError("suite split is not declared")
    split_value = splits[split]
    if not isinstance(split_value, Mapping):
        raise ValueError("suite split reference is invalid")
    split_path, split_data = _resolve_fixture_reference(
        root,
        ArtifactRef.from_dict(split_value),
        expected_kind="suite_split",
        expected_schema_id="tracelane://schemas/suite-split/v2",
    )
    split_document = _read_json_bytes(split_data, split_path.name)
    validate_document("suite-split", split_document)
    if split_document["split"] != split:
        raise ValueError("suite split identity does not match")
    scenario_ids = tuple(
        str(item)
        for item in split_document["scenario_ids"]  # type: ignore[union-attr]
    )
    if len(scenario_ids) != len(set(scenario_ids)):
        raise ValueError("suite split contains duplicate scenarios")
    scenario_values = manifest["scenarios"]
    if not isinstance(scenario_values, list):
        raise ValueError("suite scenarios must be a list")
    by_id: dict[str, Mapping[str, object]] = {}
    for item in scenario_values:
        if not isinstance(item, Mapping):
            raise ValueError("suite scenario must be an object")
        scenario_id = str(item["scenario_id"])
        if scenario_id in by_id:
            raise ValueError("suite contains duplicate scenario identities")
        by_id[scenario_id] = item
    if set(scenario_ids) - set(by_id):
        raise ValueError("suite split references unknown scenarios")
    entries = tuple(
        HistoryScenarioEntry.from_dict(by_id[scenario_id], fixture_root=root)
        for scenario_id in scenario_ids
    )
    for entry in entries:
        case_path, case_data = _resolve_fixture_reference(
            root,
            entry.case_ref,
            expected_kind="history_case",
            expected_schema_id="tracelane://schemas/case/v2",
        )
        manifest_path, manifest_data = _resolve_fixture_reference(
            root,
            entry.evidence_manifest_ref,
            expected_kind="evidence_manifest",
            expected_schema_id="tracelane://schemas/evidence-manifest/v2",
        )
        case = HistoryCase.from_dict(_read_json_bytes(case_data, case_path.name))
        evidence_manifest = EvidenceManifest.from_dict(
            _read_json_bytes(manifest_data, manifest_path.name),
            fixture_root=root,
            source_path=manifest_path,
        )
        if entry.case_id != case.case_id:
            raise ValueError("suite entry case identity does not match loaded case")
        if entry.evidence_manifest_ref != case.evidence_manifest_ref:
            raise ValueError("suite entry evidence manifest reference does not match loaded case")
        if evidence_manifest.case_id != case.case_id:
            raise ValueError("suite entry evidence manifest identity does not match loaded case")
    return entries


def _validate_record_objects(
    record: EvidenceRecordV2,
    candidate: EvidenceCandidate,
    review: CandidateReview,
) -> None:
    if record.candidate_ref.kind != "evidence_candidate":
        raise ValueError("history evidence candidate lineage metadata mismatch")
    if record.review_ref.kind != "candidate_review":
        raise ValueError("history evidence review lineage metadata mismatch")
    if (
        record.candidate_ref.schema_id != "tracelane://schemas/evidence-candidate/v2"
        or record.review_ref.schema_id != "tracelane://schemas/candidate-review/v2"
    ):
        raise ValueError("history evidence lineage schema mismatch")
    if (
        review.candidate_id,
        review.candidate_record_sha256,
        review.candidate_content_sha256,
        review.source_locator_sha256,
        review.document_date,
        review.date_precision,
    ) != (
        candidate.candidate_id,
        candidate.record_sha256,
        candidate.content_sha256,
        source_locator_sha256(candidate.source_url),
        candidate.document_date,
        candidate.date_precision,
    ):
        raise ValueError("history evidence review candidate lineage mismatch")
    if review.decision != "approved":
        raise ValueError("history evidence review is not approved")
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
        raise ValueError("history evidence cross-lineage mismatch")


def _validate_record_lineage(
    root: Path,
    record: EvidenceRecordV2,
) -> None:
    candidate_path, candidate_data = _resolve_fixture_reference(
        root,
        record.candidate_ref,
        expected_kind="evidence_candidate",
        expected_schema_id="tracelane://schemas/evidence-candidate/v2",
    )
    review_path, review_data = _resolve_fixture_reference(
        root,
        record.review_ref,
        expected_kind="candidate_review",
        expected_schema_id="tracelane://schemas/candidate-review/v2",
    )
    candidate = EvidenceCandidate.from_dict(_read_json_bytes(candidate_data, candidate_path.name))
    review = CandidateReview.from_dict(_read_json_bytes(review_data, review_path.name))
    _validate_record_objects(record, candidate, review)


def _authenticate_artifact_bytes(
    root: ArtifactRoot,
    reference: ArtifactRef,
    *,
    expected_kind: str,
    expected_schema_id: str | None,
    expected_media_type: str | None,
    label: str,
) -> bytes:
    if reference.kind != expected_kind:
        raise ValueError(f"{label} kind is invalid")
    if reference.schema_id != expected_schema_id:
        raise ValueError(f"{label} schema is invalid")
    if expected_media_type is not None and reference.media_type != expected_media_type:
        raise ValueError(f"{label} media type is invalid")
    path = root.resolve(reference.uri)
    try:
        data = secure_read_bytes(
            path,
            root=root.path,
            label=label,
        )
    except ValueError as exc:
        raise ValueError(f"{label} is unavailable") from exc
    if len(data) != reference.size_bytes:
        raise ValueError(f"{label} size is invalid")
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ValueError(f"{label} hash is invalid")
    return data


def authenticate_promoted_closure(
    root: ArtifactRoot,
    record_ref: ArtifactRef,
) -> Sequence[tuple[ArtifactRef, bytes]]:
    record_data = _authenticate_artifact_bytes(
        root,
        record_ref,
        expected_kind="evidence_record",
        expected_schema_id="tracelane://schemas/evidence-record/v2",
        expected_media_type="application/json",
        label="promoted evidence record",
    )
    record = EvidenceRecordV2.from_dict(_read_json_bytes(record_data, "promoted evidence record"))
    candidate_data = _authenticate_artifact_bytes(
        root,
        record.candidate_ref,
        expected_kind="evidence_candidate",
        expected_schema_id="tracelane://schemas/evidence-candidate/v2",
        expected_media_type="application/json",
        label="promoted evidence candidate",
    )
    candidate = EvidenceCandidate.from_dict(
        _read_json_bytes(candidate_data, "promoted evidence candidate")
    )
    review_data = _authenticate_artifact_bytes(
        root,
        record.review_ref,
        expected_kind="candidate_review",
        expected_schema_id="tracelane://schemas/candidate-review/v2",
        expected_media_type="application/json",
        label="promoted evidence review",
    )
    review = CandidateReview.from_dict(_read_json_bytes(review_data, "promoted evidence review"))
    _validate_record_objects(record, candidate, review)
    content_data = _authenticate_artifact_bytes(
        root,
        record.content_ref,
        expected_kind="evidence_blob",
        expected_schema_id=None,
        expected_media_type=None,
        label="promoted evidence content",
    )
    transformations = tuple(
        (
            reference,
            _authenticate_artifact_bytes(
                root,
                reference,
                expected_kind="evidence_transformation",
                expected_schema_id=None,
                expected_media_type=None,
                label="promoted evidence transformation",
            ),
        )
        for reference in record.transformation_refs
    )
    return (
        (record_ref, record_data),
        (record.candidate_ref, candidate_data),
        (record.review_ref, review_data),
        (record.content_ref, content_data),
        *transformations,
    )


def publish_identical_artifact(
    root: ArtifactRoot,
    reference: ArtifactRef,
    data: bytes,
) -> None:
    if len(data) != reference.size_bytes:
        raise ValueError("archive artifact size is invalid")
    if hashlib.sha256(data).hexdigest() != reference.sha256:
        raise ValueError("archive artifact hash is invalid")
    path = root.resolve(reference.uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = root.resolve(reference.uri)
    if path.exists():
        existing = secure_read_bytes(
            path,
            root=root.path,
            label="archive target artifact",
        )
        if existing != data:
            raise ValueError("archive target artifact conflicts with preserved bytes")
        return
    try:
        atomic_create_bytes(
            path,
            data,
            root=root.path,
            label="archive target artifact",
        )
    except ValueError as exc:
        if "already exists" not in str(exc):
            raise
        existing = secure_read_bytes(
            path,
            root=root.path,
            label="archive target artifact",
        )
        if existing != data:
            raise ValueError("archive target artifact conflicts with preserved bytes") from exc
    published = secure_read_bytes(
        path,
        root=root.path,
        label="archive target artifact",
    )
    if published != data:
        raise ValueError("archive target artifact bytes changed during publication")


def archive_promoted_evidence(
    source_root: str | Path,
    target_root: str | Path,
    record_ref: ArtifactRef,
) -> ArtifactRef:
    source = ArtifactRoot(Path(source_root))
    source_documents = authenticate_promoted_closure(source, record_ref)
    target = ArtifactRoot(Path(target_root))
    for reference, data in source_documents:
        publish_identical_artifact(target, reference, data)
    authenticate_promoted_closure(target, record_ref)
    return record_ref


def freeze_history_evidence(
    case: HistoryCase,
    manifest: EvidenceManifest,
) -> FrozenHistoryBundle:
    case = HistoryCase.from_dict(case.to_dict())
    if case.case_id != manifest.case_id:
        raise ValueError("case and evidence manifest identities do not match")
    if case.cutoff_at != manifest.cutoff_at:
        raise ValueError("case and evidence cutoff do not match")
    if manifest.fixture_root is None:
        raise ValueError("evidence manifest is not attached to a fixture root")
    try:
        manifest_path, manifest_data = _resolve_fixture_reference(
            manifest.fixture_root,
            case.evidence_manifest_ref,
            expected_kind="evidence_manifest",
            expected_schema_id="tracelane://schemas/evidence-manifest/v2",
        )
    except ValueError as exc:
        raise ValueError("case evidence manifest reference does not match loaded manifest") from exc
    if manifest.source_path is None or manifest_path != manifest.source_path:
        raise ValueError("case evidence manifest reference does not match loaded manifest")
    if _read_json_bytes(manifest_data, manifest_path.name) != manifest.to_dict():
        raise ValueError("case evidence manifest reference does not match loaded manifest")
    records: list[EvidenceRecordV2] = []
    for reference in manifest.record_refs:
        record_path, record_data = _resolve_fixture_reference(
            manifest.fixture_root,
            reference,
            expected_kind="evidence_record",
            expected_schema_id="tracelane://schemas/evidence-record/v2",
        )
        record = EvidenceRecordV2.from_dict(_read_json_bytes(record_data, record_path.name))
        _validate_record_lineage(manifest.fixture_root, record)
        if record.available_at > case.cutoff_at:
            raise ValueError("admitted evidence is after decision cutoff")
        if record.known_by_cutoff == "unavailable":
            raise ValueError("unavailable evidence cannot be admitted")
        resolve_fixture_ref(
            manifest.fixture_root,
            record.content_ref,
            expected_kind="evidence_blob",
            expected_schema_id=None,
        )
        for transformation in record.transformation_refs:
            resolve_fixture_ref(
                manifest.fixture_root,
                transformation,
                expected_kind="evidence_transformation",
                expected_schema_id=None,
            )
        records.append(record)
    evidence_ids = [record.evidence_id for record in records]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence manifest resolves duplicate evidence IDs")
    rejected_records: list[EvidenceRecordV2] = []
    for reference in manifest.rejected_future_refs:
        record_path, record_data = _resolve_fixture_reference(
            manifest.fixture_root,
            reference,
            expected_kind="evidence_record",
            expected_schema_id="tracelane://schemas/evidence-record/v2",
        )
        record = EvidenceRecordV2.from_dict(_read_json_bytes(record_data, record_path.name))
        _validate_record_lineage(manifest.fixture_root, record)
        if record.available_at <= case.cutoff_at:
            raise ValueError("rejected future evidence is not after decision cutoff")
        if record.known_by_cutoff != "unavailable":
            raise ValueError("rejected future evidence must be unavailable by cutoff")
        resolve_fixture_ref(
            manifest.fixture_root,
            record.content_ref,
            expected_kind="evidence_blob",
            expected_schema_id=None,
        )
        for transformation in record.transformation_refs:
            resolve_fixture_ref(
                manifest.fixture_root,
                transformation,
                expected_kind="evidence_transformation",
                expected_schema_id=None,
            )
        rejected_records.append(record)
    rejected_ids = [record.evidence_id for record in rejected_records]
    if len(rejected_ids) != len(set(rejected_ids)):
        raise ValueError("evidence manifest resolves duplicate rejected evidence IDs")
    if set(evidence_ids) & set(rejected_ids):
        raise ValueError("admitted and rejected evidence IDs must be disjoint")
    for transformation in manifest.transformation_refs:
        resolve_fixture_ref(
            manifest.fixture_root,
            transformation,
            expected_kind="evidence_transformation",
            expected_schema_id=None,
        )
    declared_transformations = {item.uri: item for item in manifest.transformation_refs}
    used_transformations = {
        item.uri: item
        for record in (*records, *rejected_records)
        for item in record.transformation_refs
    }
    if declared_transformations != used_transformations:
        raise ValueError("evidence transformation references are inconsistent")
    expected_licenses = {record.evidence_id: record.license for record in records}
    if dict(manifest.source_licenses) != expected_licenses:
        raise ValueError("source license map must exactly cover admitted evidence")
    return FrozenHistoryBundle(
        case_id=case.case_id,
        cutoff_at=case.cutoff_at,
        records=tuple(records),
        rejected_future_ids=tuple(rejected_ids),
        bundle_sha256=manifest.bundle_sha256,
    )
