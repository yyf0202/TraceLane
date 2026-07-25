from __future__ import annotations

import hashlib
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracelane.acquisition.contracts import compute_candidate_id
from tracelane.evidence_registry import reviews as evidence_reviews
from tracelane.evidence_registry import storage as evidence_storage
from tracelane.evidence_registry.contracts import (
    EvidenceProject,
    ProjectEvidenceCandidate,
    candidate_record_digest,
)
from tracelane.evidence_registry.reviews import (
    EvidenceReview,
    append_review,
    current_review,
    effective_status,
    validate_review_chain,
)
from tracelane.evidence_registry.storage import (
    EvidenceBlobStore,
    EvidenceRoot,
    write_json_create_or_match,
)
from tracelane.v2 import locking as v2_locking
from tracelane.v2.contracts import ArtifactRef, make_object_id
from tracelane.v2.schema import SchemaValidationError, validate_document


def _candidate() -> ProjectEvidenceCandidate:
    digest = hashlib.sha256(b"curated evidence").hexdigest()
    content_ref = ArtifactRef.from_dict(
        {
            "kind": "evidence_blob",
            "uri": f"tracelane://evidence/blobs/sha256/{digest}",
            "media_type": "text/plain",
            "sha256": digest,
            "size_bytes": len(b"curated evidence"),
        }
    )
    candidate_id = compute_candidate_id(
        query="Napoleon 1812 supply correspondence",
        title="March 1812 supply correspondence",
        source_url="https://history.example/napoleon-supply",
        document_date="1812-03",
        date_precision="month",
        content_sha256=digest,
    )
    return ProjectEvidenceCandidate.create(
        project_id="hist-001",
        candidate_id=candidate_id,
        source_spec_id="hist001_supply",
        query="Napoleon 1812 supply correspondence",
        title="March 1812 supply correspondence",
        source_url="https://history.example/napoleon-supply",
        document_date="1812-03",
        date_precision="month",
        retrieved_at=datetime(2026, 7, 25, tzinfo=UTC),
        curator="repository curator",
        source_type="primary",
        role="evidence",
        domains=("logistics", "military"),
        fact_ids=("logistics.prewar_supply", "military.force_readiness"),
        content_ref=content_ref,
        transformation_refs=(),
        content_authorship="repository_authored",
        retention_policy="paraphrase_only",
        license_basis="Repository-authored paraphrase.",
        acquisition_session_id="acq_hist001_20260724",
        source_candidate_uri="tracelane://artifacts/candidates/supply.json",
        source_candidate_id=candidate_id,
        source_candidate_record_sha256="a" * 64,
        source_candidate_content_sha256=digest,
    )


@pytest.fixture
def candidate() -> ProjectEvidenceCandidate:
    return _candidate()


def _review(
    candidate: ProjectEvidenceCandidate,
    *,
    decision: str = "approved",
    reason: str = "The retained scope is supported.",
    reviewer: str = "history-reviewer",
    approved_fact_ids: tuple[str, ...] | None = None,
    approved_domains: tuple[str, ...] | None = None,
    supersedes_review_id: str | None = None,
    reviewed_at: datetime = datetime(2026, 7, 25, 8, 0, tzinfo=UTC),
) -> EvidenceReview:
    if approved_fact_ids is None:
        approved_fact_ids = ("logistics.prewar_supply",) if decision == "approved" else ()
    if approved_domains is None:
        approved_domains = ("logistics",) if decision == "approved" else ()
    return EvidenceReview.create(
        candidate,
        decision=decision,
        reason=reason,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        approved_fact_ids=approved_fact_ids,
        approved_domains=approved_domains,
        supersedes_review_id=supersedes_review_id,
    )


@pytest.fixture
def approved_review(candidate: ProjectEvidenceCandidate) -> EvidenceReview:
    return _review(candidate)


def _revised_candidate(
    candidate: ProjectEvidenceCandidate, **changes: object
) -> ProjectEvidenceCandidate:
    value = candidate.to_dict()
    value.update(changes)
    value["record_sha256"] = candidate_record_digest(value)
    return ProjectEvidenceCandidate.from_dict(value)


def _published_candidate_root(
    path: Path,
    candidate: ProjectEvidenceCandidate | None,
) -> EvidenceRoot:
    root = EvidenceRoot.create(path)
    project = EvidenceProject.create(
        project_id="hist-001",
        title="HIST-001",
        research_question="What evidence supports the counterfactual?",
        historical_cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        intervention="Napoleon does not cross the Niemen.",
        required_domains=("logistics", "military"),
        admitted_source_types=("primary",),
        status="active",
    )
    write_json_create_or_match(
        root,
        "tracelane://evidence/projects/hist-001/project.json",
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        project.to_dict(),
    )
    if candidate is not None:
        EvidenceBlobStore(root).put_bytes(
            b"curated evidence",
            "text/plain",
            "evidence_blob",
        )
        write_json_create_or_match(
            root,
            (f"tracelane://evidence/projects/hist-001/candidates/{candidate.candidate_id}.json"),
            "evidence_candidate",
            "tracelane://schemas/project-evidence-candidate/v1",
            candidate.to_dict(),
        )
    return root


def test_no_review_is_pending(candidate: ProjectEvidenceCandidate) -> None:
    chain = validate_review_chain(candidate, ())

    assert chain.ordered == ()
    assert chain.head is None
    assert chain.effective_status == "pending"
    assert current_review(candidate, ()) is None
    assert effective_status(candidate, ()) == "pending"


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_first_review_sets_effective_status(
    candidate: ProjectEvidenceCandidate, decision: str
) -> None:
    review = _review(candidate, decision=decision)

    assert EvidenceReview.from_dict(review.to_dict()) == review
    assert current_review(candidate, (review,)) == review
    assert effective_status(candidate, (review,)) == decision
    assert "supersedes_review_id" not in review.to_dict()


def test_review_id_and_record_digest_follow_wire_identity(
    approved_review: EvidenceReview,
) -> None:
    value = approved_review.to_dict()
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
    assert approved_review.review_id == make_object_id("review", identity)
    assert approved_review.record_sha256 == candidate_record_digest(value)


def test_valid_review_can_supersede_current_head(
    candidate: ProjectEvidenceCandidate, approved_review: EvidenceReview
) -> None:
    rejected = _review(
        candidate,
        decision="rejected",
        reason="The source locator needs correction.",
        reviewed_at=datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
        supersedes_review_id=approved_review.review_id,
    )

    chain = validate_review_chain(candidate, (rejected, approved_review))

    assert chain.ordered == (approved_review, rejected)
    assert chain.head == rejected
    assert chain.effective_status == "rejected"


def test_missing_predecessor_is_rejected(
    candidate: ProjectEvidenceCandidate,
) -> None:
    review = _review(
        candidate,
        supersedes_review_id="review_" + "f" * 24,
    )

    with pytest.raises(ValueError, match="review chain"):
        validate_review_chain(candidate, (review,))


def test_non_head_predecessor_creates_an_invalid_fork(
    candidate: ProjectEvidenceCandidate, approved_review: EvidenceReview
) -> None:
    second = _review(
        candidate,
        decision="rejected",
        reason="Second decision.",
        reviewed_at=datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
        supersedes_review_id=approved_review.review_id,
    )
    fork = _review(
        candidate,
        decision="superseded",
        reason="Forked decision.",
        reviewed_at=datetime(2026, 7, 25, 10, 0, tzinfo=UTC),
        supersedes_review_id=approved_review.review_id,
    )

    with pytest.raises(ValueError, match="fork|current head"):
        validate_review_chain(candidate, (approved_review, second, fork))


def test_cycle_and_unconnected_component_are_rejected(
    candidate: ProjectEvidenceCandidate, approved_review: EvidenceReview
) -> None:
    second = _review(
        candidate,
        decision="rejected",
        reason="Second decision.",
        reviewed_at=datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
        supersedes_review_id=approved_review.review_id,
    )
    first_value = approved_review.to_dict()
    first_value["supersedes_review_id"] = second.review_id
    identity = {
        key: item
        for key, item in first_value.items()
        if key
        not in {
            "schema_id",
            "schema_version",
            "review_id",
            "record_sha256",
        }
    }
    first_value["review_id"] = make_object_id("review", identity)
    first_value["record_sha256"] = candidate_record_digest(first_value)
    cycle_first = EvidenceReview.from_dict(first_value)
    second_value = second.to_dict()
    second_value["supersedes_review_id"] = cycle_first.review_id
    identity = {
        key: item
        for key, item in second_value.items()
        if key
        not in {
            "schema_id",
            "schema_version",
            "review_id",
            "record_sha256",
        }
    }
    second_value["review_id"] = make_object_id("review", identity)
    second_value["record_sha256"] = candidate_record_digest(second_value)
    cycle_second = EvidenceReview.from_dict(second_value)

    with pytest.raises(ValueError, match="review chain"):
        validate_review_chain(candidate, (cycle_first, cycle_second))

    disconnected = _review(
        candidate,
        decision="rejected",
        reason="Another root.",
        reviewed_at=datetime(2026, 7, 25, 11, 0, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="review chain"):
        validate_review_chain(candidate, (approved_review, disconnected))


def test_stale_candidate_instance_fails_before_lifecycle_evaluation(
    candidate: ProjectEvidenceCandidate, approved_review: EvidenceReview
) -> None:
    stale = replace(candidate, license_basis="changed")

    with pytest.raises(ValueError, match="candidate record hash"):
        effective_status(stale, (approved_review,))


def test_valid_candidate_revision_makes_old_head_historical_and_pending(
    candidate: ProjectEvidenceCandidate, approved_review: EvidenceReview
) -> None:
    revised = _revised_candidate(candidate, license_basis="Revised license basis.")

    assert EvidenceReview.from_dict(approved_review.to_dict()) == approved_review
    assert current_review(revised, (approved_review,)) is None
    assert effective_status(revised, (approved_review,)) == "pending"
    chain = validate_review_chain(revised, (approved_review,))
    assert chain.head == approved_review
    assert chain.effective_status == "pending"


def test_current_revision_review_can_follow_historical_review(
    candidate: ProjectEvidenceCandidate, approved_review: EvidenceReview
) -> None:
    revised = _revised_candidate(candidate, license_basis="Revised license basis.")
    current = _review(
        revised,
        decision="rejected",
        reason="Revised candidate rejected.",
        reviewed_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        supersedes_review_id=approved_review.review_id,
    )

    chain = validate_review_chain(revised, (current, approved_review))

    assert chain.ordered == (approved_review, current)
    assert current_review(revised, chain.ordered) == current
    assert effective_status(revised, chain.ordered) == "rejected"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "other-001"),
        ("candidate_id", "candidate_" + "f" * 24),
    ],
)
def test_cross_project_or_candidate_review_is_rejected(
    candidate: ProjectEvidenceCandidate,
    approved_review: EvidenceReview,
    field: str,
    value: str,
) -> None:
    review_value = approved_review.to_dict()
    review_value[field] = value
    identity = {
        key: item
        for key, item in review_value.items()
        if key
        not in {
            "schema_id",
            "schema_version",
            "review_id",
            "record_sha256",
        }
    }
    review_value["review_id"] = make_object_id("review", identity)
    review_value["record_sha256"] = candidate_record_digest(review_value)
    review = EvidenceReview.from_dict(review_value)

    with pytest.raises(ValueError, match=field):
        validate_review_chain(candidate, (review,))


def test_approval_can_reduce_candidate_fact_and_domain_scope(
    candidate: ProjectEvidenceCandidate,
) -> None:
    review = _review(candidate)

    assert review.approved_fact_ids == ("logistics.prewar_supply",)
    assert review.approved_domains == ("logistics",)
    assert effective_status(candidate, (review,)) == "approved"


@pytest.mark.parametrize(
    ("decision", "fact_ids", "domains"),
    [
        ("approved", (), ("logistics",)),
        ("approved", ("not.proposed",), ("logistics",)),
        ("approved", ("logistics.prewar_supply",), ("not-proposed",)),
        ("rejected", ("logistics.prewar_supply",), ()),
        ("superseded", (), ("logistics",)),
    ],
)
def test_review_decision_and_approved_scope_are_enforced(
    candidate: ProjectEvidenceCandidate,
    decision: str,
    fact_ids: tuple[str, ...],
    domains: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="approved|decision"):
        _review(
            candidate,
            decision=decision,
            approved_fact_ids=fact_ids,
            approved_domains=domains,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approved_fact_ids", ""),
        ("approved_domains", ""),
    ],
)
def test_create_rejects_string_values_for_scope_arrays(
    candidate: ProjectEvidenceCandidate,
    field: str,
    value: str,
) -> None:
    arguments: dict[str, object] = {
        "decision": "rejected",
        "reason": "The candidate is rejected.",
        "reviewer": "history-reviewer",
        "reviewed_at": datetime(2026, 7, 25, 8, 0, tzinfo=UTC),
        "approved_fact_ids": (),
        "approved_domains": (),
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=field):
        EvidenceReview.create(candidate, **arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("license_basis", "Different basis."),
        ("retention_policy", "licensed_full_text"),
    ],
)
def test_current_review_must_match_candidate_rights(
    candidate: ProjectEvidenceCandidate,
    approved_review: EvidenceReview,
    field: str,
    replacement: str,
) -> None:
    review_value = approved_review.to_dict()
    review_value[field] = replacement
    identity = {
        key: item
        for key, item in review_value.items()
        if key
        not in {
            "schema_id",
            "schema_version",
            "review_id",
            "record_sha256",
        }
    }
    review_value["review_id"] = make_object_id("review", identity)
    review_value["record_sha256"] = candidate_record_digest(review_value)
    review = EvidenceReview.from_dict(review_value)

    with pytest.raises(ValueError, match=field.replace("_", " ")):
        validate_review_chain(candidate, (review,))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("reason", r"token=C:\private\secret.txt"),
        ("reviewer", ".local/runtime.json"),
        ("license_basis", "Bearer sk-abcdefghijklmnop"),
        ("approved_fact_ids", ["safe", r"C:\Users\operator\fact"]),
        ("approved_domains", [".local/domain"]),
    ],
)
def test_review_rejects_sensitive_or_local_persisted_text_without_echo(
    approved_review: EvidenceReview,
    field: str,
    replacement: object,
) -> None:
    value = approved_review.to_dict()
    value[field] = replacement

    with pytest.raises(ValueError, match="sensitive text") as captured:
        EvidenceReview.from_dict(value)

    assert str(replacement) not in str(captured.value)


@pytest.mark.parametrize(
    "replacement",
    [
        [r"C:\private\review-tag.txt", r"C:\private\review-tag.txt"],
        r"C:\private\review-tag.txt",
    ],
)
def test_malformed_sensitive_scope_never_echoes_before_schema_validation(
    approved_review: EvidenceReview,
    replacement: object,
) -> None:
    value = approved_review.to_dict()
    value["approved_fact_ids"] = replacement

    with pytest.raises(ValueError, match="sensitive text") as captured:
        EvidenceReview.from_dict(value)

    assert r"C:\private\review-tag.txt" not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "sensitive"),
    [
        ("decision", r"C:\private\decision.txt"),
        ("reviewed_at", ".local/runtime.json"),
    ],
)
def test_sensitive_structural_values_never_reach_echoing_schema_errors(
    approved_review: EvidenceReview,
    field: str,
    sensitive: str,
) -> None:
    value = approved_review.to_dict()
    value[field] = sensitive

    with pytest.raises(ValueError, match="^review contains sensitive text$") as captured:
        EvidenceReview.from_dict(value)

    assert sensitive not in str(captured.value)


def test_sensitive_unknown_key_never_reaches_echoing_schema_error(
    approved_review: EvidenceReview,
) -> None:
    sensitive = r"C:\private\unknown.txt"
    value = approved_review.to_dict()
    value[sensitive] = "unexpected"

    with pytest.raises(ValueError, match="^review contains sensitive text$") as captured:
        EvidenceReview.from_dict(value)

    assert sensitive not in str(captured.value)


def test_sensitive_unknown_key_collision_uses_stable_no_echo_error(
    approved_review: EvidenceReview,
) -> None:
    first = r"C:\private\first.txt"
    second = r"D:\private\second.txt"
    value = approved_review.to_dict()
    value[first] = "unexpected"
    value[second] = "unexpected"

    with pytest.raises(ValueError, match="^review contains sensitive text$") as captured:
        EvidenceReview.from_dict(value)

    assert first not in str(captured.value)
    assert second not in str(captured.value)


def test_non_mapping_review_root_fails_without_echoing_nested_sensitive_text() -> None:
    sensitive = r"C:\private\review-root.txt"

    with pytest.raises(ValueError, match="^review record is invalid$") as captured:
        EvidenceReview.from_dict([sensitive])  # type: ignore[arg-type]

    assert sensitive not in str(captured.value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("reason", " \t "),
        ("reviewer", " \t "),
        ("license_basis", " \t "),
        ("approved_fact_ids", [" \t "]),
        ("approved_domains", [" \t "]),
    ],
)
def test_schema_and_python_reject_whitespace_only_review_text(
    approved_review: EvidenceReview,
    field: str,
    replacement: object,
) -> None:
    value = approved_review.to_dict()
    value[field] = replacement

    with pytest.raises(SchemaValidationError):
        validate_document("evidence-review", value)
    with pytest.raises(ValueError):
        EvidenceReview.from_dict(value)


def test_schema_and_python_cover_review_wire_rules(
    approved_review: EvidenceReview,
) -> None:
    value = approved_review.to_dict()
    validate_document("evidence-review", value)

    invalid_values = []
    for field, replacement in (
        ("decision", "pending"),
        ("record_sha256", "A" * 64),
        ("review_id", "review_bad"),
        ("approved_fact_ids", ["z", "z"]),
        ("retention_policy", "indefinite"),
    ):
        invalid = dict(value)
        invalid[field] = replacement
        invalid_values.append(invalid)
    invalid_values.append(value | {"unexpected": True})
    missing = dict(value)
    missing.pop("reason")
    invalid_values.append(missing)
    rejected_with_scope = dict(value)
    rejected_with_scope["decision"] = "rejected"
    invalid_values.append(rejected_with_scope)

    for invalid in invalid_values:
        with pytest.raises(SchemaValidationError):
            validate_document("evidence-review", invalid)


def test_first_review_schema_omits_predecessor_and_later_review_requires_it(
    candidate: ProjectEvidenceCandidate,
    approved_review: EvidenceReview,
) -> None:
    first = approved_review.to_dict()
    assert "supersedes_review_id" not in first
    validate_document("evidence-review", first)

    later = _review(
        candidate,
        decision="rejected",
        supersedes_review_id=approved_review.review_id,
    ).to_dict()
    assert later["supersedes_review_id"] == approved_review.review_id
    validate_document("evidence-review", later)


def test_append_review_is_idempotent_and_uses_exact_uri(
    tmp_path, approved_review: EvidenceReview
) -> None:
    root = _published_candidate_root(tmp_path / "evidence", _candidate())

    first = append_review(root, approved_review)
    second = append_review(root, approved_review)

    assert first == second
    assert first.kind == "evidence_review"
    assert first.schema_id == "tracelane://schemas/evidence-review/v1"
    assert first.uri == (
        f"tracelane://evidence/projects/hist-001/reviews/{approved_review.review_id}.json"
    )
    assert root.resolve(first.uri).read_bytes().endswith(b"\n")


def test_append_review_lock_failure_has_sanitized_traceback(
    tmp_path: Path,
    approved_review: EvidenceReview,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_path = tmp_path / "private-lock-path"

    @contextmanager
    def fail_lock(target: str | Path):
        del target
        raise OSError(13, "lock denied", str(sensitive_path))
        yield

    monkeypatch.setattr(
        evidence_reviews,
        "evidence_root_mutation_lock",
        fail_lock,
    )

    with pytest.raises(ValueError, match="^review append failed$") as caught:
        append_review(tmp_path / "evidence", approved_review)

    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert caught.value.__cause__ is None
    assert str(tmp_path) not in rendered


def test_append_review_exit_only_lock_validation_failure_preserves_success(
    tmp_path: Path,
    approved_review: EvidenceReview,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _published_candidate_root(tmp_path / "evidence", _candidate())
    original_validate = v2_locking._validate_acquired_lock
    target_lock_name = (
        f"evidence-import-{evidence_storage._evidence_root_parent_identity(root.path)}.lock"
    )
    validations_for_first_lock = 0

    def fail_lock_exit(*args: object, **kwargs: object) -> None:
        nonlocal validations_for_first_lock
        path = Path(args[1])
        original_validate(*args, **kwargs)
        if path.name == target_lock_name:
            validations_for_first_lock += 1
            if validations_for_first_lock == 2:
                raise ValueError("injected exit-only lock validation failure")

    monkeypatch.setattr(v2_locking, "_validate_acquired_lock", fail_lock_exit)

    reference = append_review(root, approved_review)

    assert validations_for_first_lock >= 2
    assert root.resolve(reference.uri, must_exist=True).is_file()


def test_append_review_never_replaces_existing_different_bytes(
    tmp_path, approved_review: EvidenceReview
) -> None:
    root = _published_candidate_root(tmp_path / "evidence", _candidate())
    uri = f"tracelane://evidence/projects/hist-001/reviews/{approved_review.review_id}.json"
    target = root.resolve(uri)
    target.parent.mkdir(parents=True)
    original = b'{"different":true}\n'
    target.write_bytes(original)

    with pytest.raises(ValueError, match="source is invalid|conflict"):
        append_review(root, approved_review)

    assert target.read_bytes() == original


def test_append_review_rejects_orphan_project_and_candidate(
    tmp_path: Path,
    approved_review: EvidenceReview,
) -> None:
    missing_project = EvidenceRoot.create(tmp_path / "missing-project")
    project_only = _published_candidate_root(tmp_path / "project-only", None)

    for root in (missing_project, project_only):
        with pytest.raises(ValueError, match="review append source is invalid"):
            append_review(root, approved_review)

    assert not list(missing_project.path.rglob("*.json"))
    assert not list((project_only.path / "projects" / "hist-001" / "reviews").glob("*.json"))


def test_append_review_rejects_stale_candidate_binding(
    tmp_path: Path,
    candidate: ProjectEvidenceCandidate,
    approved_review: EvidenceReview,
) -> None:
    revised = _revised_candidate(candidate, license_basis="Revised license basis.")
    root = _published_candidate_root(tmp_path / "evidence", revised)

    with pytest.raises(ValueError, match="review append candidate is stale"):
        append_review(root, approved_review)

    assert not list((root.path / "projects" / "hist-001" / "reviews").glob("*.json"))


def test_append_review_rejects_non_head_predecessor(
    tmp_path: Path,
    candidate: ProjectEvidenceCandidate,
    approved_review: EvidenceReview,
) -> None:
    root = _published_candidate_root(tmp_path / "evidence", candidate)
    append_review(root, approved_review)
    second = _review(
        candidate,
        decision="rejected",
        reviewed_at=datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
        supersedes_review_id=approved_review.review_id,
    )
    append_review(root, second)
    fork = _review(
        candidate,
        decision="superseded",
        reviewed_at=datetime(2026, 7, 25, 10, 0, tzinfo=UTC),
        supersedes_review_id=approved_review.review_id,
    )

    with pytest.raises(ValueError, match="review append predecessor is not the current head"):
        append_review(root, fork)

    assert len(list((root.path / "projects" / "hist-001" / "reviews").glob("*.json"))) == 2


def test_concurrent_same_head_review_append_has_one_winner(
    tmp_path: Path,
    candidate: ProjectEvidenceCandidate,
    approved_review: EvidenceReview,
) -> None:
    root = _published_candidate_root(tmp_path / "evidence", candidate)
    append_review(root, approved_review)
    proposed = (
        _review(
            candidate,
            decision="rejected",
            reason="First competing decision.",
            reviewed_at=datetime(2026, 7, 25, 9, 0, tzinfo=UTC),
            supersedes_review_id=approved_review.review_id,
        ),
        _review(
            candidate,
            decision="superseded",
            reason="Second competing decision.",
            reviewed_at=datetime(2026, 7, 25, 9, 1, tzinfo=UTC),
            supersedes_review_id=approved_review.review_id,
        ),
    )

    def publish(review: EvidenceReview) -> str:
        try:
            append_review(root, review)
        except ValueError as exc:
            return str(exc)
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(publish, proposed))

    assert outcomes.count("published") == 1
    assert outcomes.count("review append predecessor is not the current head") == 1
    assert len(list((root.path / "projects" / "hist-001" / "reviews").glob("*.json"))) == 2
