from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tracelane.contracts import canonical_json, parse_utc
from tracelane.evidence_registry.contracts import (
    ProjectEvidenceCandidate,
    _digest,
    _format_utc,
    _non_empty,
    _require_project_id,
    _sorted_unique_strings,
    _validate_persisted_json,
    candidate_record_digest,
)
from tracelane.evidence_registry.storage import (
    EvidenceRoot,
    write_json_create_or_match,
)
from tracelane.v2.contracts import ArtifactRef, make_object_id
from tracelane.v2.schema import validate_document

_CANDIDATE_ID = re.compile(r"^candidate_[0-9a-f]{24}$")
_REVIEW_ID = re.compile(r"^review_[0-9a-f]{24}$")
_DECISIONS = frozenset({"approved", "rejected", "superseded"})
_RETENTION_POLICIES = frozenset(
    {"paraphrase_only", "public_domain_full_text", "licensed_full_text"}
)

ReviewDecision = Literal["approved", "rejected", "superseded"]
EffectiveStatus = Literal["pending", "approved", "rejected", "superseded"]


def _validated_candidate(
    candidate: ProjectEvidenceCandidate,
) -> ProjectEvidenceCandidate:
    if not isinstance(candidate, ProjectEvidenceCandidate):
        raise ValueError("candidate record hash is invalid")
    try:
        candidate.to_dict()
    except (TypeError, ValueError) as exc:
        raise ValueError("candidate record hash is invalid") from exc
    return candidate


def _review_identity(value: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "project_id",
        "candidate_id",
        "candidate_record_sha256",
        "decision",
        "reason",
        "reviewer",
        "reviewed_at",
        "approved_fact_ids",
        "approved_domains",
        "license_basis",
        "retention_policy",
    )
    identity = {key: value[key] for key in keys}
    if "supersedes_review_id" in value:
        identity["supersedes_review_id"] = value["supersedes_review_id"]
    return identity


def _validate_current_binding(
    candidate: ProjectEvidenceCandidate,
    review: EvidenceReview,
) -> None:
    if review.license_basis != candidate.license_basis:
        raise ValueError("review license basis does not match candidate")
    if review.retention_policy != candidate.retention_policy:
        raise ValueError("review retention policy does not match candidate")
    if review.decision == "approved":
        if not set(review.approved_fact_ids).issubset(candidate.fact_ids):
            raise ValueError("approved fact IDs are outside candidate scope")
        if not set(review.approved_domains).issubset(candidate.domains):
            raise ValueError("approved domains are outside candidate scope")


@dataclass(frozen=True)
class EvidenceReview:
    schema_id: str
    schema_version: str
    record_sha256: str
    review_id: str
    project_id: str
    candidate_id: str
    candidate_record_sha256: str
    decision: ReviewDecision
    reason: str
    reviewer: str
    reviewed_at: datetime
    approved_fact_ids: tuple[str, ...]
    approved_domains: tuple[str, ...]
    license_basis: str
    retention_policy: Literal[
        "paraphrase_only", "public_domain_full_text", "licensed_full_text"
    ]
    supersedes_review_id: str | None = None

    @classmethod
    def create(
        cls,
        candidate: ProjectEvidenceCandidate,
        *,
        decision: ReviewDecision,
        reason: str,
        reviewer: str,
        reviewed_at: datetime,
        approved_fact_ids: Sequence[str],
        approved_domains: Sequence[str],
        supersedes_review_id: str | None = None,
    ) -> EvidenceReview:
        candidate = _validated_candidate(candidate)
        fact_ids = _sorted_unique_strings(
            approved_fact_ids,
            "approved_fact_ids",
            required=False,
        )
        domains = _sorted_unique_strings(
            approved_domains,
            "approved_domains",
            required=False,
        )
        value: dict[str, object] = {
            "schema_id": "tracelane://schemas/evidence-review/v1",
            "schema_version": "1.0.0",
            "record_sha256": "",
            "review_id": "",
            "project_id": candidate.project_id,
            "candidate_id": candidate.candidate_id,
            "candidate_record_sha256": candidate.record_sha256,
            "decision": decision,
            "reason": reason,
            "reviewer": reviewer,
            "reviewed_at": _format_utc(reviewed_at, "reviewed_at"),
            "approved_fact_ids": list(fact_ids),
            "approved_domains": list(domains),
            "license_basis": candidate.license_basis,
            "retention_policy": candidate.retention_policy,
        }
        if supersedes_review_id is not None:
            value["supersedes_review_id"] = supersedes_review_id
        value["review_id"] = make_object_id("review", _review_identity(value))
        value["record_sha256"] = candidate_record_digest(value)
        review = cls.from_dict(value)
        _validate_current_binding(candidate, review)
        return review

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvidenceReview:
        if isinstance(value, Mapping):
            for field in (
                "reason",
                "reviewer",
                "license_basis",
                "approved_fact_ids",
                "approved_domains",
            ):
                if field in value:
                    _validate_persisted_json(value[field], field)
        validate_document("evidence-review", value)
        review = cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            record_sha256=str(value["record_sha256"]),
            review_id=str(value["review_id"]),
            project_id=str(value["project_id"]),
            candidate_id=str(value["candidate_id"]),
            candidate_record_sha256=str(value["candidate_record_sha256"]),
            decision=str(value["decision"]),  # type: ignore[arg-type]
            reason=str(value["reason"]),
            reviewer=str(value["reviewer"]),
            reviewed_at=parse_utc(str(value["reviewed_at"])),
            approved_fact_ids=_sorted_unique_strings(
                value["approved_fact_ids"],
                "approved_fact_ids",
                required=False,
            ),
            approved_domains=_sorted_unique_strings(
                value["approved_domains"],
                "approved_domains",
                required=False,
            ),
            license_basis=str(value["license_basis"]),
            retention_policy=str(value["retention_policy"]),  # type: ignore[arg-type]
            supersedes_review_id=(
                str(value["supersedes_review_id"])
                if "supersedes_review_id" in value
                else None
            ),
        )
        _require_project_id(review.project_id)
        if _CANDIDATE_ID.fullmatch(review.candidate_id) is None:
            raise ValueError("candidate_id is invalid")
        _digest(review.candidate_record_sha256, "candidate_record_sha256")
        if _REVIEW_ID.fullmatch(review.review_id) is None:
            raise ValueError("review_id is invalid")
        if review.decision not in _DECISIONS:
            raise ValueError("review decision is invalid")
        for item, label in (
            (review.reason, "reason"),
            (review.reviewer, "reviewer"),
            (review.license_basis, "license_basis"),
        ):
            _non_empty(item, label)
        if review.retention_policy not in _RETENTION_POLICIES:
            raise ValueError("retention policy is invalid")
        if review.supersedes_review_id is not None:
            if _REVIEW_ID.fullmatch(review.supersedes_review_id) is None:
                raise ValueError("supersedes_review_id is invalid")
            if review.supersedes_review_id == review.review_id:
                raise ValueError("review cannot supersede itself")
        if review.decision == "approved":
            if not review.approved_fact_ids or not review.approved_domains:
                raise ValueError("approved review scope must not be empty")
        elif review.approved_fact_ids or review.approved_domains:
            raise ValueError("review decision requires empty approved scope")
        raw = review._raw_dict()
        if make_object_id("review", _review_identity(raw)) != review.review_id:
            raise ValueError("review_id does not match review identity")
        if candidate_record_digest(raw) != review.record_sha256:
            raise ValueError("review record digest is stale")
        return review

    def _raw_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "record_sha256": self.record_sha256,
            "review_id": self.review_id,
            "project_id": self.project_id,
            "candidate_id": self.candidate_id,
            "candidate_record_sha256": self.candidate_record_sha256,
            "decision": self.decision,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "reviewed_at": _format_utc(self.reviewed_at, "reviewed_at"),
            "approved_fact_ids": list(self.approved_fact_ids),
            "approved_domains": list(self.approved_domains),
            "license_basis": self.license_basis,
            "retention_policy": self.retention_policy,
        }
        if self.supersedes_review_id is not None:
            value["supersedes_review_id"] = self.supersedes_review_id
        return value

    def to_dict(self) -> dict[str, object]:
        type(self).from_dict(self._raw_dict())
        return json.loads(canonical_json(self._raw_dict()))


@dataclass(frozen=True)
class ReviewChain:
    ordered: tuple[EvidenceReview, ...]
    head: EvidenceReview | None
    effective_status: EffectiveStatus


def validate_review_chain(
    candidate: ProjectEvidenceCandidate,
    reviews: Sequence[EvidenceReview],
) -> ReviewChain:
    candidate = _validated_candidate(candidate)
    if isinstance(reviews, (str, bytes)) or not isinstance(reviews, Sequence):
        raise ValueError("review chain must be a sequence")
    values = tuple(reviews)
    validated: list[EvidenceReview] = []
    for review in values:
        if not isinstance(review, EvidenceReview):
            raise ValueError("review chain contains an invalid review")
        review.to_dict()
        if review.project_id != candidate.project_id:
            raise ValueError("review project_id does not match candidate")
        if review.candidate_id != candidate.candidate_id:
            raise ValueError("review candidate_id does not match candidate")
        if review.candidate_record_sha256 == candidate.record_sha256:
            _validate_current_binding(candidate, review)
        validated.append(review)
    if not validated:
        return ReviewChain(ordered=(), head=None, effective_status="pending")

    by_id = {review.review_id: review for review in validated}
    if len(by_id) != len(validated):
        raise ValueError("review chain contains duplicate review IDs")
    roots = [review for review in validated if review.supersedes_review_id is None]
    if len(roots) != 1:
        raise ValueError("review chain must have exactly one root")

    successors: dict[str, EvidenceReview] = {}
    for review in validated:
        predecessor = review.supersedes_review_id
        if predecessor is None:
            continue
        if predecessor not in by_id:
            raise ValueError("review chain has a missing predecessor")
        if predecessor in successors:
            raise ValueError("review chain fork does not supersede the current head")
        successors[predecessor] = review

    ordered: list[EvidenceReview] = []
    seen: set[str] = set()
    review: EvidenceReview | None = roots[0]
    while review is not None:
        if review.review_id in seen:
            raise ValueError("review chain contains a cycle")
        seen.add(review.review_id)
        ordered.append(review)
        review = successors.get(review.review_id)
    if len(ordered) != len(validated):
        raise ValueError("review chain contains a cycle or unconnected component")

    head = ordered[-1]
    status: EffectiveStatus = (
        head.decision
        if head.candidate_record_sha256 == candidate.record_sha256
        else "pending"
    )
    return ReviewChain(ordered=tuple(ordered), head=head, effective_status=status)


def effective_status(
    candidate: ProjectEvidenceCandidate,
    reviews: Sequence[EvidenceReview],
) -> EffectiveStatus:
    return validate_review_chain(candidate, reviews).effective_status


def current_review(
    candidate: ProjectEvidenceCandidate,
    reviews: Sequence[EvidenceReview],
) -> EvidenceReview | None:
    chain = validate_review_chain(candidate, reviews)
    if chain.effective_status == "pending":
        return None
    return chain.head


def append_review(root: EvidenceRoot, review: EvidenceReview) -> ArtifactRef:
    if not isinstance(review, EvidenceReview):
        raise ValueError("review record is invalid")
    value = review.to_dict()
    uri = (
        f"tracelane://evidence/projects/{review.project_id}/reviews/"
        f"{review.review_id}.json"
    )
    return write_json_create_or_match(
        root,
        uri,
        "evidence_review",
        "tracelane://schemas/evidence-review/v1",
        value,
    )
