from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from tracelane.acquisition.contracts import compute_candidate_id
from tracelane.evidence_registry.index import (
    EvidenceQuery,
    find_evidence,
    rebuild_evidence_indexes,
    verify_evidence_registry,
)
from tracelane.hist001 import HIST001_SOURCE_MANIFEST

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / "evidence"


def _candidate_documents() -> dict[str, dict[str, object]]:
    documents: dict[str, dict[str, object]] = {}
    for path in sorted((EVIDENCE_ROOT / "projects" / "hist-001" / "candidates").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        documents[str(value["candidate_id"])] = value
    return documents


def _expected_candidates() -> dict[str, tuple[str, str]]:
    expected: dict[str, tuple[str, str]] = {}
    for spec in HIST001_SOURCE_MANIFEST:
        content_sha256 = hashlib.sha256(spec.note.encode("utf-8")).hexdigest()
        for document_date in spec.document_date.split("/"):
            date_precision = {4: "year", 7: "month", 10: "day"}[len(document_date)]
            candidate_id = compute_candidate_id(
                query=spec.query,
                title=spec.title,
                source_url=spec.source_url,
                document_date=document_date,
                date_precision=date_precision,
                content_sha256=content_sha256,
            )
            expected[candidate_id] = (spec.source_spec_id, content_sha256)
    return expected


def test_hist001_registry_has_nine_pending_candidates() -> None:
    report = verify_evidence_registry(EVIDENCE_ROOT, "hist-001")

    assert report.candidate_count == 9
    assert report.status_counts["pending"] == 9
    assert all(count == 0 for status, count in report.status_counts.items() if status != "pending")
    assert report.future_control_count == 1
    assert report.review_count == 0


def test_hist001_clean_query_excludes_future_control() -> None:
    clean = find_evidence(
        EVIDENCE_ROOT,
        EvidenceQuery(project_id="hist-001", clean_only=True),
    )

    assert len(clean) == 8
    assert all(item.role == "evidence" for item in clean)


def test_hist001_project_and_candidates_match_authenticated_source_manifest() -> None:
    project = json.loads(
        (EVIDENCE_ROOT / "projects" / "hist-001" / "project.json").read_text(encoding="utf-8")
    )
    candidates = _candidate_documents()

    assert project["research_question"] == (
        "How might European history have changed if Napoleon had not crossed the "
        "Niemen or launched the Russian campaign in 1812?"
    )
    assert project["historical_cutoff_at"] == "1812-06-23T23:59:59Z"
    assert project["intervention"] == (
        "Napoleon does not cross the Niemen or launch the Russian campaign."
    )
    assert project["required_domains"] == [
        "diplomacy",
        "economy",
        "iberia",
        "imperial-governance",
        "logistics",
        "military",
    ]
    assert project["admitted_source_types"] == ["primary"]
    assert len({item[0] for item in _expected_candidates().values()}) == 7
    assert set(candidates) == set(_expected_candidates())
    for candidate_id, (source_spec_id, content_sha256) in _expected_candidates().items():
        candidate = candidates[candidate_id]
        assert candidate["source_spec_id"] == source_spec_id
        assert candidate["content_sha256"] == content_sha256
        assert candidate["source_candidate_id"] == candidate_id
        assert candidate["source_candidate_content_sha256"] == content_sha256
        assert candidate["source_candidate_record_sha256"]


def test_hist001_registry_has_no_reviews_or_sensitive_paths_and_rebuilds_identically(
    tmp_path: Path,
) -> None:
    evidence_bytes = b"\n".join(
        path.read_bytes() for path in sorted(EVIDENCE_ROOT.rglob("*")) if path.is_file()
    )
    target = tmp_path / "evidence"
    shutil.copytree(EVIDENCE_ROOT, target)
    before = {
        path.relative_to(target): path.read_bytes()
        for path in sorted(target.rglob("*"))
        if path.is_file()
    }

    rebuild_evidence_indexes(target, "hist-001")
    after = {
        path.relative_to(target): path.read_bytes()
        for path in sorted(target.rglob("*"))
        if path.is_file()
    }

    assert not list((EVIDENCE_ROOT / "projects" / "hist-001" / "reviews").glob("*.json"))
    assert not (REPO_ROOT / "fixtures" / "v0.2").exists()
    assert b"D:\\" not in evidence_bytes
    assert b"C:\\" not in evidence_bytes
    assert b"/home/" not in evidence_bytes
    assert b".local/runtime.json" not in evidence_bytes
    assert before == after
