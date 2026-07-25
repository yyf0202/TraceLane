from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracelane.acquisition.contracts import compute_candidate_id
from tracelane.contracts import canonical_json
from tracelane.evidence_registry import index as evidence_index
from tracelane.evidence_registry.contracts import (
    EvidenceProject,
    EvidenceTransformation,
    ProjectEvidenceCandidate,
    candidate_record_digest,
)
from tracelane.evidence_registry.index import (
    EvidenceIndexEntry,
    EvidenceProjectIndex,
    EvidenceQuery,
    build_project_index,
    build_registry,
    find_evidence,
    rebuild_project_index,
    rebuild_registry,
    verify_evidence_registry,
)
from tracelane.evidence_registry.reviews import EvidenceReview, append_review
from tracelane.evidence_registry.storage import (
    EvidenceBlobStore,
    EvidenceRoot,
    write_json_create_or_match,
)
from tracelane.v2.contracts import ArtifactRef, make_object_id
from tracelane.v2.schema import validate_document


def _write_project(root: EvidenceRoot) -> EvidenceProject:
    project = EvidenceProject.create(
        project_id="hist-001",
        title="HIST-001",
        research_question="What evidence supports the counterfactual?",
        historical_cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        intervention="Napoleon does not cross the Niemen.",
        required_domains=("diplomacy", "logistics"),
        admitted_source_types=("dataset", "primary", "secondary"),
        status="active",
    )
    write_json_create_or_match(
        root,
        "tracelane://evidence/projects/hist-001/project.json",
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        project.to_dict(),
    )
    return project


def _candidate(
    root: EvidenceRoot,
    *,
    ordinal: int,
    document_date: str,
    date_precision: str,
    source_type: str = "primary",
    role: str = "evidence",
) -> ProjectEvidenceCandidate:
    payload = f"curated evidence {ordinal}".encode()
    blob_ref = EvidenceBlobStore(root).put_bytes(payload, "text/plain", "evidence_blob")
    query = f"query {ordinal}"
    title = f"source {ordinal}"
    source_url = f"https://history.example/source-{ordinal}"
    candidate_id = compute_candidate_id(
        query=query,
        title=title,
        source_url=source_url,
        document_date=document_date,
        date_precision=date_precision,
        content_sha256=hashlib.sha256(payload).hexdigest(),
    )
    candidate = ProjectEvidenceCandidate.create(
        project_id="hist-001",
        candidate_id=candidate_id,
        source_spec_id=f"hist001_source_{ordinal}",
        query=query,
        title=title,
        source_url=source_url,
        document_date=document_date,
        date_precision=date_precision,
        retrieved_at=datetime(2026, 7, 25, tzinfo=UTC),
        curator="repository curator",
        source_type=source_type,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        domains=("diplomacy",) if ordinal % 2 else ("logistics",),
        fact_ids=(f"fact.{ordinal}",),
        content_ref=blob_ref,
        transformation_refs=(),
        content_authorship="repository_authored",
        retention_policy="paraphrase_only",
        license_basis="Repository-authored paraphrase.",
        acquisition_session_id="acq_hist001_20260725",
        source_candidate_uri=f"tracelane://artifacts/candidates/source-{ordinal}.json",
        source_candidate_id=candidate_id,
        source_candidate_record_sha256=f"{ordinal:x}" * 64,
        source_candidate_content_sha256=blob_ref.sha256,
    )
    write_json_create_or_match(
        root,
        (f"tracelane://evidence/projects/hist-001/candidates/{candidate.candidate_id}.json"),
        "evidence_candidate",
        "tracelane://schemas/project-evidence-candidate/v1",
        candidate.to_dict(),
    )
    return candidate


def _review(
    root: EvidenceRoot,
    candidate: ProjectEvidenceCandidate,
    decision: str,
    *,
    supersedes_review_id: str | None = None,
) -> EvidenceReview:
    review = EvidenceReview.create(
        candidate,
        decision=decision,  # type: ignore[arg-type]
        reason=f"{decision} after review.",
        reviewer="history-reviewer",
        reviewed_at=datetime(2026, 7, 25, 8, 0, tzinfo=UTC),
        approved_fact_ids=candidate.fact_ids if decision == "approved" else (),
        approved_domains=candidate.domains if decision == "approved" else (),
        supersedes_review_id=supersedes_review_id,
    )
    append_review(root, review)
    return review


@pytest.fixture
def registry_root(tmp_path: Path) -> EvidenceRoot:
    root = EvidenceRoot.create(tmp_path / "evidence")
    _write_project(root)
    pending = _candidate(root, ordinal=1, document_date="1811", date_precision="year")
    approved = _candidate(root, ordinal=2, document_date="1812-05", date_precision="month")
    rejected = _candidate(root, ordinal=3, document_date="1812-06-01", date_precision="day")
    superseded = _candidate(root, ordinal=4, document_date="1811-12", date_precision="estimated")
    _candidate(
        root,
        ordinal=5,
        document_date="1812-06-29",
        date_precision="day",
        source_type="secondary",
        role="future-control",
    )
    _review(root, approved, "approved")
    _review(root, rejected, "rejected")
    _review(root, superseded, "superseded")
    assert pending
    rebuild_project_index(root, "hist-001")
    rebuild_registry(root)
    return root


def test_project_index_is_canonical_sorted_and_source_derived(
    registry_root: EvidenceRoot,
) -> None:
    index = build_project_index(registry_root, "hist-001")

    assert isinstance(index, EvidenceProjectIndex)
    assert [entry.candidate_id for entry in index.entries] == sorted(
        entry.candidate_id for entry in index.entries
    )
    assert index.status_counts == {
        "pending": 2,
        "approved": 1,
        "rejected": 1,
        "superseded": 1,
    }
    assert "build" not in str(index.to_dict()).lower()
    approved = next(entry for entry in index.entries if entry.effective_status == "approved")
    assert approved.current_review_ref is not None
    assert approved.current_review_ref.kind == "evidence_review"
    assert approved.current_review_ref.schema_id == "tracelane://schemas/evidence-review/v1"


def test_deleted_indexes_rebuild_byte_identically(registry_root: EvidenceRoot) -> None:
    project_path = registry_root.resolve(
        "tracelane://evidence/projects/hist-001/index.json", must_exist=True
    )
    registry_path = registry_root.resolve("tracelane://evidence/registry.json", must_exist=True)
    project_bytes = project_path.read_bytes()
    registry_bytes = registry_path.read_bytes()

    project_path.unlink()
    registry_path.unlink()
    rebuild_project_index(registry_root, "hist-001")
    rebuild_registry(registry_root)

    assert project_path.read_bytes() == project_bytes
    assert registry_path.read_bytes() == registry_bytes


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (EvidenceQuery("hist-001", statuses=("approved",)), 1),
        (EvidenceQuery("hist-001", fact_id="fact.2"), 1),
        (EvidenceQuery("hist-001", domain="logistics"), 2),
        (EvidenceQuery("hist-001", role="future-control"), 1),
        (EvidenceQuery("hist-001", source_type="secondary"), 1),
        (EvidenceQuery("hist-001", date_from="1812-05", date_to="1812-05"), 1),
        (EvidenceQuery("hist-001", date_from="1811-12", date_to="1811-12"), 2),
    ],
)
def test_find_evidence_filters(
    registry_root: EvidenceRoot, query: EvidenceQuery, expected: int
) -> None:
    assert len(find_evidence(registry_root, query)) == expected


def test_clean_query_excludes_future_control(registry_root: EvidenceRoot) -> None:
    values = find_evidence(
        registry_root,
        EvidenceQuery(project_id="hist-001", clean_only=True),
    )
    assert len(values) == 4
    assert all(item.role != "future-control" for item in values)


def test_verification_reports_persisted_file_hashes(
    registry_root: EvidenceRoot,
) -> None:
    report = verify_evidence_registry(registry_root, "hist-001")

    registry_bytes = registry_root.resolve(
        "tracelane://evidence/registry.json", must_exist=True
    ).read_bytes()
    index_bytes = registry_root.resolve(
        "tracelane://evidence/projects/hist-001/index.json", must_exist=True
    ).read_bytes()
    assert report.registry_sha256 == hashlib.sha256(registry_bytes).hexdigest()
    assert report.project_index_sha256 == hashlib.sha256(index_bytes).hexdigest()
    assert report.candidate_count == 5
    assert report.review_count == 3
    assert report.future_control_count == 1


def test_hand_edited_index_is_rejected(registry_root: EvidenceRoot) -> None:
    target, value = _index_value(registry_root)
    entries = value["entries"]
    counts = value["status_counts"]
    assert isinstance(entries, list)
    assert isinstance(counts, dict)
    approved = next(item for item in entries if item["effective_status"] == "approved")
    approved["effective_status"] = "rejected"
    counts["approved"] -= 1
    counts["rejected"] += 1
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(target, value)

    with pytest.raises(ValueError, match="project index"):
        verify_evidence_registry(registry_root, "hist-001")


def test_extra_json_in_managed_directory_is_rejected(
    registry_root: EvidenceRoot,
) -> None:
    target = registry_root.path / "projects" / "hist-001" / "reviews" / "extra.json"
    target.write_text('{"unexpected":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="review"):
        build_project_index(registry_root, "hist-001")


@pytest.mark.parametrize(
    "query",
    [
        EvidenceQuery("hist-001", statuses=("unknown",)),
        EvidenceQuery("hist-001", date_from="1812-13"),
        EvidenceQuery("hist-001", date_from="1813", date_to="1812"),
    ],
)
def test_invalid_query_fails_with_stable_public_error(
    registry_root: EvidenceRoot, query: EvidenceQuery
) -> None:
    with pytest.raises(ValueError, match="evidence query is invalid"):
        find_evidence(registry_root, query)


def _rewrite_json(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_json(value).encode("utf-8") + b"\n")


def _index_value(root: EvidenceRoot) -> tuple[Path, dict[str, object]]:
    path = root.resolve("tracelane://evidence/projects/hist-001/index.json", must_exist=True)
    return path, json.loads(path.read_text(encoding="utf-8"))


def _tree_snapshot(root: EvidenceRoot) -> dict[str, bytes]:
    return {
        path.relative_to(root.path).as_posix(): path.read_bytes()
        for path in sorted(root.path.rglob("*"))
        if path.is_file()
    }


def _redigest_candidate(path: Path, changes: dict[str, object]) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.update(changes)
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(path, value)


def _corrupt_registry_record_digest(root: EvidenceRoot) -> None:
    path = root.resolve("tracelane://evidence/registry.json", must_exist=True)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["record_sha256"] = "0" * 64
    _rewrite_json(path, value)


def _corrupt_project_record_digest(root: EvidenceRoot) -> None:
    path = root.resolve(
        "tracelane://evidence/projects/hist-001/project.json",
        must_exist=True,
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["record_sha256"] = "0" * 64
    _rewrite_json(path, value)


def _corrupt_index_record_digest(root: EvidenceRoot) -> None:
    path, value = _index_value(root)
    value["record_sha256"] = "0" * 64
    _rewrite_json(path, value)


def _corrupt_candidate_source(root: EvidenceRoot) -> None:
    _redigest_candidate(
        _candidate_path_for_fact(root, "fact.1"),
        {"source_url": "https://history.example/substituted-source"},
    )


def _corrupt_candidate_date(root: EvidenceRoot) -> None:
    _redigest_candidate(
        _candidate_path_for_fact(root, "fact.1"),
        {"document_date": "1810"},
    )


def _corrupt_candidate_role(root: EvidenceRoot) -> None:
    path = next(
        path
        for path in (root.path / "projects" / "hist-001" / "candidates").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["role"] == "future-control"
    )
    _redigest_candidate(path, {"role": "evidence"})


def _corrupt_candidate_facts(root: EvidenceRoot) -> None:
    _redigest_candidate(
        _candidate_path_for_fact(root, "fact.1"),
        {"fact_ids": ["fact.changed"]},
    )


def _corrupt_candidate_domains(root: EvidenceRoot) -> None:
    _redigest_candidate(
        _candidate_path_for_fact(root, "fact.1"),
        {"domains": ["military"]},
    )


def _corrupt_candidate_retention(root: EvidenceRoot) -> None:
    _redigest_candidate(
        _candidate_path_for_fact(root, "fact.1"),
        {"content_authorship": "third_party"},
    )


def _corrupt_candidate_lineage(root: EvidenceRoot) -> None:
    _redigest_candidate(
        _candidate_path_for_fact(root, "fact.1"),
        {"source_candidate_content_sha256": "0" * 64},
    )


def _corrupt_candidate_record_digest(root: EvidenceRoot) -> None:
    path = _candidate_path_for_fact(root, "fact.1")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["record_sha256"] = "0" * 64
    _rewrite_json(path, value)


def _corrupt_candidate_blob_bytes(root: EvidenceRoot) -> None:
    path = _candidate_path_for_fact(root, "fact.1")
    candidate = ProjectEvidenceCandidate.from_dict(json.loads(path.read_text(encoding="utf-8")))
    root.resolve(candidate.content_ref.uri, must_exist=True).write_bytes(b"corrupt blob bytes")


def _corrupt_candidate_blob_size(root: EvidenceRoot) -> None:
    path = _candidate_path_for_fact(root, "fact.1")
    value = json.loads(path.read_text(encoding="utf-8"))
    content_ref = dict(value["content_ref"])
    content_ref["size_bytes"] += 1
    value["content_ref"] = content_ref
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(path, value)


def _corrupt_candidate_blob_path(root: EvidenceRoot) -> None:
    path = _candidate_path_for_fact(root, "fact.1")
    value = json.loads(path.read_text(encoding="utf-8"))
    content_ref = dict(value["content_ref"])
    content_ref["uri"] = "tracelane://evidence/blobs/sha256/" + "0" * 64
    value["content_ref"] = content_ref
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(path, value)


def _corrupt_candidate_inventory(root: EvidenceRoot) -> None:
    source = _candidate_path_for_fact(root, "fact.1")
    target = source.with_name("candidate_" + "0" * 24 + ".json")
    target.write_bytes(source.read_bytes())


def _corrupt_review_inventory(root: EvidenceRoot) -> None:
    source = next((root.path / "projects" / "hist-001" / "reviews").glob("*.json"))
    target = source.with_name("review_" + "0" * 24 + ".json")
    target.write_bytes(source.read_bytes())


def _approved_review_path(root: EvidenceRoot) -> Path:
    return next(
        path
        for path in (root.path / "projects" / "hist-001" / "reviews").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["decision"] == "approved"
    )


def _review_with_wire_identity(
    value: dict[str, object],
) -> dict[str, object]:
    identity = {
        key: item
        for key, item in value.items()
        if key
        not in {
            "schema_id",
            "schema_version",
            "review_id",
            "record_sha256",
        }
    }
    value["review_id"] = make_object_id("review", identity)
    value["record_sha256"] = candidate_record_digest(value)
    return value


def _replace_approved_review(
    root: EvidenceRoot,
    changes: dict[str, object],
) -> None:
    source = _approved_review_path(root)
    value = json.loads(source.read_text(encoding="utf-8"))
    value.update(changes)
    _review_with_wire_identity(value)
    target = source.with_name(f"{value['review_id']}.json")
    _rewrite_json(target, value)
    if target != source:
        source.unlink()


def _corrupt_review_record_digest(root: EvidenceRoot) -> None:
    path = _approved_review_path(root)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["record_sha256"] = "0" * 64
    _rewrite_json(path, value)


def _corrupt_review_candidate_digest(root: EvidenceRoot) -> None:
    _replace_approved_review(
        root,
        {"candidate_record_sha256": "0" * 64},
    )


def _corrupt_review_chain_head(root: EvidenceRoot) -> None:
    source = _approved_review_path(root)
    value = json.loads(source.read_text(encoding="utf-8"))
    value["reviewer"] = "second root reviewer"
    _review_with_wire_identity(value)
    _rewrite_json(source.with_name(f"{value['review_id']}.json"), value)


def _corrupt_review_predecessor(root: EvidenceRoot) -> None:
    _replace_approved_review(
        root,
        {"supersedes_review_id": "review_" + "f" * 24},
    )


def _corrupt_review_scope(root: EvidenceRoot) -> None:
    _replace_approved_review(
        root,
        {
            "approved_fact_ids": ["fact.outside-candidate"],
            "approved_domains": ["diplomacy"],
        },
    )


def _corrupt_review_decision(root: EvidenceRoot) -> None:
    _replace_approved_review(
        root,
        {
            "decision": "rejected",
            "approved_fact_ids": [],
            "approved_domains": [],
        },
    )


@pytest.mark.parametrize(
    ("corrupt", "category"),
    [
        (_corrupt_registry_record_digest, "^evidence registry is invalid$"),
        (_corrupt_project_record_digest, "^evidence project record is invalid$"),
        (_corrupt_index_record_digest, "^project index is invalid$"),
        (_corrupt_candidate_source, "^evidence candidate record is invalid$"),
        (_corrupt_candidate_date, "^evidence candidate record is invalid$"),
        (_corrupt_candidate_role, "^candidate date exceeds the project cutoff$"),
        (_corrupt_candidate_facts, "^project index does not match source records$"),
        (_corrupt_candidate_domains, "^project index does not match source records$"),
        (_corrupt_candidate_retention, "^evidence candidate record is invalid$"),
        (_corrupt_candidate_lineage, "^evidence candidate record is invalid$"),
        (_corrupt_candidate_record_digest, "^evidence candidate record is invalid$"),
        (_corrupt_candidate_blob_bytes, "^evidence blob hash mismatch$"),
        (_corrupt_candidate_blob_size, "^evidence blob size mismatch$"),
        (_corrupt_candidate_blob_path, "^evidence candidate record is invalid$"),
        (_corrupt_review_record_digest, "^evidence review record is invalid$"),
        (
            _corrupt_review_candidate_digest,
            "^project index does not match source records$",
        ),
        (_corrupt_review_chain_head, "^evidence review chain is invalid$"),
        (_corrupt_review_predecessor, "^evidence review chain is invalid$"),
        (_corrupt_review_scope, "^evidence review chain is invalid$"),
        (_corrupt_review_decision, "^project index does not match source records$"),
        (_corrupt_candidate_inventory, "^candidate inventory identity is invalid$"),
        (_corrupt_review_inventory, "^review inventory identity is invalid$"),
    ],
    ids=[
        "registry-digest",
        "project-digest",
        "index-digest",
        "candidate-source",
        "candidate-date",
        "candidate-role-cutoff",
        "candidate-facts",
        "candidate-domains",
        "candidate-retention",
        "candidate-lineage",
        "candidate-digest",
        "blob-bytes",
        "blob-size",
        "blob-path",
        "review-digest",
        "review-candidate-digest",
        "review-chain-head",
        "review-predecessor",
        "review-scope",
        "review-decision",
        "candidate-inventory",
        "review-inventory",
    ],
)
def test_registry_corruption_matrix_rejects_without_further_mutation(
    registry_root: EvidenceRoot,
    corrupt,
    category: str,
) -> None:
    corrupt(registry_root)
    corrupt_state = _tree_snapshot(registry_root)

    with pytest.raises(ValueError, match=category):
        verify_evidence_registry(registry_root, "hist-001")

    assert _tree_snapshot(registry_root) == corrupt_state


def test_new_source_candidate_cannot_remain_unindexed(
    registry_root: EvidenceRoot,
) -> None:
    _candidate(
        registry_root,
        ordinal=6,
        document_date="1812-04-01",
        date_precision="day",
    )
    changed_source_state = _tree_snapshot(registry_root)

    with pytest.raises(
        ValueError,
        match="^project index does not match source records$",
    ):
        verify_evidence_registry(registry_root)

    assert _tree_snapshot(registry_root) == changed_source_state


def test_validly_redigested_ghost_index_entry_is_rejected(
    registry_root: EvidenceRoot,
) -> None:
    path, value = _index_value(registry_root)
    entries = value["entries"]
    assert isinstance(entries, list)
    ghost = dict(entries[0])
    ghost_id = "candidate_" + "f" * 24
    ghost["candidate_id"] = ghost_id
    candidate_ref = dict(ghost["candidate_ref"])
    candidate_ref["uri"] = f"tracelane://evidence/projects/hist-001/candidates/{ghost_id}.json"
    ghost["candidate_ref"] = candidate_ref
    ghost["effective_status"] = "pending"
    ghost.pop("current_review_ref", None)
    entries.append(ghost)
    entries.sort(key=lambda item: item["candidate_id"])
    counts = value["status_counts"]
    assert isinstance(counts, dict)
    counts["pending"] += 1
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(path, value)

    with pytest.raises(ValueError, match="project index"):
        verify_evidence_registry(registry_root)


def test_validly_redigested_stale_current_review_ref_is_rejected(
    registry_root: EvidenceRoot,
) -> None:
    path, value = _index_value(registry_root)
    entries = value["entries"]
    assert isinstance(entries, list)
    reviewed = next(item for item in entries if "current_review_ref" in item)
    review_ref = dict(reviewed["current_review_ref"])
    review_ref["sha256"] = "0" * 64
    reviewed["current_review_ref"] = review_ref
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(path, value)
    corrupt_state = _tree_snapshot(registry_root)

    with pytest.raises(
        ValueError,
        match="^project index does not match source records$",
    ):
        verify_evidence_registry(registry_root)

    assert _tree_snapshot(registry_root) == corrupt_state


def test_duplicate_candidate_record_is_rejected_before_index_comparison(
    registry_root: EvidenceRoot,
) -> None:
    candidates = registry_root.path / "projects" / "hist-001" / "candidates"
    source = next(candidates.glob("*.json"))
    duplicate = candidates / ("candidate_" + "0" * 24 + ".json")
    duplicate.write_bytes(source.read_bytes())

    with pytest.raises(ValueError, match="candidate inventory"):
        verify_evidence_registry(registry_root)


def test_orphan_review_is_rejected(registry_root: EvidenceRoot) -> None:
    orphan = _candidate(
        registry_root,
        ordinal=7,
        document_date="1812-04-02",
        date_precision="day",
    )
    _review(registry_root, orphan, "rejected")
    registry_root.resolve(
        (f"tracelane://evidence/projects/hist-001/candidates/{orphan.candidate_id}.json"),
        must_exist=True,
    ).unlink()

    with pytest.raises(ValueError, match="orphan"):
        build_project_index(registry_root, "hist-001")


def _add_transformation_to_pending_candidate(
    root: EvidenceRoot,
) -> tuple[EvidenceTransformation, Path]:
    candidate_paths = sorted((root.path / "projects" / "hist-001" / "candidates").glob("*.json"))
    candidate_path = next(
        path
        for path in candidate_paths
        if "fact.1"
        in ProjectEvidenceCandidate.from_dict(json.loads(path.read_text(encoding="utf-8"))).fact_ids
    )
    candidate = ProjectEvidenceCandidate.from_dict(
        json.loads(candidate_path.read_text(encoding="utf-8"))
    )
    input_ref = EvidenceBlobStore(root).put_bytes(
        b"transformation input", "text/plain", "evidence_blob"
    )
    transformation = EvidenceTransformation.create(
        project_id="hist-001",
        candidate_id=candidate.candidate_id,
        transformation_type="normalization",
        input_ref=input_ref,
        output_ref=candidate.content_ref,
        actor="repository curator",
        method="Normalize whitespace.",
        parameters={"line_endings": "lf"},
        created_at=datetime(2026, 7, 25, tzinfo=UTC),
        license_implications="No change.",
    )
    transformation_ref = write_json_create_or_match(
        root,
        (
            "tracelane://evidence/projects/hist-001/transformations/"
            f"{transformation.transformation_id}.json"
        ),
        "evidence_transformation",
        "tracelane://schemas/evidence-transformation/v1",
        transformation.to_dict(),
    )
    ref_value = transformation_ref.to_dict()
    ref_value.pop("schema_id")
    candidate_value = candidate.to_dict()
    candidate_value["transformation_refs"] = [ArtifactRef.from_dict(ref_value).to_dict()]
    candidate_value["record_sha256"] = candidate_record_digest(candidate_value)
    _rewrite_json(candidate_path, candidate_value)
    return transformation, root.resolve(input_ref.uri, must_exist=True)


def test_transformation_reference_and_blobs_are_authenticated(
    registry_root: EvidenceRoot,
) -> None:
    transformation, input_path = _add_transformation_to_pending_candidate(registry_root)
    rebuilt = build_project_index(registry_root, "hist-001")
    transformed = next(
        item
        for item in rebuilt.entries
        if transformation.transformation_id in item.transformation_ids
    )
    assert transformed.transformation_ids == (transformation.transformation_id,)

    input_path.write_bytes(b"altered transformation input")
    with pytest.raises(ValueError, match="blob"):
        build_project_index(registry_root, "hist-001")


def test_orphan_transformation_is_rejected(registry_root: EvidenceRoot) -> None:
    transformation, _ = _add_transformation_to_pending_candidate(registry_root)
    input_ref = EvidenceBlobStore(registry_root).put_bytes(
        b"orphan input", "text/plain", "evidence_blob"
    )
    output_ref = EvidenceBlobStore(registry_root).put_bytes(
        b"orphan output", "text/plain", "evidence_blob"
    )
    orphan = EvidenceTransformation.create(
        project_id="hist-001",
        candidate_id=transformation.candidate_id,
        transformation_type="ocr",
        input_ref=input_ref,
        output_ref=output_ref,
        actor="repository curator",
        method="OCR.",
        parameters={},
        created_at=datetime(2026, 7, 25, 1, tzinfo=UTC),
        license_implications="No change.",
    )
    write_json_create_or_match(
        registry_root,
        (f"tracelane://evidence/projects/hist-001/transformations/{orphan.transformation_id}.json"),
        "evidence_transformation",
        "tracelane://schemas/evidence-transformation/v1",
        orphan.to_dict(),
    )

    with pytest.raises(ValueError, match="orphan"):
        build_project_index(registry_root, "hist-001")


def test_index_and_registry_schema_parity(registry_root: EvidenceRoot) -> None:
    index = build_project_index(registry_root, "hist-001").to_dict()
    registry = json.loads(
        registry_root.resolve("tracelane://evidence/registry.json", must_exist=True).read_text(
            encoding="utf-8"
        )
    )

    validate_document("evidence-project-index", index)
    validate_document("evidence-registry", registry)
    for schema_name, value in (
        ("evidence-project-index", index),
        ("evidence-registry", registry),
    ):
        with pytest.raises(ValueError):
            validate_document(schema_name, value | {"unexpected": True})


def test_sensitive_unknown_index_field_fails_without_echo(
    registry_root: EvidenceRoot,
) -> None:
    sensitive = r"C:\private\registry-token.txt"
    value = build_project_index(registry_root, "hist-001").to_dict()
    value[sensitive] = "unexpected"

    with pytest.raises(ValueError, match="sensitive") as captured:
        EvidenceProjectIndex.from_dict(value)

    assert sensitive not in str(captured.value)


def test_index_entry_rejects_explicit_null_current_review_ref(
    registry_root: EvidenceRoot,
) -> None:
    value = build_project_index(registry_root, "hist-001").entries[0].to_dict()
    value["effective_status"] = "pending"
    value["current_review_ref"] = None

    with pytest.raises(ValueError, match="current review reference"):
        EvidenceIndexEntry.from_dict(value)


def test_unreferenced_global_blob_is_permitted(
    registry_root: EvidenceRoot,
) -> None:
    EvidenceBlobStore(registry_root).put_bytes(
        b"interrupted import payload",
        "application/octet-stream",
        "evidence_blob",
    )

    assert verify_evidence_registry(registry_root).candidate_count == 5


def test_post_cutoff_non_future_candidate_is_rejected(
    registry_root: EvidenceRoot,
) -> None:
    candidate_path = next(
        path
        for path in (registry_root.path / "projects" / "hist-001" / "candidates").glob("*.json")
        if json.loads(path.read_text(encoding="utf-8"))["role"] == "future-control"
    )
    value = json.loads(candidate_path.read_text(encoding="utf-8"))
    value["role"] = "evidence"
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(candidate_path, value)

    with pytest.raises(ValueError, match="cutoff"):
        build_project_index(registry_root, "hist-001")


def _candidate_path_for_fact(root: EvidenceRoot, fact_id: str) -> Path:
    return next(
        path
        for path in sorted((root.path / "projects" / "hist-001" / "candidates").glob("*.json"))
        if fact_id
        in ProjectEvidenceCandidate.from_dict(json.loads(path.read_text(encoding="utf-8"))).fact_ids
    )


def _planned_transformation_ref(
    transformation: EvidenceTransformation,
) -> ArtifactRef:
    data = canonical_json(transformation.to_dict()).encode("utf-8") + b"\n"
    return ArtifactRef.from_dict(
        {
            "kind": "evidence_transformation",
            "uri": (
                "tracelane://evidence/projects/hist-001/transformations/"
                f"{transformation.transformation_id}.json"
            ),
            "media_type": "application/json",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
            "schema_id": "tracelane://schemas/evidence-transformation/v1",
        }
    )


def _install_lineage(
    root: EvidenceRoot,
    disconnected_at: str | None,
) -> tuple[str, ...]:
    candidate_path = _candidate_path_for_fact(root, "fact.1")
    candidate = ProjectEvidenceCandidate.from_dict(
        json.loads(candidate_path.read_text(encoding="utf-8"))
    )
    source = EvidenceBlobStore(root).put_bytes(b"lineage source", "text/plain", "evidence_blob")
    middle_one = EvidenceBlobStore(root).put_bytes(
        b"lineage middle one", "text/plain", "evidence_blob"
    )
    middle_two = EvidenceBlobStore(root).put_bytes(
        b"lineage middle two", "text/plain", "evidence_blob"
    )
    disconnected = EvidenceBlobStore(root).put_bytes(
        b"lineage disconnected", "text/plain", "evidence_blob"
    )
    final_ref = candidate.content_ref
    links = {
        "first": (
            (source, middle_one),
            (disconnected, final_ref),
        ),
        "intermediate": (
            (source, middle_one),
            (middle_one, middle_two),
            (disconnected, final_ref),
        ),
        "final": ((source, disconnected),),
        None: (
            (source, middle_one),
            (middle_one, middle_two),
            (middle_two, final_ref),
        ),
    }[disconnected_at]

    transformations: tuple[EvidenceTransformation, ...] | None = None
    planned_refs: tuple[ArtifactRef, ...] | None = None
    for attempt in range(200):
        values = tuple(
            EvidenceTransformation.create(
                project_id="hist-001",
                candidate_id=candidate.candidate_id,
                transformation_type="normalization",
                input_ref=input_ref,
                output_ref=output_ref,
                actor=f"repository curator {attempt}",
                method=f"Lineage step {position}.",
                parameters={"position": position},
                created_at=datetime(2026, 7, 25, position, tzinfo=UTC),
                license_implications="No change.",
            )
            for position, (input_ref, output_ref) in enumerate(links, start=1)
        )
        refs = tuple(_planned_transformation_ref(item) for item in values)
        keys = tuple(canonical_json(item.to_dict()) for item in refs)
        if keys == tuple(sorted(keys)):
            transformations = values
            planned_refs = refs
            break
    assert transformations is not None
    assert planned_refs is not None

    candidate_refs: list[ArtifactRef] = []
    for transformation, planned_ref in zip(transformations, planned_refs, strict=True):
        actual_ref = write_json_create_or_match(
            root,
            planned_ref.uri,
            "evidence_transformation",
            "tracelane://schemas/evidence-transformation/v1",
            transformation.to_dict(),
        )
        assert actual_ref == planned_ref
        ref_value = actual_ref.to_dict()
        ref_value.pop("schema_id")
        candidate_refs.append(ArtifactRef.from_dict(ref_value))
    candidate_value = candidate.to_dict()
    candidate_value["transformation_refs"] = [item.to_dict() for item in candidate_refs]
    candidate_value["record_sha256"] = candidate_record_digest(candidate_value)
    _rewrite_json(candidate_path, candidate_value)
    return tuple(item.transformation_id for item in transformations)


def test_ordered_transformation_lineage_is_accepted(
    registry_root: EvidenceRoot,
) -> None:
    transformation_ids = _install_lineage(registry_root, None)

    index = build_project_index(registry_root, "hist-001")

    entry = next(item for item in index.entries if item.fact_ids == ("fact.1",))
    assert entry.transformation_ids == tuple(sorted(transformation_ids))


@pytest.mark.parametrize("disconnected_at", ["first", "intermediate", "final"])
def test_disconnected_transformation_lineage_is_rejected(
    registry_root: EvidenceRoot,
    disconnected_at: str,
) -> None:
    _install_lineage(registry_root, disconnected_at)

    with pytest.raises(ValueError, match="transformation lineage"):
        build_project_index(registry_root, "hist-001")


def _mutate_candidate_record(root: EvidenceRoot) -> None:
    path = _candidate_path_for_fact(root, "fact.1")
    value = json.loads(path.read_text(encoding="utf-8"))
    value["domains"] = ["diplomacy", "snapshot-change"]
    value["record_sha256"] = candidate_record_digest(value)
    _rewrite_json(path, value)


def _mutate_candidate_blob(root: EvidenceRoot) -> None:
    path = _candidate_path_for_fact(root, "fact.1")
    candidate = ProjectEvidenceCandidate.from_dict(json.loads(path.read_text(encoding="utf-8")))
    root.resolve(candidate.content_ref.uri, must_exist=True).write_bytes(
        b"changed after derivation"
    )


def _mutate_inventory(root: EvidenceRoot) -> None:
    (root.path / "projects" / "hist-001" / "candidates" / "late-entry.txt").write_text(
        "changed", encoding="utf-8"
    )


def _mutate_after_first_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    root: EvidenceRoot,
    mutation,
) -> None:
    original = evidence_index._load_project_snapshot
    calls = 0

    def changing_snapshot(*args, **kwargs):
        nonlocal calls
        snapshot = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            mutation(root)
        return snapshot

    monkeypatch.setattr(
        evidence_index,
        "_load_project_snapshot",
        changing_snapshot,
    )


@pytest.mark.parametrize(
    "operation",
    [
        lambda root: build_project_index(root, "hist-001"),
        build_registry,
        verify_evidence_registry,
        lambda root: find_evidence(root, EvidenceQuery("hist-001")),
    ],
    ids=["project-build", "registry-build", "verify", "find"],
)
def test_read_paths_reauthenticate_source_snapshot_before_success(
    registry_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
    operation,
) -> None:
    _mutate_after_first_snapshot(
        monkeypatch,
        registry_root,
        _mutate_candidate_record,
    )

    with pytest.raises(ValueError, match="source snapshot changed"):
        operation(registry_root)


@pytest.mark.parametrize(
    "mutation",
    [_mutate_inventory, _mutate_candidate_blob],
    ids=["inventory", "blob"],
)
def test_final_reauthentication_covers_inventory_and_referenced_blobs(
    registry_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
) -> None:
    _mutate_after_first_snapshot(monkeypatch, registry_root, mutation)

    with pytest.raises(ValueError, match="source snapshot changed"):
        verify_evidence_registry(registry_root)


@pytest.mark.parametrize("publication", ["project-index", "registry"])
def test_rebuild_reauthenticates_after_publication_before_returning(
    registry_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
    publication: str,
) -> None:
    if publication == "project-index":
        target = registry_root.resolve(
            "tracelane://evidence/projects/hist-001/index.json",
            must_exist=True,
        )
    else:
        target = registry_root.resolve(
            "tracelane://evidence/registry.json",
            must_exist=True,
        )

    def operation() -> None:
        if publication == "project-index":
            rebuild_project_index(registry_root, "hist-001")
        else:
            rebuild_registry(registry_root)

    target.unlink()
    original_write = evidence_index.write_json_create_or_match

    def changing_write(*args, **kwargs):
        reference = original_write(*args, **kwargs)
        _mutate_candidate_record(registry_root)
        return reference

    monkeypatch.setattr(
        evidence_index,
        "write_json_create_or_match",
        changing_write,
    )

    with pytest.raises(ValueError, match="source snapshot changed"):
        operation()

    assert target.exists()
