from __future__ import annotations

import importlib.util
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

from scripts import hist001_manifest
from scripts import import_hist001_evidence as hist001_import
from scripts import prepare_hist001_candidates as hist001_preparation
from tracelane.evidence_registry.contracts import ProjectEvidenceCandidate
from tracelane.evidence_registry.index import (
    EvidenceQuery,
    find_evidence,
    rebuild_evidence_indexes,
    verify_evidence_registry,
)
from tracelane.evidence_registry.reviews import EvidenceReview, append_review
from tracelane.security import classify_and_redact

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / "evidence"
_SHA256_TEXT = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
EXPECTED_CANDIDATES = {
    "candidate_11e5aa586f46c2491dab8562": (
        "86a0081c91b453d5caab63c60e6c5506db27534616b5cd12663905ddc13a3afa",
        "ae443edfdeaa745550e986b21f59b11ddf351e2e5263a89e69b801eda363d2b4",
        "hist001_twenty_ninth_bulletin",
        "future-control",
        "29th Bulletin of the Grande Armée, 3 December 1812",
        "1812-12-03",
    ),
    "candidate_1bcaad938bf0a3dc0abfa91b": (
        "0f55a256037f996601651e3b33aee45f4f61a3ffd55bcc605aef562ae792edb8",
        "adf039505c7f6b9315349541db3be647bbe6e4bc2aaafe5180be1630f3571954",
        "hist001_french_conscription_1811",
        "evidence",
        "Council of State recommendation on the 1811 conscription",
        "1811",
    ),
    "candidate_58a3dfb2f562b9d5da7e6131": (
        "ce6cf4280ec34073279181f222b01920baa8bc1bc0a28528d2ec8a5f7549aea2",
        "21df77bf225bad3608d1d0df462c19810ec6612d6c6fdb1f7fa503079d2310f2",
        "hist001_wellington_iberia_dispatch",
        "evidence",
        "Wellington to Liverpool, Villa Fermosa, 7 May 1811",
        "1811-05-07",
    ),
    "candidate_5a6f72798147eff0b3907e39": (
        "c6c41108a013afce374556a2dc469b1c6778a531478563cf560d2bc79b54ccb1",
        "20257565de8690cafe13e64aead04a82d78aa4f649cfdcd24ac4c4f7da39001a",
        "hist001_napoleon_supply_correspondence",
        "evidence",
        "Napoleon's pre-campaign correspondence on supplies, March 1812",
        "1812-03-26",
    ),
    "candidate_73605662756b5b352e0c7b63": (
        "c1921564cf49038fec92f11dbc03178f785422f3dc1107ec9ac3b48a12b7231e",
        "9d556c77aeaadfdfcc59bcbdce0ed95825dc89427f0feb44beaa87661b7a7fdc",
        "hist001_russian_trade_1811",
        "evidence",
        "Russian arrangements for foreign trade in 1811",
        "1810-12-19",
    ),
    "candidate_819bf107d321427970ba8953": (
        "b2322e787f56c19e98a588ad5a780bc3f5a51ff4a236d042c3beaaa7458c57e9",
        "adf039505c7f6b9315349541db3be647bbe6e4bc2aaafe5180be1630f3571954",
        "hist001_french_conscription_1811",
        "evidence",
        "Council of State recommendation on the 1811 conscription",
        "1810-12-13",
    ),
    "candidate_a886d1c1509640022197f00b": (
        "818562df99cc27fb83bf0aecde6e1ede6441eb757854e6b3041794fdd6b3999b",
        "8928c7d0492a013bf3fb4cca138461208d1b90ecfe5c364d6bd102a86cd519e8",
        "hist001_continental_system_decrees",
        "evidence",
        "Documents upon the Continental System: Berlin and Milan Decrees",
        "1807-12-17",
    ),
    "candidate_d3a9c79200d59227a7fdb65d": (
        "beb724b8f736966aa13827df6c6d10974fcd136264fc0de6ddf00e9b4d963ac6",
        "8928c7d0492a013bf3fb4cca138461208d1b90ecfe5c364d6bd102a86cd519e8",
        "hist001_continental_system_decrees",
        "evidence",
        "Documents upon the Continental System: Berlin and Milan Decrees",
        "1806-11-21",
    ),
    "candidate_fce1daafae049933747fef6f": (
        "bca0c3324d634a2cd2df03f6a806a7bfca31048a84f5223a8a068e6895c7297e",
        "2669b2282220c6fbdb2c16c14d4470ba99a3f1299ff911df5d66a977c424c1a3",
        "hist001_tilsit_treaty",
        "evidence",
        "Treaty of Tilsit, 9 July 1807",
        "1807-07-09",
    ),
}


def _candidate_documents(root: Path = EVIDENCE_ROOT) -> dict[str, dict[str, object]]:
    documents: dict[str, dict[str, object]] = {}
    for path in sorted((root / "projects" / "hist-001" / "candidates").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        documents[str(value["candidate_id"])] = value
    return documents


def _tree_state(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_candidate_identities(candidates: dict[str, dict[str, object]]) -> None:
    assert len({item[2] for item in EXPECTED_CANDIDATES.values()}) == 7
    assert set(candidates) == set(EXPECTED_CANDIDATES)
    for candidate_id, expected in EXPECTED_CANDIDATES.items():
        candidate = candidates[candidate_id]
        assert (
            candidate["source_candidate_record_sha256"],
            candidate["source_candidate_content_sha256"],
            candidate["source_spec_id"],
            candidate["role"],
            candidate["title"],
            candidate["document_date"],
        ) == expected
        assert candidate["candidate_id"] == candidate_id
        assert candidate["source_candidate_id"] == candidate_id
        assert candidate["content_sha256"] == expected[1]
    future_control = candidates["candidate_11e5aa586f46c2491dab8562"]
    assert future_control["role"] == "future-control"
    assert future_control["title"] == "29th Bulletin of the Grande Armée, 3 December 1812"
    assert future_control["document_date"] == "1812-12-03"
    assert all(
        candidate["role"] == "evidence"
        for candidate_id, candidate in candidates.items()
        if candidate_id != "candidate_11e5aa586f46c2491dab8562"
    )


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
    assert {item.candidate_id for item in clean} == {
        candidate_id
        for candidate_id, (_, _, _, role, _, _) in EXPECTED_CANDIDATES.items()
        if role == "evidence"
    }


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
    _assert_candidate_identities(candidates)


def test_hist001_manifest_is_single_authoritative_nonruntime_script_data() -> None:
    assert importlib.util.find_spec("tracelane.hist001") is None
    assert hist001_preparation.SPECS is hist001_manifest.HIST001_SOURCE_MANIFEST
    assert hist001_import.HIST001_SOURCE_MANIFEST is hist001_manifest.HIST001_SOURCE_MANIFEST
    assert {spec.source_spec_id for spec in hist001_manifest.HIST001_SOURCE_MANIFEST} == {
        item[2] for item in EXPECTED_CANDIDATES.values()
    }


def test_tracked_evidence_text_has_no_sensitive_or_absolute_local_paths() -> None:
    sentinels = (
        r"E:\private\evidence.txt",
        r"\\server\share\evidence.txt",
        r"\\?\C:\private\evidence.txt",
        "/Users/operator/private/evidence.txt",
        "/opt/tracelane/private/evidence.txt",
        "operator@example.com",
    )
    assert all(classify_and_redact(item).redaction_applied for item in sentinels)

    violations: list[str] = []
    for path in sorted(EVIDENCE_ROOT.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        classifier_input = _SHA256_TEXT.sub("[SHA256]", text)
        if classify_and_redact(classifier_input).redaction_applied or ".local/runtime.json" in text:
            violations.append(path.relative_to(REPO_ROOT).as_posix())

    assert violations == []


def test_hist001_stale_source_rebuild_updates_copy_without_changing_tracked_state(
    tmp_path: Path,
) -> None:
    tracked_before = _tree_state(EVIDENCE_ROOT)
    target = tmp_path / "evidence"
    shutil.copytree(EVIDENCE_ROOT, target)
    before = _tree_state(target)
    candidate_path = (
        target / "projects" / "hist-001" / "candidates" / "candidate_fce1daafae049933747fef6f.json"
    )
    candidate = ProjectEvidenceCandidate.from_dict(
        json.loads(candidate_path.read_text(encoding="utf-8"))
    )
    review = EvidenceReview.create(
        candidate,
        decision="approved",
        reason="The authenticated primary source supports the scoped facts.",
        reviewer="history-reviewer",
        reviewed_at=datetime(2026, 7, 25, tzinfo=UTC),
        approved_fact_ids=candidate.fact_ids,
        approved_domains=candidate.domains,
    )
    stale_index = (target / "projects" / "hist-001" / "index.json").read_bytes()
    stale_registry = (target / "registry.json").read_bytes()

    append_review(target, review)
    assert (target / "projects" / "hist-001" / "index.json").read_bytes() == stale_index
    assert (target / "registry.json").read_bytes() == stale_registry
    rebuild_evidence_indexes(target, "hist-001")
    after = _tree_state(target)
    report = verify_evidence_registry(target, "hist-001")
    rebuild_evidence_indexes(target, "hist-001")
    after_second_rebuild = _tree_state(target)
    tracked_after = _tree_state(EVIDENCE_ROOT)

    assert not list((EVIDENCE_ROOT / "projects" / "hist-001" / "reviews").glob("*.json"))
    assert not (REPO_ROOT / "fixtures" / "v0.2").exists()
    assert report.review_count == 1
    assert report.status_counts == {
        "pending": 8,
        "approved": 1,
        "rejected": 0,
        "superseded": 0,
    }
    assert before != after
    assert after == after_second_rebuild
    assert tracked_before == tracked_after
