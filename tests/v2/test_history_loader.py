from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracelane import history as history_module
from tracelane.acquisition import (
    CandidateReview,
    EvidenceCandidate,
    ManualAcquisitionService,
)
from tracelane.acquisition.contracts import compute_candidate_id
from tracelane.contracts import canonical_json
from tracelane.history import loader as history_loader_module
from tracelane.history.contracts import (
    EvidenceRecordV2,
    compute_evidence_provenance_sha256,
    compute_history_bundle_sha256,
)
from tracelane.history.loader import (
    freeze_history_evidence,
    load_evidence_manifest,
    load_history_case,
    load_history_suite,
)
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.source import source_locator_sha256
from tracelane.v2.storage import ArtifactRoot, BlobStore

CUTOFF = "1812-06-23T23:59:59Z"
NOW = datetime(2026, 7, 24, tzinfo=UTC)


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    return path


def fixture_ref(
    root: Path,
    path: Path,
    kind: str,
    schema_id: str | None = None,
) -> dict[str, object]:
    data = path.read_bytes()
    value: dict[str, object] = {
        "kind": kind,
        "uri": f"tracelane://fixtures/v0.2/{path.relative_to(root).as_posix()}",
        "media_type": "application/json" if path.suffix == ".json" else "text/plain",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }
    if schema_id is not None:
        value["schema_id"] = schema_id
    return value


def build_suite(
    root: Path,
    *,
    admitted_available_at: str = "1812-05-31T23:59:59Z",
    admitted_content_kind: str = "evidence_blob",
    admitted_content_schema_id: str | None = None,
    admitted_record_kind: str = "evidence_record",
    admitted_record_schema_id: str | None = "tracelane://schemas/evidence-record/v2",
    admitted_uses_transformation: bool = False,
    manifest_license: str = "Public-Domain",
    evidence_manifest_case_id: str = "hist-001",
    rejected_available_at: str | None = None,
    rejected_content_kind: str = "evidence_blob",
    rejected_content_schema_id: str | None = None,
    rejected_evidence_id: str = "hist-001-ev-0002",
    rejected_known_by_cutoff: str = "unavailable",
    rejected_record_kind: str = "evidence_record",
    rejected_record_schema_id: str | None = "tracelane://schemas/evidence-record/v2",
    duplicate_rejected_ref: bool = False,
    rejected_uses_transformation: bool = False,
    declare_transformation: bool = False,
    transformation_kind: str = "evidence_transformation",
    transformation_schema_id: str | None = None,
    manifest_transformation_kind: str = "evidence_transformation",
    manifest_transformation_schema_id: str | None = None,
    entry_case_id: str = "hist-001",
    swap_entry_manifest_ref: bool = False,
) -> None:
    blob_path = root / "history/hist-001/evidence/blobs/source.txt"
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_text("source text", encoding="utf-8")
    content_ref = fixture_ref(
        root,
        blob_path,
        admitted_content_kind,
        admitted_content_schema_id,
    )
    transformation_path = root / "history/hist-001/evidence/transformations/excerpt.txt"
    transformation_path.parent.mkdir(parents=True, exist_ok=True)
    transformation_path.write_text("transformation", encoding="utf-8")
    transformation_ref = fixture_ref(
        root,
        transformation_path,
        transformation_kind,
        transformation_schema_id,
    )
    candidate_transformation_ref = fixture_ref(
        root,
        transformation_path,
        "evidence_transformation",
        None,
    )
    manifest_transformation_ref = fixture_ref(
        root,
        transformation_path,
        manifest_transformation_kind,
        manifest_transformation_schema_id,
    )

    def record_value(
        evidence_id: str,
        *,
        available_at: str,
        known_by_cutoff: str,
        record_content_ref: dict[str, object],
        record_transformation_refs: list[dict[str, object]],
        candidate_transformation_refs: list[dict[str, object]],
    ) -> dict[str, object]:
        parsed_content_ref = ArtifactRef.from_dict(record_content_ref)
        parsed_candidate_transformations = tuple(
            ArtifactRef.from_dict(item) for item in candidate_transformation_refs
        )
        candidate_id = compute_candidate_id(
            query="query",
            title="Primary source",
            source_url="https://history.example/source",
            document_date="1812-05",
            date_precision="month",
            content_sha256=parsed_content_ref.sha256,
        )
        candidate = EvidenceCandidate.create(
            candidate_id=candidate_id,
            query="query",
            title="Primary source",
            source_url="https://history.example/source",
            document_date="1812-05",
            date_precision="month",
            retrieved_at=datetime(2026, 7, 24, tzinfo=UTC),
            curator="curator-001",
            transformation_refs=parsed_candidate_transformations,
            content_ref=parsed_content_ref,
        )
        candidate_path = write_json(
            root / f"history/hist-001/evidence/candidates/{candidate_id}.json",
            candidate.to_dict(),
        )
        candidate_ref = fixture_ref(
            root,
            candidate_path,
            "evidence_candidate",
            "tracelane://schemas/evidence-candidate/v2",
        )
        review = CandidateReview.create(
            candidate,
            decision="approved",
            reviewer="reviewer-001",
            reviewed_at=datetime(2026, 7, 24, tzinfo=UTC),
            available_at=datetime.fromisoformat(available_at.replace("Z", "+00:00")),
            source_type="primary",
            license="Public-Domain",
            reason="approved for fixture",
        )
        review_path = write_json(
            root / f"history/hist-001/evidence/reviews/{candidate_id}.json",
            review.to_dict(),
        )
        review_ref = fixture_ref(
            root,
            review_path,
            "candidate_review",
            "tracelane://schemas/candidate-review/v2",
        )
        value: dict[str, object] = {
            "schema_id": "tracelane://schemas/evidence-record/v2",
            "schema_version": "2.0.0",
            "evidence_id": evidence_id,
            "document_date": "1812-05",
            "date_precision": "month",
            "available_at": available_at,
            "known_by_cutoff": known_by_cutoff,
            "source_type": "primary",
            "source_title": "Primary source",
            "source_locator": "https://history.example/source",
            "source_locator_sha256": source_locator_sha256("https://history.example/source"),
            "curator": "curator-001",
            "candidate_id": candidate.candidate_id,
            "candidate_record_sha256": candidate.record_sha256,
            "review_sha256": review.content_sha256,
            "candidate_ref": candidate_ref,
            "review_ref": review_ref,
            "license": "Public-Domain",
            "excerpt_kind": "paraphrased",
            "content_ref": record_content_ref,
            "fact_ids": ["diplomacy.tilsit"],
            "transformation_refs": record_transformation_refs,
        }
        value["provenance_sha256"] = compute_evidence_provenance_sha256(value)
        return value

    record = record_value(
        "hist-001-ev-0001",
        available_at=admitted_available_at,
        known_by_cutoff="known",
        record_content_ref=content_ref,
        record_transformation_refs=([transformation_ref] if admitted_uses_transformation else []),
        candidate_transformation_refs=(
            [candidate_transformation_ref] if admitted_uses_transformation else []
        ),
    )
    record_path = write_json(
        root / "history/hist-001/evidence/records/hist-001-ev-0001.json",
        record,
    )
    record_ref = fixture_ref(
        root,
        record_path,
        admitted_record_kind,
        admitted_record_schema_id,
    )
    rejected_refs: list[dict[str, object]] = []
    if rejected_available_at is not None:
        rejected_blob_path = root / "history/hist-001/evidence/blobs/future.txt"
        rejected_blob_path.write_text("future source text", encoding="utf-8")
        rejected_content_ref = fixture_ref(
            root,
            rejected_blob_path,
            rejected_content_kind,
            rejected_content_schema_id,
        )
        rejected_record = record_value(
            rejected_evidence_id,
            available_at=rejected_available_at,
            known_by_cutoff=rejected_known_by_cutoff,
            record_content_ref=rejected_content_ref,
            record_transformation_refs=(
                [transformation_ref] if rejected_uses_transformation else []
            ),
            candidate_transformation_refs=(
                [candidate_transformation_ref] if rejected_uses_transformation else []
            ),
        )
        rejected_path = write_json(
            root / "history/hist-001/evidence/records/hist-001-ev-0002.json",
            rejected_record,
        )
        rejected_refs.append(
            fixture_ref(
                root,
                rejected_path,
                rejected_record_kind,
                rejected_record_schema_id,
            )
        )
        if duplicate_rejected_ref:
            rejected_refs.append(rejected_refs[0])
    declared_transformations = [manifest_transformation_ref] if declare_transformation else []
    bundle_sha256 = compute_history_bundle_sha256(
        case_id=evidence_manifest_case_id,
        cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        record_refs=(record_ref,),
        rejected_future_refs=rejected_refs,
        transformation_refs=declared_transformations,
        source_licenses={"hist-001-ev-0001": manifest_license},
    )
    evidence_manifest: dict[str, object] = {
        "schema_id": "tracelane://schemas/evidence-manifest/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "case_id": evidence_manifest_case_id,
        "cutoff_at": CUTOFF,
        "record_refs": [record_ref],
        "rejected_future_refs": rejected_refs,
        "source_licenses": {"hist-001-ev-0001": manifest_license},
        "transformation_refs": declared_transformations,
        "bundle_sha256": bundle_sha256,
    }
    evidence_manifest["content_sha256"] = content_digest(evidence_manifest)
    evidence_manifest_path = write_json(
        root / "history/hist-001/evidence/manifest.json",
        evidence_manifest,
    )
    evidence_manifest_ref = fixture_ref(
        root,
        evidence_manifest_path,
        "evidence_manifest",
        "tracelane://schemas/evidence-manifest/v2",
    )
    entry_evidence_manifest_ref = evidence_manifest_ref
    if swap_entry_manifest_ref:
        alternate_manifest_path = write_json(
            root / "history/hist-001/evidence/manifest-copy.json",
            evidence_manifest,
        )
        entry_evidence_manifest_ref = fixture_ref(
            root,
            alternate_manifest_path,
            "evidence_manifest",
            "tracelane://schemas/evidence-manifest/v2",
        )
    case: dict[str, object] = {
        "schema_id": "tracelane://schemas/case/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "case_id": "hist-001",
        "title": "Napoleon does not invade Russia",
        "decision_maker": "Napoleon Bonaparte",
        "cutoff_at": CUTOFF,
        "intervention": ("Napoleon does not cross the Niemen or launch the Russian campaign."),
        "projection_end": "1815-12-31",
        "minimum_alternatives": 2,
        "minimum_scenario_branches": 3,
        "required_domains": ["diplomacy", "military"],
        "evidence_manifest_ref": evidence_manifest_ref,
        "rubric_refs": [],
    }
    case["content_sha256"] = content_digest(case)
    case_path = write_json(root / "history/hist-001/case.json", case)
    case_ref = fixture_ref(
        root,
        case_path,
        "history_case",
        "tracelane://schemas/case/v2",
    )
    development: dict[str, object] = {
        "schema_id": "tracelane://schemas/suite-split/v2",
        "schema_version": "2.0.0",
        "split": "development",
        "scenario_ids": ["hist-001/clean"],
    }
    development_path = write_json(root / "splits/development.json", development)
    heldout = {
        "schema_id": "tracelane://schemas/suite-split/v2",
        "schema_version": "2.0.0",
        "split": "heldout",
        "scenario_ids": [],
    }
    heldout_path = write_json(root / "splits/heldout.json", heldout)
    suite_manifest: dict[str, object] = {
        "schema_id": "tracelane://schemas/suite-manifest/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "suite_id": "history-v0.2",
        "splits": {
            "development": fixture_ref(
                root,
                development_path,
                "suite_split",
                "tracelane://schemas/suite-split/v2",
            ),
            "heldout": fixture_ref(
                root,
                heldout_path,
                "suite_split",
                "tracelane://schemas/suite-split/v2",
            ),
        },
        "scenarios": [
            {
                "scenario_id": "hist-001/clean",
                "case_id": entry_case_id,
                "case_ref": case_ref,
                "evidence_manifest_ref": entry_evidence_manifest_ref,
                "fault_ref": None,
            }
        ],
    }
    suite_manifest["content_sha256"] = content_digest(suite_manifest)
    write_json(root / "manifest.json", suite_manifest)


def acquire_promoted_evidence(
    root: Path,
) -> tuple[ArtifactRef, EvidenceRecordV2]:
    artifact_root = ArtifactRoot(root)
    transformation_ref = BlobStore(artifact_root).put_bytes(
        b"normalized excerpt",
        "text/plain",
        "evidence_transformation",
    )
    service = ManualAcquisitionService(
        root,
        session_id="archive_source_20260724",
        clock=lambda: NOW,
    )
    candidate = service.ingest(
        query="archive query",
        title="Archived primary source",
        source_url="https://history.example/archive-source",
        document_date="1812-05",
        date_precision="month",
        curated_text="archived source text",
        curator="curator-archive",
        transformation_refs=(transformation_ref,),
    )
    review = CandidateReview.create(
        candidate,
        decision="approved",
        reviewer="reviewer-archive",
        reviewed_at=NOW,
        available_at=datetime(1812, 5, 31, 23, 59, 59, tzinfo=UTC),
        source_type="primary",
        license="Public-Domain",
        reason="provenance checked",
    )
    record_ref = service.promote(
        candidate.candidate_id,
        review,
        evidence_id="hist-001-ev-archive",
        known_by_cutoff="known",
        excerpt_kind="paraphrased",
        fact_ids=("archive.fact",),
    )
    record_path = artifact_root.resolve(record_ref.uri)
    record = EvidenceRecordV2.from_dict(json.loads(record_path.read_text(encoding="utf-8")))
    return record_ref, record


def build_archived_evidence_suite(
    root: Path,
    record_ref: ArtifactRef,
    record: EvidenceRecordV2,
) -> None:
    evidence_manifest: dict[str, object] = {
        "schema_id": "tracelane://schemas/evidence-manifest/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "case_id": "hist-001",
        "cutoff_at": CUTOFF,
        "record_refs": [record_ref.to_dict()],
        "rejected_future_refs": [],
        "source_licenses": {record.evidence_id: record.license},
        "transformation_refs": [reference.to_dict() for reference in record.transformation_refs],
        "bundle_sha256": compute_history_bundle_sha256(
            case_id="hist-001",
            cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
            record_refs=(record_ref,),
            rejected_future_refs=(),
            transformation_refs=record.transformation_refs,
            source_licenses={record.evidence_id: record.license},
        ),
    }
    evidence_manifest["content_sha256"] = content_digest(evidence_manifest)
    evidence_manifest_path = write_json(
        root / "history/hist-001/evidence/manifest.json",
        evidence_manifest,
    )
    evidence_manifest_ref = fixture_ref(
        root,
        evidence_manifest_path,
        "evidence_manifest",
        "tracelane://schemas/evidence-manifest/v2",
    )
    case: dict[str, object] = {
        "schema_id": "tracelane://schemas/case/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "case_id": "hist-001",
        "title": "Archived acquisition evidence",
        "decision_maker": "Historical decision maker",
        "cutoff_at": CUTOFF,
        "intervention": "Choose a strategy",
        "projection_end": "1815-12-31",
        "minimum_alternatives": 2,
        "minimum_scenario_branches": 1,
        "required_domains": ["diplomacy"],
        "evidence_manifest_ref": evidence_manifest_ref,
        "rubric_refs": [],
    }
    case["content_sha256"] = content_digest(case)
    case_path = write_json(root / "history/hist-001/case.json", case)
    case_ref = fixture_ref(
        root,
        case_path,
        "history_case",
        "tracelane://schemas/case/v2",
    )
    development = {
        "schema_id": "tracelane://schemas/suite-split/v2",
        "schema_version": "2.0.0",
        "split": "development",
        "scenario_ids": ["hist-001/archive"],
    }
    development_path = write_json(root / "splits/development.json", development)
    heldout = {
        "schema_id": "tracelane://schemas/suite-split/v2",
        "schema_version": "2.0.0",
        "split": "heldout",
        "scenario_ids": [],
    }
    heldout_path = write_json(root / "splits/heldout.json", heldout)
    suite_manifest: dict[str, object] = {
        "schema_id": "tracelane://schemas/suite-manifest/v2",
        "schema_version": "2.0.0",
        "content_sha256": "",
        "suite_id": "history-v0.2",
        "splits": {
            "development": fixture_ref(
                root,
                development_path,
                "suite_split",
                "tracelane://schemas/suite-split/v2",
            ),
            "heldout": fixture_ref(
                root,
                heldout_path,
                "suite_split",
                "tracelane://schemas/suite-split/v2",
            ),
        },
        "scenarios": [
            {
                "scenario_id": "hist-001/archive",
                "case_id": "hist-001",
                "case_ref": case_ref,
                "evidence_manifest_ref": evidence_manifest_ref,
                "fault_ref": None,
            }
        ],
    }
    suite_manifest["content_sha256"] = content_digest(suite_manifest)
    write_json(root / "manifest.json", suite_manifest)


def file_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_archive_promoted_evidence_preserves_closure_and_loads_history(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source-root"
    target_root = tmp_path / "frozen-root"
    target_root.mkdir()
    promoted_ref, record = acquire_promoted_evidence(source_root)

    archived_ref = history_module.archive_promoted_evidence(
        source_root,
        target_root,
        promoted_ref,
    )

    assert archived_ref == promoted_ref
    closure_refs = (
        promoted_ref,
        record.candidate_ref,
        record.review_ref,
        record.content_ref,
        *record.transformation_refs,
    )
    source = ArtifactRoot(source_root)
    target = ArtifactRoot(target_root)
    for reference in closure_refs:
        assert (
            target.resolve(reference.uri).read_bytes() == source.resolve(reference.uri).read_bytes()
        )

    build_archived_evidence_suite(target_root, promoted_ref, record)
    entry = load_history_suite(target_root, "development")[0]
    frozen = freeze_history_evidence(
        load_history_case(entry.case_ref_path),
        load_evidence_manifest(entry.evidence_manifest_path),
    )
    assert frozen.records == (record,)
    assert not (tmp_path / "fixtures/v0.2").exists()


def test_archive_promoted_evidence_is_idempotent(tmp_path: Path) -> None:
    source_root = tmp_path / "source-root"
    target_root = tmp_path / "frozen-root"
    target_root.mkdir()
    promoted_ref, _record = acquire_promoted_evidence(source_root)

    first = history_module.archive_promoted_evidence(
        source_root,
        target_root,
        promoted_ref,
    )
    after_first = file_snapshot(target_root)
    second = history_module.archive_promoted_evidence(
        source_root,
        target_root,
        promoted_ref,
    )

    assert first == promoted_ref
    assert second == promoted_ref
    assert file_snapshot(target_root) == after_first
    assert not (tmp_path / "fixtures/v0.2").exists()


def test_archive_promoted_evidence_rejects_conflicting_target_without_overwrite(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source-root"
    target_root = tmp_path / "frozen-root"
    target = ArtifactRoot(target_root)
    promoted_ref, _record = acquire_promoted_evidence(source_root)
    conflicting_path = target.resolve(promoted_ref.uri)
    conflicting_path.parent.mkdir(parents=True)
    conflicting_path.write_bytes(b"conflicting target")

    with pytest.raises(ValueError, match="conflict|immutable|hash|bytes"):
        history_module.archive_promoted_evidence(
            source_root,
            target_root,
            promoted_ref,
        )

    assert conflicting_path.read_bytes() == b"conflicting target"
    assert not (tmp_path / "fixtures/v0.2").exists()


@pytest.mark.parametrize(
    "closure_member",
    ["record", "candidate", "review", "content", "transformation"],
)
def test_archive_promoted_evidence_authenticates_source_before_creating_target(
    tmp_path: Path,
    closure_member: str,
) -> None:
    source_root = tmp_path / "source-root"
    target_root = tmp_path / "frozen-root"
    promoted_ref, record = acquire_promoted_evidence(source_root)
    source = ArtifactRoot(source_root)
    missing_ref = {
        "record": promoted_ref,
        "candidate": record.candidate_ref,
        "review": record.review_ref,
        "content": record.content_ref,
        "transformation": record.transformation_refs[0],
    }[closure_member]
    source.resolve(missing_ref.uri).unlink()

    with pytest.raises(ValueError, match="unavailable|hash|size|invalid"):
        history_module.archive_promoted_evidence(
            source_root,
            target_root,
            promoted_ref,
        )

    assert not target_root.exists()
    assert not (tmp_path / "fixtures/v0.2").exists()


def test_archive_promoted_evidence_rejects_substituted_source_before_target_mutation(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source-root"
    target_root = tmp_path / "frozen-root"
    target_root.mkdir()
    sentinel = target_root / "sentinel.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    promoted_ref, record = acquire_promoted_evidence(source_root)
    source = ArtifactRoot(source_root)
    source.resolve(record.review_ref.uri).write_bytes(b"substituted source")
    before = file_snapshot(target_root)

    with pytest.raises(ValueError, match="unavailable|hash|size|invalid"):
        history_module.archive_promoted_evidence(
            source_root,
            target_root,
            promoted_ref,
        )

    assert file_snapshot(target_root) == before
    assert not (tmp_path / "fixtures/v0.2").exists()


def test_freeze_rejects_future_record_even_if_manifest_admits_it(
    tmp_path: Path,
) -> None:
    build_suite(tmp_path, admitted_available_at="1812-06-25T00:00:00Z")
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    manifest = load_evidence_manifest(entry.evidence_manifest_path)

    with pytest.raises(ValueError, match="after decision cutoff"):
        freeze_history_evidence(case, manifest)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("intervention", "changed intervention"),
        ("required_domains", ("logistics", "diplomacy")),
    ],
)
def test_freeze_rejects_stale_case_digest_without_output_mutation(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    build_suite(tmp_path)
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    manifest = load_evidence_manifest(entry.evidence_manifest_path)
    mutated = replace(case, **{field: replacement})
    before = file_snapshot(tmp_path)

    with pytest.raises(ValueError, match="history case content hash"):
        freeze_history_evidence(mutated, manifest)

    assert file_snapshot(tmp_path) == before


def test_suite_loader_uses_declared_split_not_directory_scanning(
    tmp_path: Path,
) -> None:
    build_suite(tmp_path)
    (tmp_path / "history" / "undeclared").mkdir()

    entries = load_history_suite(tmp_path, "development")

    assert [entry.scenario_id for entry in entries] == ["hist-001/clean"]


def test_fixture_reference_hash_is_verified_before_parsing(tmp_path: Path) -> None:
    build_suite(tmp_path)
    case_path = tmp_path / "history/hist-001/case.json"
    case_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="hash|size"):
        _ = load_history_suite(tmp_path, "development")[0].case_ref_path


def test_suite_loader_parses_the_same_verified_split_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_suite(tmp_path)
    split_path = tmp_path / "splits" / "development.json"
    original_secure_read = history_loader_module.secure_read_bytes
    split_reads = 0

    def replace_if_reopened(
        path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> bytes:
        nonlocal split_reads
        if path == split_path:
            split_reads += 1
            if split_reads > 1:
                replacement = {
                    "schema_id": "tracelane://schemas/suite-split/v2",
                    "schema_version": "2.0.0",
                    "split": "heldout",
                    "scenario_ids": [],
                }
                split_path.write_text(
                    canonical_json(replacement) + "\n",
                    encoding="utf-8",
                )
        return original_secure_read(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        history_loader_module,
        "secure_read_bytes",
        replace_if_reopened,
    )

    entries = load_history_suite(tmp_path, "development")

    assert split_reads == 1
    assert [entry.scenario_id for entry in entries] == ["hist-001/clean"]


def test_case_must_reference_exact_loaded_evidence_manifest(tmp_path: Path) -> None:
    build_suite(tmp_path)
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    manifest_path = entry.evidence_manifest_path
    replacement = json.loads(manifest_path.read_text(encoding="utf-8"))
    replacement["source_licenses"]["hist-001-ev-0001"] = "CC-BY-4.0"
    replacement["bundle_sha256"] = compute_history_bundle_sha256(
        case_id="hist-001",
        cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        record_refs=replacement["record_refs"],
        rejected_future_refs=replacement["rejected_future_refs"],
        transformation_refs=replacement["transformation_refs"],
        source_licenses=replacement["source_licenses"],
    )
    replacement["content_sha256"] = content_digest(replacement)
    manifest_path.write_text(canonical_json(replacement) + "\n", encoding="utf-8")
    loaded_manifest = load_evidence_manifest(manifest_path)

    with pytest.raises(ValueError, match="evidence manifest reference"):
        freeze_history_evidence(case, loaded_manifest)


def test_rejected_future_record_must_be_unavailable_by_cutoff(
    tmp_path: Path,
) -> None:
    build_suite(
        tmp_path,
        rejected_available_at="1812-06-25T00:00:00Z",
        rejected_known_by_cutoff="known",
    )
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    manifest = load_evidence_manifest(entry.evidence_manifest_path)

    with pytest.raises(ValueError, match="unavailable"):
        freeze_history_evidence(case, manifest)


@pytest.mark.parametrize(
    ("record_kind", "record_schema_id", "message"),
    [
        (
            "evidence_blob",
            "tracelane://schemas/evidence-record/v2",
            "kind",
        ),
        ("evidence_record", None, "schema"),
    ],
)
def test_admitted_record_reference_requires_expected_kind_and_schema(
    tmp_path: Path,
    record_kind: str,
    record_schema_id: str | None,
    message: str,
) -> None:
    build_suite(
        tmp_path,
        admitted_record_kind=record_kind,
        admitted_record_schema_id=record_schema_id,
    )
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match=message):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


def test_admitted_and_rejected_evidence_ids_must_be_disjoint(tmp_path: Path) -> None:
    build_suite(
        tmp_path,
        rejected_available_at="1812-06-25T00:00:00Z",
        rejected_evidence_id="hist-001-ev-0001",
    )
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match="duplicate|disjoint"):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


@pytest.mark.parametrize(
    ("record_kind", "record_schema_id", "message"),
    [
        (
            "evidence_blob",
            "tracelane://schemas/evidence-record/v2",
            "kind",
        ),
        ("evidence_record", None, "schema"),
    ],
)
def test_rejected_record_reference_requires_expected_kind_and_schema(
    tmp_path: Path,
    record_kind: str,
    record_schema_id: str | None,
    message: str,
) -> None:
    build_suite(
        tmp_path,
        rejected_available_at="1812-06-25T00:00:00Z",
        rejected_record_kind=record_kind,
        rejected_record_schema_id=record_schema_id,
    )
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match=message):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


def test_rejected_evidence_ids_must_be_unique(tmp_path: Path) -> None:
    build_suite(
        tmp_path,
        rejected_available_at="1812-06-25T00:00:00Z",
        duplicate_rejected_ref=True,
    )
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match="duplicate"):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


def test_source_license_map_must_match_admitted_record_license(tmp_path: Path) -> None:
    build_suite(tmp_path, manifest_license="CC-BY-4.0")
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match="source license"):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


def test_rejected_record_content_bytes_are_verified(tmp_path: Path) -> None:
    build_suite(
        tmp_path,
        rejected_available_at="1812-06-25T00:00:00Z",
    )
    future_blob = tmp_path / "history/hist-001/evidence/blobs/future.txt"
    future_blob.write_text("tampered", encoding="utf-8")
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match="hash|size"):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


@pytest.mark.parametrize("lineage_ref", ["candidate_ref", "review_ref"])
def test_history_loader_rejects_lineage_artifact_substitution(
    tmp_path: Path,
    lineage_ref: str,
) -> None:
    build_suite(tmp_path)
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    manifest = load_evidence_manifest(entry.evidence_manifest_path)
    record = json.loads(
        (tmp_path / "history/hist-001/evidence/records/hist-001-ev-0001.json").read_text(
            encoding="utf-8"
        )
    )
    uri = record[lineage_ref]["uri"]
    lineage_path = tmp_path / uri.removeprefix("tracelane://fixtures/v0.2/")
    lineage_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="lineage|candidate|review|hash|size"):
        freeze_history_evidence(case, manifest)


@pytest.mark.parametrize(
    ("rejected_uses_transformation", "declare_transformation"),
    [(True, False), (False, True)],
)
def test_declared_transformations_must_equal_used_transformations(
    tmp_path: Path,
    rejected_uses_transformation: bool,
    declare_transformation: bool,
) -> None:
    build_suite(
        tmp_path,
        rejected_available_at="1812-06-25T00:00:00Z",
        rejected_uses_transformation=rejected_uses_transformation,
        declare_transformation=declare_transformation,
    )
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match="transformation references"):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


@pytest.mark.parametrize(
    ("record_group", "kind", "schema_id", "message"),
    [
        ("admitted", "evidence_transformation", None, "kind"),
        (
            "admitted",
            "evidence_blob",
            "tracelane://schemas/case/v2",
            "schema",
        ),
        ("rejected", "evidence_transformation", None, "kind"),
        (
            "rejected",
            "evidence_blob",
            "tracelane://schemas/case/v2",
            "schema",
        ),
    ],
)
def test_record_content_reference_requires_blob_kind_and_no_schema(
    tmp_path: Path,
    record_group: str,
    kind: str,
    schema_id: str | None,
    message: str,
) -> None:
    build_options: dict[str, object] = {}
    if record_group == "admitted":
        build_options.update(
            admitted_content_kind=kind,
            admitted_content_schema_id=schema_id,
        )
    else:
        build_options.update(
            rejected_available_at="1812-06-25T00:00:00Z",
            rejected_content_kind=kind,
            rejected_content_schema_id=schema_id,
        )
    build_suite(tmp_path, **build_options)
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match=message):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


@pytest.mark.parametrize(
    ("record_group", "kind", "schema_id", "message"),
    [
        ("admitted", "evidence_blob", None, "kind"),
        (
            "admitted",
            "evidence_transformation",
            "tracelane://schemas/case/v2",
            "schema",
        ),
        ("rejected", "evidence_blob", None, "kind"),
        (
            "rejected",
            "evidence_transformation",
            "tracelane://schemas/case/v2",
            "schema",
        ),
    ],
)
def test_record_transformation_reference_requires_expected_kind_and_no_schema(
    tmp_path: Path,
    record_group: str,
    kind: str,
    schema_id: str | None,
    message: str,
) -> None:
    build_options: dict[str, object] = {
        "declare_transformation": True,
        "transformation_kind": kind,
        "transformation_schema_id": schema_id,
    }
    if record_group == "admitted":
        build_options["admitted_uses_transformation"] = True
    else:
        build_options.update(
            rejected_available_at="1812-06-25T00:00:00Z",
            rejected_uses_transformation=True,
        )
    build_suite(tmp_path, **build_options)
    entry = load_history_suite(tmp_path, "development")[0]

    with pytest.raises(ValueError, match=message):
        freeze_history_evidence(
            load_history_case(entry.case_ref_path),
            load_evidence_manifest(entry.evidence_manifest_path),
        )


@pytest.mark.parametrize(
    ("kind", "schema_id", "message"),
    [
        ("evidence_blob", None, "kind"),
        (
            "evidence_transformation",
            "tracelane://schemas/case/v2",
            "schema",
        ),
    ],
)
def test_manifest_transformation_reference_requires_expected_kind_and_no_schema(
    tmp_path: Path,
    kind: str,
    schema_id: str | None,
    message: str,
) -> None:
    build_suite(
        tmp_path,
        admitted_uses_transformation=True,
        declare_transformation=True,
        manifest_transformation_kind=kind,
        manifest_transformation_schema_id=schema_id,
    )

    with pytest.raises(ValueError, match=message):
        load_history_suite(tmp_path, "development")


def test_case_rejects_byte_identical_manifest_loaded_from_other_path(
    tmp_path: Path,
) -> None:
    build_suite(tmp_path)
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    original_path = entry.evidence_manifest_path
    copied_path = original_path.with_name("manifest-identical-copy.json")
    copied_path.write_bytes(original_path.read_bytes())
    copied_manifest = load_evidence_manifest(copied_path)

    with pytest.raises(ValueError, match="evidence manifest reference"):
        freeze_history_evidence(case, copied_manifest)


def test_suite_entry_case_id_must_match_loaded_case(tmp_path: Path) -> None:
    build_suite(tmp_path, entry_case_id="hist-002")

    with pytest.raises(ValueError, match="case identity"):
        load_history_suite(tmp_path, "development")


def test_suite_entry_manifest_ref_must_match_loaded_case_ref(tmp_path: Path) -> None:
    build_suite(tmp_path, swap_entry_manifest_ref=True)

    with pytest.raises(ValueError, match="evidence manifest reference"):
        load_history_suite(tmp_path, "development")


def test_suite_entry_loaded_manifest_case_id_must_match_case(tmp_path: Path) -> None:
    build_suite(tmp_path, evidence_manifest_case_id="hist-002")

    with pytest.raises(ValueError, match="manifest identity"):
        load_history_suite(tmp_path, "development")
