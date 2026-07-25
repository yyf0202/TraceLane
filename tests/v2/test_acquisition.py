from __future__ import annotations

import hashlib
import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts import prepare_hist001_candidates as preparation
from tracelane.acquisition import CandidateReview, ManualAcquisitionService
from tracelane.acquisition import contracts as acquisition_contracts
from tracelane.acquisition import service as acquisition_service
from tracelane.acquisition.contracts import EvidenceCandidate, source_locator_sha256
from tracelane.contracts import canonical_json, sha256_json
from tracelane.history import EvidenceRecordV2
from tracelane.history.contracts import compute_evidence_provenance_sha256
from tracelane.v2 import storage as storage_module
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.manifests import artifact_ref_for_file
from tracelane.v2.storage import ArtifactRoot, BlobStore

NOW = datetime(2026, 7, 24, tzinfo=UTC)
ACQUISITION_CORRUPTIONS = (
    "candidate_traversal",
    "candidate_unc_path",
    "record_substitution",
    "blob_substitution",
    "stale_approval",
    "session_identity_substitution",
)
ACQUISITION_CORRUPTION_EXPECTATIONS: dict[str, tuple[type[ValueError], str]] = {
    "candidate_traversal": (ValueError, "candidate_id is invalid"),
    "candidate_unc_path": (ValueError, "candidate_id is invalid"),
    "record_substitution": (ValueError, "review does not match candidate record"),
    "blob_substitution": (ValueError, "artifact size mismatch"),
    "stale_approval": (ValueError, "review does not match candidate record"),
    "session_identity_substitution": (
        ValueError,
        "acquisition session identity mismatch",
    ),
}


def make_service(root: Path) -> ManualAcquisitionService:
    return ManualAcquisitionService(
        root,
        session_id="acq_hist001_20260724",
        clock=lambda: NOW,
    )


@pytest.fixture
def service(tmp_path: Path) -> ManualAcquisitionService:
    return make_service(tmp_path)


def ingest_candidate(
    service: ManualAcquisitionService,
    **overrides: object,
) -> EvidenceCandidate:
    values: dict[str, object] = {
        "query": "query",
        "title": "Primary source",
        "source_url": "https://history.example/source",
        "document_date": "1812-05",
        "date_precision": "month",
        "curated_text": "source text",
        "curator": "curator-001",
    }
    values.update(overrides)
    return service.ingest(**values)  # type: ignore[arg-type]


def approved_review(candidate: EvidenceCandidate) -> CandidateReview:
    return CandidateReview.create(
        candidate,
        decision="approved",
        reviewer="reviewer",
        reviewed_at=NOW,
        available_at=NOW,
        source_type="primary",
        license="Public-Domain",
        reason="provenance checked",
    )


def promotion_fields() -> dict[str, object]:
    return {
        "evidence_id": "hist-001-ev-0001",
        "known_by_cutoff": "known",
        "excerpt_kind": "paraphrased",
        "fact_ids": ("fact.safe",),
    }


def promote_candidate(
    service: ManualAcquisitionService,
    candidate: EvidenceCandidate,
    review: CandidateReview,
    **overrides: object,
):
    fields = promotion_fields()
    fields.update(overrides)
    return service.promote(
        candidate.candidate_id,
        review,
        **fields,  # type: ignore[arg-type]
    )


def rebuilt_review(review: CandidateReview, **changes: object) -> CandidateReview:
    value = review.to_dict()
    value.update(changes)
    value["content_sha256"] = content_digest(value)
    return CandidateReview.from_dict(value)


def record_digest(value: dict[str, object]) -> str:
    return sha256_json({str(key): item for key, item in value.items() if key != "record_sha256"})


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_snapshot_candidates_returns_authenticated_immutable_closure_without_writes(
    service: ManualAcquisitionService,
    tmp_path: Path,
) -> None:
    transformation_ref = BlobStore(ArtifactRoot(tmp_path)).put_bytes(
        b"opaque transformation",
        "application/octet-stream",
        "evidence_transformation",
    )
    candidate = ingest_candidate(
        service,
        curated_text="authenticated source text",
        transformation_refs=(transformation_ref,),
    )
    before = tree_snapshot(tmp_path)

    closures = service.snapshot_candidates()

    assert tree_snapshot(tmp_path) == before
    assert len(closures) == 1
    closure = closures[0]
    assert isinstance(closure, acquisition_contracts.AcquisitionCandidateClosure)
    assert closure.candidate == candidate
    assert closure.candidate_bytes == (canonical_json(candidate.to_dict()) + "\n").encode()
    assert closure.content_bytes == b"authenticated source text"
    assert closure.transformations == ((transformation_ref, b"opaque transformation"),)
    with pytest.raises(TypeError):
        closures[0] = closure  # type: ignore[index]


@pytest.mark.parametrize(
    "member",
    ["manifest", "candidate", "content", "transformation", "inventory"],
)
def test_snapshot_candidates_fails_closed_for_mutated_closure_member(
    tmp_path: Path,
    member: str,
) -> None:
    service = make_service(tmp_path)
    transformation_ref = BlobStore(ArtifactRoot(tmp_path)).put_bytes(
        b"opaque transformation",
        "application/octet-stream",
        "evidence_transformation",
    )
    candidate = ingest_candidate(
        service,
        curated_text="authenticated source text",
        transformation_refs=(transformation_ref,),
    )
    if member == "manifest":
        target = service.session_dir / "manifest.json"
    elif member == "candidate":
        target = service.candidate_path(candidate.candidate_id)
    elif member == "content":
        target = ArtifactRoot(tmp_path).resolve(candidate.content_ref.uri)
    elif member == "transformation":
        target = ArtifactRoot(tmp_path).resolve(transformation_ref.uri)
    else:
        target = service.session_dir / "candidates" / "unreferenced.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if member == "inventory":
        target.write_text("{}\n", encoding="utf-8")
    else:
        target.write_bytes(target.read_bytes() + b"changed")

    with pytest.raises(ValueError, match="acquisition"):
        service.snapshot_candidates()


def test_snapshot_candidates_revalidates_source_before_return(
    service: ManualAcquisitionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = ingest_candidate(service, curated_text="authenticated source text")
    content_path = ArtifactRoot(tmp_path).resolve(candidate.content_ref.uri)
    original_read = acquisition_service.secure_read_bytes
    mutated = False

    def mutate_after_first_snapshot_content_read(
        path: str | Path,
        *,
        root: str | Path | None = None,
        label: str = "file",
    ) -> bytes:
        nonlocal mutated
        data = original_read(path, root=root, label=label)
        if label == "acquisition snapshot content" and not mutated:
            mutated = True
            content_path.write_bytes(b"mutated after snapshot")
        return data

    monkeypatch.setattr(
        acquisition_service,
        "secure_read_bytes",
        mutate_after_first_snapshot_content_read,
    )

    with pytest.raises(ValueError, match="acquisition source snapshot changed"):
        service.snapshot_candidates()


def test_snapshot_candidates_closes_authentication_after_returned_closure_read(
    service: ManualAcquisitionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = ingest_candidate(service, curated_text="authenticated source text")
    content_path = ArtifactRoot(tmp_path).resolve(candidate.content_ref.uri)
    original_read = acquisition_service.secure_read_bytes
    snapshot_content_reads = 0

    def mutate_after_second_snapshot_content_read(
        path: str | Path,
        *,
        root: str | Path | None = None,
        label: str = "file",
    ) -> bytes:
        nonlocal snapshot_content_reads
        data = original_read(path, root=root, label=label)
        if label == "acquisition snapshot content":
            snapshot_content_reads += 1
            if snapshot_content_reads == 2:
                content_path.write_bytes(b"mutated after returned closure read")
        return data

    monkeypatch.setattr(
        acquisition_service,
        "secure_read_bytes",
        mutate_after_second_snapshot_content_read,
    )

    with pytest.raises(ValueError, match="acquisition source snapshot changed"):
        service.snapshot_candidates()


def test_snapshot_candidates_read_os_error_never_echoes_local_paths(
    service: ManualAcquisitionService,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ingest_candidate(service, curated_text="authenticated source text")
    original_read = acquisition_service.secure_read_bytes

    def fail_snapshot_content_read(
        path: str | Path,
        *,
        root: str | Path | None = None,
        label: str = "file",
    ) -> bytes:
        if label == "acquisition snapshot content":
            raise OSError(13, "read denied", str(path))
        return original_read(path, root=root, label=label)

    monkeypatch.setattr(
        acquisition_service,
        "secure_read_bytes",
        fail_snapshot_content_read,
    )

    with pytest.raises(
        ValueError,
        match="acquisition source snapshot is invalid",
    ) as caught:
        service.snapshot_candidates()

    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert caught.value.__cause__ is None
    assert str(tmp_path) not in rendered


def leave_pending_promotion(
    service: ManualAcquisitionService,
    candidate: EvidenceCandidate,
    monkeypatch: pytest.MonkeyPatch,
    *,
    evidence_id: str,
) -> Path:
    original_write_json = acquisition_service._write_json

    def interrupt_after_journal(
        path: Path,
        value: object,
        *,
        root: Path,
        create_new: bool = False,
    ) -> None:
        original_write_json(
            path,
            value,
            root=root,
            create_new=create_new,
        )
        if path.name == "promotion-transaction.json":
            raise RuntimeError("injected interruption after journal write")

    monkeypatch.setattr(acquisition_service, "_write_json", interrupt_after_journal)
    with pytest.raises(RuntimeError, match="injected interruption"):
        promote_candidate(
            service,
            candidate,
            approved_review(candidate),
            evidence_id=evidence_id,
        )
    monkeypatch.setattr(acquisition_service, "_write_json", original_write_json)
    transaction_path = service.session_dir / "promotion-transaction.json"
    assert transaction_path.exists()
    return transaction_path


def test_manual_acquisition_records_redacted_curated_text_and_untrusted_boundary(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(
        service,
        query="Treaty person@example.test",
        title="Treaty at C:/Users/name/private.txt",
        curated_text="Treaty text for +86 17610768902",
    )

    expected_body = b"Treaty text for [PHONE]"
    assert candidate.content_sha256 == hashlib.sha256(expected_body).hexdigest()
    assert candidate.query == "Treaty [EMAIL]"
    assert candidate.title == "Treaty at [LOCAL_PATH]"
    assert candidate.document_date == "1812-05"
    assert candidate.date_precision == "month"
    assert candidate.retrieved_at == NOW
    assert candidate.trust_level == "untrusted_external"
    assert "system_prompt" not in candidate.to_dict()
    stored = json.loads(service.candidate_path(candidate.candidate_id).read_text(encoding="utf-8"))
    assert stored["source_url"] == "https://history.example/source"
    assert stored["retrieved_at"] == "2026-07-24T00:00:00Z"


def test_manual_acquisition_redacts_non_home_posix_paths_before_persistence(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(
        service,
        query="inspect /etc/passwd",
        title="notes at /var/tmp/private.txt",
        curated_text="source from /opt/private/file",
    )

    assert candidate.query == "inspect [LOCAL_PATH]"
    assert candidate.title == "notes at [LOCAL_PATH]"
    body_path = ArtifactRoot(service.session_dir.parents[1]).resolve(candidate.content_ref.uri)
    assert body_path.read_bytes() == b"source from [LOCAL_PATH]"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://history.example/document",
        "https://user:password@history.example/document",
        "https://127.0.0.1/private",
        "https://history.example/?api_key=secret-value",
    ],
)
def test_manual_acquisition_rejects_unsafe_or_sensitive_source_url(
    service: ManualAcquisitionService,
    url: str,
) -> None:
    with pytest.raises(ValueError, match="URL|scheme|credential|host"):
        ingest_candidate(service, source_url=url)


@pytest.mark.parametrize(
    "parameter_name",
    [
        "api%5Fkey",
        "api-key",
        "apikey",
        "TOKEN",
        "access%5Ftoken",
        "client%5Fsecret",
        "secret",
        "authorization",
        "password",
        "cookie",
        "session",
        "session_id",
        "set-cookie",
    ],
)
def test_manual_acquisition_rejects_decoded_sensitive_query_names(
    service: ManualAcquisitionService,
    parameter_name: str,
) -> None:
    url = f"https://history.example/source?{parameter_name}=ordinary-value"

    with pytest.raises(ValueError, match="sensitive"):
        ingest_candidate(service, source_url=url)


def test_manual_acquisition_rejects_configured_secret_in_decoded_query_name(
    service: ManualAcquisitionService,
) -> None:
    configured_secret = "private-runtime-value"
    url = "https://history.example/source?private%2Druntime%2Dvalue=ordinary-value"

    with pytest.raises(ValueError, match="sensitive"):
        ingest_candidate(service, source_url=url, secrets=(configured_secret,))


def test_manual_acquisition_rejects_generic_credential_in_decoded_query_name(
    service: ManualAcquisitionService,
) -> None:
    url = "https://history.example/source?sk-" + "%61" * 16 + "=ordinary-value"

    with pytest.raises(ValueError, match="sensitive"):
        ingest_candidate(service, source_url=url)


@pytest.mark.parametrize(
    ("source_url", "secrets"),
    [
        ("https://history.example/source/sk-" + "%61" * 16, ()),
        ("https://history.example/source/person%40example.test", ()),
        ("https://history.example/source/%2B86%2017610768902", ()),
        ("https://history.example/source?note=Bearer%20abc.def.ghi", ()),
        (
            "https://history.example/source/private%2Druntime%2Dvalue",
            ("private-runtime-value",),
        ),
    ],
)
def test_manual_acquisition_rejects_decoded_sensitive_source_url_values(
    service: ManualAcquisitionService,
    source_url: str,
    secrets: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="sensitive"):
        ingest_candidate(service, source_url=source_url, secrets=secrets)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.COM:443/a#fragment", "https://example.com/a"),
        ("https://bücher.example", "https://xn--bcher-kva.example/"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/%7euser/%41", "https://example.com/~user/A"),
        (
            "https://example.com/a?second=2&first=1",
            "https://example.com/a?second=2&first=1",
        ),
    ],
)
def test_source_url_canonical_form(raw: str, expected: str) -> None:
    assert acquisition_contracts.canonical_source_url(raw) == expected


@pytest.mark.parametrize(
    "source_url",
    [
        "https://example.com/a/../source",
        "https://example.com/a/./source",
        "https://example.com/a/%2e%2e/source",
        "https://example.com/a/%2E/source",
    ],
)
def test_source_url_rejects_literal_or_encoded_dot_segments(source_url: str) -> None:
    with pytest.raises(ValueError, match="dot segment"):
        acquisition_contracts.canonical_source_url(source_url)


def test_equivalent_source_urls_share_candidate_and_locator_identity(
    service: ManualAcquisitionService,
) -> None:
    first = ingest_candidate(
        service,
        source_url="https://Example.COM:443/a#fragment",
    )
    second = ingest_candidate(
        service,
        source_url="https://example.com/a",
    )

    assert first == second
    assert first.source_url == "https://example.com/a"
    assert source_locator_sha256(first.source_url) == source_locator_sha256(
        "https://Example.COM:443/a#fragment"
    )


def test_loaded_candidate_rejects_noncanonical_source_url(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(service, source_url="https://example.com/a")
    value = candidate.to_dict()
    value["source_url"] = "https://Example.COM:443/a#fragment"
    value["record_sha256"] = record_digest(value)

    with pytest.raises(ValueError, match="canonical"):
        EvidenceCandidate.from_dict(value)


@pytest.mark.parametrize(
    ("document_date", "date_precision"),
    [
        ("1812-01/1812-05", "day"),
        ("1812-5", "month"),
        ("1812-05", "quarter"),
    ],
)
def test_manual_acquisition_rejects_invalid_candidate_dates(
    service: ManualAcquisitionService,
    document_date: str,
    date_precision: str,
) -> None:
    with pytest.raises(ValueError, match="document_date|date_precision"):
        ingest_candidate(
            service,
            document_date=document_date,
            date_precision=date_precision,
        )


def test_candidate_id_is_bound_to_date_fields(
    service: ManualAcquisitionService,
) -> None:
    first = ingest_candidate(service)
    second = ingest_candidate(
        service,
        document_date="1812",
        date_precision="year",
    )

    assert first.candidate_id != second.candidate_id


def test_candidate_id_is_stable_but_record_changes_for_curator(
    service: ManualAcquisitionService,
) -> None:
    first = ingest_candidate(service, curator="curator-001")
    second = EvidenceCandidate.create(
        candidate_id=first.candidate_id,
        query=first.query,
        title=first.title,
        source_url=first.source_url,
        document_date=first.document_date,
        date_precision=first.date_precision,
        retrieved_at=first.retrieved_at,
        curator="curator-002",
        transformation_refs=first.transformation_refs,
        content_ref=first.content_ref,
    )

    assert first.candidate_id == second.candidate_id
    assert first.record_sha256 != second.record_sha256


def test_candidate_id_is_stable_but_record_changes_for_ordered_transformations(
    service: ManualAcquisitionService,
    tmp_path: Path,
) -> None:
    store = BlobStore(ArtifactRoot(tmp_path))
    first_transformation = store.put_bytes(
        b'{"step":"first"}',
        "application/json",
        "evidence_transformation",
    )
    second_transformation = store.put_bytes(
        b'{"step":"second"}',
        "application/json",
        "evidence_transformation",
    )

    forward = ingest_candidate(
        service,
        transformation_refs=(first_transformation, second_transformation),
    )
    reverse = EvidenceCandidate.create(
        candidate_id=forward.candidate_id,
        query=forward.query,
        title=forward.title,
        source_url=forward.source_url,
        document_date=forward.document_date,
        date_precision=forward.date_precision,
        retrieved_at=forward.retrieved_at,
        curator=forward.curator,
        transformation_refs=(second_transformation, first_transformation),
        content_ref=forward.content_ref,
    )

    assert forward.candidate_id == reverse.candidate_id
    assert forward.record_sha256 != reverse.record_sha256


def test_curator_edit_keeps_candidate_id_but_invalidates_existing_review(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(service)
    review = approved_review(candidate)
    edited = EvidenceCandidate.create(
        candidate_id=candidate.candidate_id,
        query=candidate.query,
        title=candidate.title,
        source_url=candidate.source_url,
        document_date=candidate.document_date,
        date_precision=candidate.date_precision,
        retrieved_at=candidate.retrieved_at,
        curator="curator-002",
        transformation_refs=candidate.transformation_refs,
        content_ref=candidate.content_ref,
    )
    candidate_path = service.candidate_path(candidate.candidate_id)
    candidate_path.write_text(canonical_json(edited.to_dict()) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="review does not match candidate record"):
        promote_candidate(service, candidate, review)


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "transformation_record"},
        {"schema_id": "tracelane://schemas/object-envelope/v2"},
    ],
)
def test_ingest_rejects_malformed_transformation_ref_before_candidate_publication(
    service: ManualAcquisitionService,
    tmp_path: Path,
    changes: dict[str, object],
) -> None:
    valid = BlobStore(ArtifactRoot(tmp_path)).put_bytes(
        b"manual transformation",
        "text/plain",
        "evidence_transformation",
    )
    malformed = replace(valid, **changes)

    with pytest.raises(ValueError, match="transformation"):
        ingest_candidate(service, transformation_refs=(malformed,))

    manifest = json.loads((service.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["candidate_refs"] == []


def test_candidate_from_dict_rejects_malformed_transformation_ref(
    service: ManualAcquisitionService,
    tmp_path: Path,
) -> None:
    candidate = ingest_candidate(service)
    malformed = BlobStore(ArtifactRoot(tmp_path)).put_bytes(
        b"manual transformation",
        "text/plain",
        "transformation_record",
    )
    value = candidate.to_dict()
    value["transformation_refs"] = [malformed.to_dict()]
    value["record_sha256"] = record_digest(value)

    with pytest.raises(ValueError, match="transformation"):
        EvidenceCandidate.from_dict(value)


def test_candidate_to_dict_rejects_malformed_transformation_ref(
    service: ManualAcquisitionService,
    tmp_path: Path,
) -> None:
    candidate = ingest_candidate(service)
    malformed = BlobStore(ArtifactRoot(tmp_path)).put_bytes(
        b"manual transformation",
        "text/plain",
        "transformation_record",
    )
    mutated = replace(candidate, transformation_refs=(malformed,))

    with pytest.raises(ValueError, match="transformation"):
        mutated.to_dict()


def test_candidate_id_is_recomputed_when_loading(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(service)
    value = candidate.to_dict()
    value["candidate_id"] = "candidate_" + "0" * 24
    value["record_sha256"] = record_digest(value)

    with pytest.raises(ValueError, match="candidate_id"):
        EvidenceCandidate.from_dict(value)


def test_candidate_rejects_precision_mismatched_document_date(
    service: ManualAcquisitionService,
) -> None:
    value = ingest_candidate(service).to_dict()
    value["date_precision"] = "day"
    value["candidate_id"] = acquisition_contracts.compute_candidate_id(
        query=str(value["query"]),
        title=str(value["title"]),
        source_url=str(value["source_url"]),
        document_date=str(value["document_date"]),
        date_precision=str(value["date_precision"]),
        content_sha256=str(value["content_sha256"]),
    )
    value["record_sha256"] = record_digest(value)

    with pytest.raises(ValueError, match="document_date|date_precision"):
        EvidenceCandidate.from_dict(value)


def test_review_rejects_invalid_calendar_document_date(
    service: ManualAcquisitionService,
) -> None:
    value = approved_review(ingest_candidate(service)).to_dict()
    value["document_date"] = "1812-02-30"
    value["date_precision"] = "day"
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match="document_date"):
        CandidateReview.from_dict(value)


@pytest.mark.parametrize(
    ("corruption", "expected_error", "expected_message"),
    [(item, *ACQUISITION_CORRUPTION_EXPECTATIONS[item]) for item in ACQUISITION_CORRUPTIONS],
)
def test_public_acquisition_service_rejects_adversarial_matrix(
    service: ManualAcquisitionService,
    tmp_path: Path,
    corruption: str,
    expected_error: type[ValueError],
    expected_message: str,
) -> None:
    candidate = ingest_candidate(service)
    review = approved_review(candidate)
    candidate_id = candidate.candidate_id
    outside_path: Path | None = None

    if corruption == "candidate_traversal":
        candidate_id = "../outside"
        review = replace(review, candidate_id=candidate_id)
        outside_path = service.session_dir / "outside.json"
    elif corruption == "candidate_unc_path":
        candidate_id = r"\\server\share\candidate.json"
        review = replace(review, candidate_id=candidate_id)
    elif corruption == "record_substitution":
        candidate_path = service.candidate_path(candidate.candidate_id)
        value = json.loads(candidate_path.read_text(encoding="utf-8"))
        value["title"] = "substituted title"
        value["candidate_id"] = (
            "candidate_"
            + sha256_json(
                {
                    "query": value["query"],
                    "title": value["title"],
                    "source_url": value["source_url"],
                    "document_date": value["document_date"],
                    "date_precision": value["date_precision"],
                    "content_sha256": value["content_sha256"],
                    "curator": value["curator"],
                    "transformation_refs": value["transformation_refs"],
                }
            )[:24]
        )
        value["record_sha256"] = record_digest(value)
        candidate_path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    elif corruption == "blob_substitution":
        blob_path = service._root.resolve(candidate.content_ref.uri)
        blob_path.write_bytes(b"substituted bytes")
    elif corruption == "stale_approval":
        review_value = review.to_dict()
        review_value["candidate_record_sha256"] = "f" * 64
        review_value["content_sha256"] = content_digest(review_value)
        review = CandidateReview.from_dict(review_value)
    elif corruption == "session_identity_substitution":
        manifest = service.session_dir / "manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["session_id"] = "another-session"
        value["content_sha256"] = content_digest(value)
        manifest.write_text(canonical_json(value) + "\n", encoding="utf-8")
    else:
        raise AssertionError(f"unknown corruption: {corruption}")

    before_rejection = tree_snapshot(tmp_path)
    if outside_path is not None:
        assert not outside_path.exists()

    with pytest.raises(expected_error, match=expected_message) as captured:
        if corruption == "session_identity_substitution":
            make_service(tmp_path)
        else:
            service.promote(
                candidate_id,
                review,
                **promotion_fields(),  # type: ignore[arg-type]
            )

    assert type(captured.value) is expected_error
    assert tree_snapshot(tmp_path) == before_rejection
    if outside_path is not None:
        assert not outside_path.exists()


def test_existing_session_manifest_rejects_digest_substitution(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    manifest = service.session_dir / "manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["created_at"] = "2026-07-23T00:00:00Z"
    manifest.write_text(canonical_json(value) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="content hash"):
        make_service(tmp_path)


def test_promotion_requires_matching_explicit_approval(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(service)
    rejected = CandidateReview.create(
        candidate,
        decision="rejected",
        reviewer="reviewer",
        reviewed_at=NOW,
        available_at=NOW,
        source_type="primary",
        license="Public domain",
        reason="wrong document",
    )

    with pytest.raises(ValueError, match="approved"):
        promote_candidate(service, candidate, rejected)

    reference = promote_candidate(service, candidate, approved_review(candidate))

    assert reference.kind == "evidence_record"
    assert reference.sha256
    review_path = service.session_dir / "reviews" / f"{candidate.candidate_id}.json"
    stored = json.loads(review_path.read_text(encoding="utf-8"))
    assert stored["reviewer"] == "reviewer"
    assert stored["candidate_record_sha256"] == candidate.record_sha256
    assert stored["candidate_content_sha256"] == candidate.content_sha256
    assert stored["content_sha256"] == content_digest(stored)


def test_promotion_strictly_revalidates_review_object_before_writing(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(service)
    review = replace(approved_review(candidate), reason="changed without a new digest")

    with pytest.raises(ValueError, match="review content hash"):
        promote_candidate(service, candidate, review)

    assert not (service.session_dir / "reviews" / f"{candidate.candidate_id}.json").exists()


def test_promotion_rejects_hard_linked_candidate_record(
    service: ManualAcquisitionService,
    tmp_path: Path,
) -> None:
    candidate = ingest_candidate(service)
    candidate_path = service.candidate_path(candidate.candidate_id)
    outside = tmp_path / "outside-candidate.json"
    try:
        os.link(candidate_path, outside)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="hard link|link count|multiple links|unavailable"):
        promote_candidate(service, candidate, approved_review(candidate))

    assert outside.read_bytes() == candidate_path.read_bytes()
    assert not (service.session_dir / "reviews" / f"{candidate.candidate_id}.json").exists()


def test_session_manifest_inventories_and_full_history_promotion(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    transformation_ref = BlobStore(ArtifactRoot(tmp_path)).put_bytes(
        b'{"kind":"manual_paraphrase"}',
        "application/json",
        "evidence_transformation",
    )
    candidate = service.ingest(
        query="query",
        title="Primary source",
        source_url="https://History.Example:443/source#fragment",
        document_date="1812-05",
        date_precision="month",
        curated_text="source text",
        curator="curator-001",
        transformation_refs=(transformation_ref,),
    )
    review = CandidateReview.create(
        candidate,
        decision="approved",
        reviewer="reviewer",
        reviewed_at=NOW,
        available_at=NOW,
        source_type="primary",
        license="Public-Domain",
        reason="provenance checked",
    )

    reference = service.promote(
        candidate.candidate_id,
        review,
        evidence_id="hist-001-ev-0001",
        known_by_cutoff="known",
        excerpt_kind="paraphrased",
        fact_ids=("fact.safe",),
    )

    assert reference.kind == "evidence_record"
    assert reference.schema_id == "tracelane://schemas/evidence-record/v2"
    record_path = ArtifactRoot(tmp_path).resolve(reference.uri)
    record = EvidenceRecordV2.from_dict(json.loads(record_path.read_text(encoding="utf-8")))
    stored_review = json.loads(
        (service.session_dir / "reviews" / f"{candidate.candidate_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert record.candidate_id == candidate.candidate_id
    assert record.candidate_record_sha256 == candidate.record_sha256
    assert record.review_sha256 == stored_review["content_sha256"]
    assert record.source_locator_sha256 == source_locator_sha256(candidate.source_url)
    assert record.curator == "curator-001"
    assert record.source_locator == "https://history.example/source"
    assert record.transformation_refs == (transformation_ref,)

    manifest = json.loads((service.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["candidate_refs"]) == 1
    assert len(manifest["review_refs"]) == 1
    assert record.candidate_ref == ArtifactRef.from_dict(manifest["candidate_refs"][0])
    assert record.review_ref == ArtifactRef.from_dict(manifest["review_refs"][0])
    assert manifest["promoted_record_refs"] == [reference.to_dict()]
    assert manifest["content_sha256"] == content_digest(manifest)
    make_service(tmp_path)


@pytest.mark.parametrize("lineage_field", ["candidate_ref", "review_ref"])
def test_session_reopen_rejects_cross_candidate_lineage_substitution(
    tmp_path: Path,
    lineage_field: str,
) -> None:
    service = make_service(tmp_path)
    first = ingest_candidate(service, curated_text="first source")
    first_record_ref = promote_candidate(
        service,
        first,
        approved_review(first),
    )
    second = ingest_candidate(service, curated_text="second source")
    promote_candidate(
        service,
        second,
        approved_review(second),
        evidence_id="hist-001-ev-0002",
    )
    manifest_path = service.session_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    replacement_inventory = (
        manifest["candidate_refs"] if lineage_field == "candidate_ref" else manifest["review_refs"]
    )
    replacement = next(item for item in replacement_inventory if second.candidate_id in item["uri"])
    record_path = ArtifactRoot(tmp_path).resolve(first_record_ref.uri)
    record_value = json.loads(record_path.read_text(encoding="utf-8"))
    record_value[lineage_field] = replacement
    record_value["provenance_sha256"] = compute_evidence_provenance_sha256(record_value)
    record_path.write_text(canonical_json(record_value) + "\n", encoding="utf-8")
    updated_record_ref = artifact_ref_for_file(
        tmp_path,
        record_path,
        "evidence_record",
        "tracelane://schemas/evidence-record/v2",
    )
    manifest["promoted_record_refs"] = [
        (updated_record_ref.to_dict() if item["uri"] == first_record_ref.uri else item)
        for item in manifest["promoted_record_refs"]
    ]
    manifest["content_sha256"] = content_digest(manifest)
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="lineage|candidate|review"):
        make_service(tmp_path)


def test_session_operations_are_serialized_without_inventory_loss(tmp_path: Path) -> None:
    first_service = make_service(tmp_path)
    second_service = make_service(tmp_path)
    first = ingest_candidate(first_service, curated_text="first source")
    second = ingest_candidate(second_service, curated_text="second source")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                promote_candidate,
                first_service,
                first,
                approved_review(first),
            ),
            executor.submit(
                promote_candidate,
                second_service,
                second,
                approved_review(second),
                evidence_id="hist-001-ev-0002",
            ),
        )
        references = tuple(future.result() for future in futures)

    manifest = json.loads((first_service.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["candidate_refs"]) == 2
    assert len(manifest["review_refs"]) == 2
    assert len(manifest["promoted_record_refs"]) == 2
    assert {item["uri"] for item in manifest["promoted_record_refs"]} == {
        reference.uri for reference in references
    }
    make_service(tmp_path)


def test_two_services_do_not_lose_ingest_inventory(tmp_path: Path) -> None:
    first_service = make_service(tmp_path)
    second_service = make_service(tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                ingest_candidate,
                first_service,
                curated_text="first concurrent source",
            ),
            executor.submit(
                ingest_candidate,
                second_service,
                curated_text="second concurrent source",
            ),
        )
        candidates = tuple(future.result() for future in futures)

    manifest = json.loads((first_service.session_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_refs = {
        artifact_ref_for_file(
            tmp_path,
            first_service.candidate_path(candidate.candidate_id),
            "evidence_candidate",
            "tracelane://schemas/evidence-candidate/v2",
        )
        for candidate in candidates
    }
    assert {ArtifactRef.from_dict(item) for item in manifest["candidate_refs"]} == expected_refs
    make_service(tmp_path)


def test_ingest_recovers_after_candidate_publish_before_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path)
    original_write_json = acquisition_service._write_json
    interrupted_candidate: EvidenceCandidate | None = None

    def write_then_interrupt(
        path: Path,
        value: object,
        *,
        root: Path,
        create_new: bool = False,
    ) -> None:
        nonlocal interrupted_candidate
        original_write_json(
            path,
            value,
            root=root,
            create_new=create_new,
        )
        if path.parent.name == "candidates":
            assert isinstance(value, dict)
            interrupted_candidate = EvidenceCandidate.from_dict(value)
            raise RuntimeError("injected interruption after candidate write")

    monkeypatch.setattr(acquisition_service, "_write_json", write_then_interrupt)
    with pytest.raises(RuntimeError, match="injected interruption"):
        ingest_candidate(service)
    monkeypatch.setattr(acquisition_service, "_write_json", original_write_json)
    assert interrupted_candidate is not None

    recovered = make_service(tmp_path)
    candidate_ref = artifact_ref_for_file(
        tmp_path,
        recovered.candidate_path(interrupted_candidate.candidate_id),
        "evidence_candidate",
        "tracelane://schemas/evidence-candidate/v2",
    )
    manifest = json.loads((recovered.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert [ArtifactRef.from_dict(item) for item in manifest["candidate_refs"]].count(
        candidate_ref
    ) == 1


@pytest.mark.parametrize(
    "failure_point",
    ["journal", "review", "record", "manifest"],
)
def test_interrupted_promotion_is_recovered_on_session_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    service = make_service(tmp_path)
    candidate = ingest_candidate(service)
    original_write_json = acquisition_service._write_json
    injected = False

    def write_then_interrupt(
        path: Path,
        value: object,
        *,
        root: Path,
        create_new: bool = False,
    ) -> None:
        nonlocal injected
        original_write_json(
            path,
            value,
            root=root,
            create_new=create_new,
        )
        is_failure_point = {
            "journal": path.name == "promotion-transaction.json",
            "review": path.parent.name == "reviews",
            "record": path.parent.name == "promoted",
            "manifest": path.name == "manifest.json",
        }[failure_point]
        if is_failure_point and not injected:
            injected = True
            raise RuntimeError(f"injected interruption after {failure_point} write")

    monkeypatch.setattr(acquisition_service, "_write_json", write_then_interrupt)
    with pytest.raises(RuntimeError, match="injected interruption"):
        promote_candidate(service, candidate, approved_review(candidate))
    monkeypatch.setattr(acquisition_service, "_write_json", original_write_json)

    recovered = make_service(tmp_path)
    manifest = json.loads((recovered.session_dir / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["candidate_refs"]) == 1
    assert len(manifest["review_refs"]) == 1
    assert len(manifest["promoted_record_refs"]) == 1
    assert not (recovered.session_dir / "promotion-transaction.json").exists()


def test_recovery_does_not_repair_invalid_existing_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path)
    existing_candidate = ingest_candidate(service, curated_text="existing source")
    existing_record_ref = promote_candidate(
        service,
        existing_candidate,
        approved_review(existing_candidate),
    )
    pending_candidate = ingest_candidate(service, curated_text="pending source")
    transaction_path = leave_pending_promotion(
        service,
        pending_candidate,
        monkeypatch,
        evidence_id="hist-001-ev-0002",
    )

    record_path = ArtifactRoot(tmp_path).resolve(existing_record_ref.uri)
    record = json.loads(record_path.read_text(encoding="utf-8"))
    pending_candidate_ref = artifact_ref_for_file(
        tmp_path,
        service.candidate_path(pending_candidate.candidate_id),
        "evidence_candidate",
        "tracelane://schemas/evidence-candidate/v2",
    )
    record["candidate_ref"] = pending_candidate_ref.to_dict()
    record["provenance_sha256"] = compute_evidence_provenance_sha256(record)
    record_path.write_text(canonical_json(record) + "\n", encoding="utf-8")
    substituted_record_ref = artifact_ref_for_file(
        tmp_path,
        record_path,
        "evidence_record",
        "tracelane://schemas/evidence-record/v2",
    )
    manifest_path = service.session_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["promoted_record_refs"] = [
        (substituted_record_ref.to_dict() if item["uri"] == existing_record_ref.uri else item)
        for item in manifest["promoted_record_refs"]
    ]
    manifest["content_sha256"] = content_digest(manifest)
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    if "base_manifest_sha256" in transaction:
        transaction["base_manifest_sha256"] = manifest["content_sha256"]
        transaction["content_sha256"] = content_digest(transaction)
        transaction_path.write_text(
            canonical_json(transaction) + "\n",
            encoding="utf-8",
        )

    before = tree_snapshot(tmp_path)
    with pytest.raises(ValueError):
        make_service(tmp_path)
    assert transaction_path.exists()
    assert tree_snapshot(tmp_path) == before


def test_recovery_keeps_journal_until_merged_session_validates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path)
    candidate = ingest_candidate(service)
    transaction_path = leave_pending_promotion(
        service,
        candidate,
        monkeypatch,
        evidence_id="hist-001-ev-0001",
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    pending_record_ref = ArtifactRef.from_dict(transaction["record_ref"])
    original_validate = ManualAcquisitionService._validate_session_manifest

    def reject_merged_session(
        instance: ManualAcquisitionService,
        manifest: dict[str, object],
        *args: object,
        **kwargs: object,
    ) -> None:
        original_validate(instance, manifest, *args, **kwargs)
        references = {
            ArtifactRef.from_dict(item).uri
            for item in manifest["promoted_record_refs"]  # type: ignore[union-attr]
        }
        if pending_record_ref.uri in references:
            raise ValueError("injected merged-session validation failure")

    monkeypatch.setattr(
        ManualAcquisitionService,
        "_validate_session_manifest",
        reject_merged_session,
    )
    with pytest.raises(ValueError, match="merged-session"):
        make_service(tmp_path)
    assert transaction_path.exists()


def test_recovery_keeps_journal_until_published_session_reread_validates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path)
    candidate = ingest_candidate(service)
    transaction_path = leave_pending_promotion(
        service,
        candidate,
        monkeypatch,
        evidence_id="hist-001-ev-0001",
    )
    transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
    pending_record_ref = ArtifactRef.from_dict(transaction["record_ref"])
    original_validate = ManualAcquisitionService._validate_session_manifest
    pending_validations = 0

    def reject_published_reread(
        instance: ManualAcquisitionService,
        manifest: dict[str, object],
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal pending_validations
        original_validate(instance, manifest, *args, **kwargs)
        references = {
            ArtifactRef.from_dict(item).uri
            for item in manifest["promoted_record_refs"]  # type: ignore[union-attr]
        }
        if pending_record_ref.uri in references:
            pending_validations += 1
            if pending_validations == 2:
                raise ValueError("injected published-reread validation failure")

    monkeypatch.setattr(
        ManualAcquisitionService,
        "_validate_session_manifest",
        reject_published_reread,
    )

    with pytest.raises(ValueError, match="published-reread"):
        make_service(tmp_path)

    assert pending_validations == 2
    assert transaction_path.exists()


def test_recovery_retirement_rejects_concurrent_journal_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path)
    candidate = ingest_candidate(service)
    transaction_path = leave_pending_promotion(
        service,
        candidate,
        monkeypatch,
        evidence_id="hist-001-ev-0001",
    )
    expected_journal = transaction_path.read_bytes()
    original_move = storage_module.atomic_move_no_replace
    injected = False

    def move_then_race(
        source: Path,
        target: Path,
        *,
        label: str,
    ) -> None:
        nonlocal injected
        original_move(source, target, label=label)
        if source == transaction_path and not injected:
            injected = True
            transaction_path.write_bytes(b"racing replacement")

    monkeypatch.setattr(storage_module, "atomic_move_no_replace", move_then_race)

    with pytest.raises(ValueError, match="changed during retirement"):
        make_service(tmp_path)

    assert injected
    assert transaction_path.read_bytes() == b"racing replacement"
    tombstones = list(transaction_path.parent.glob(".promotion-transaction.json.*.retired"))
    assert len(tombstones) == 1
    assert tombstones[0].read_bytes() == expected_journal


def test_promotion_transaction_create_never_overwrites_a_racing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path)
    candidate = ingest_candidate(service)
    original_create = storage_module.atomic_create_bytes
    injected = False

    def create_after_racing_file(
        path: Path,
        data: bytes,
        *,
        root: str | Path | None = None,
        label: str = "file",
    ) -> None:
        nonlocal injected
        if path.name == "promotion-transaction.json" and not injected:
            injected = True
            path.write_bytes(b"racing sentinel")
        original_create(path, data, root=root, label=label)

    monkeypatch.setattr(
        acquisition_service,
        "atomic_create_bytes",
        create_after_racing_file,
        raising=False,
    )

    with pytest.raises(ValueError, match="already exists"):
        promote_candidate(service, candidate, approved_review(candidate))

    assert (service.session_dir / "promotion-transaction.json").read_bytes() == (b"racing sentinel")
    assert not (service.session_dir / "reviews").exists()
    assert not (service.session_dir / "promoted").exists()


def test_recovery_rejects_tampered_base_manifest_before_materializing_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = make_service(tmp_path)
    candidate = ingest_candidate(service)
    original_write_json = acquisition_service._write_json

    def interrupt_after_journal(
        path: Path,
        value: object,
        *,
        root: Path,
        create_new: bool = False,
    ) -> None:
        original_write_json(
            path,
            value,
            root=root,
            create_new=create_new,
        )
        if path.name == "promotion-transaction.json":
            raise RuntimeError("injected interruption after journal write")

    monkeypatch.setattr(
        acquisition_service,
        "_write_json",
        interrupt_after_journal,
    )
    with pytest.raises(RuntimeError, match="injected interruption"):
        promote_candidate(service, candidate, approved_review(candidate))
    monkeypatch.setattr(
        acquisition_service,
        "_write_json",
        original_write_json,
    )
    manifest_path = service.session_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["created_at"] = "2026-07-23T00:00:00Z"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="content hash"):
        make_service(tmp_path)

    assert not (service.session_dir / "reviews").exists()
    assert not (service.session_dir / "promoted").exists()


def test_promotion_sanitizes_directly_constructed_review_free_text(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(service)
    original = approved_review(candidate)
    github_token = "ghp_" + "a" * 36
    review = CandidateReview(
        content_sha256=original.content_sha256,
        candidate_id=original.candidate_id,
        candidate_record_sha256=original.candidate_record_sha256,
        candidate_content_sha256=original.candidate_content_sha256,
        source_locator_sha256=original.source_locator_sha256,
        decision=original.decision,
        reviewer="person@example.test",
        reviewed_at=original.reviewed_at,
        document_date=original.document_date,
        date_precision=original.date_precision,
        available_at=original.available_at,
        source_type=original.source_type,
        license=original.license,
        reason=f"Call +1 (415) 555-2671 about C:/Users/name/review.txt with {github_token}",
    )

    review = rebuilt_review(
        original,
        reviewer=review.reviewer,
        license=review.license,
        reason=review.reason,
    )
    promote_candidate(service, candidate, review)

    review_path = service.session_dir / "reviews" / f"{candidate.candidate_id}.json"
    stored = json.loads(review_path.read_text(encoding="utf-8"))
    serialized = canonical_json(stored)
    for forbidden in (
        "person@example.test",
        "415",
        "Users/name",
        github_token,
    ):
        assert forbidden not in serialized
    assert stored["content_sha256"] == content_digest(stored)
    assert stored["candidate_record_sha256"] == candidate.record_sha256
    assert stored["candidate_content_sha256"] == candidate.content_sha256


def test_promotion_redacts_configured_secret_from_direct_review(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(service)
    original = approved_review(candidate)
    configured_secret = "private-" + "review-" + "runtime-value"
    review = CandidateReview(
        content_sha256=original.content_sha256,
        candidate_id=original.candidate_id,
        candidate_record_sha256=original.candidate_record_sha256,
        candidate_content_sha256=original.candidate_content_sha256,
        source_locator_sha256=original.source_locator_sha256,
        decision=original.decision,
        reviewer=original.reviewer,
        reviewed_at=original.reviewed_at,
        document_date=original.document_date,
        date_precision=original.date_precision,
        available_at=original.available_at,
        source_type=original.source_type,
        license=original.license,
        reason=f"Checked with {configured_secret}",
    )

    review = rebuilt_review(original, reason=review.reason)
    promote_candidate(
        service,
        candidate,
        review,
        secrets=(configured_secret,),
    )

    review_path = service.session_dir / "reviews" / f"{candidate.candidate_id}.json"
    stored = json.loads(review_path.read_text(encoding="utf-8"))
    assert configured_secret not in canonical_json(stored)
    assert stored["reason"] == "Checked with [REDACTED]"
    assert stored["content_sha256"] == content_digest(stored)


def test_promotion_rebuilds_review_date_from_bound_candidate(
    service: ManualAcquisitionService,
) -> None:
    candidate = ingest_candidate(
        service,
        document_date="1812-05",
        date_precision="month",
    )
    original = approved_review(candidate)
    review = CandidateReview(
        content_sha256=original.content_sha256,
        candidate_id=original.candidate_id,
        candidate_record_sha256=original.candidate_record_sha256,
        candidate_content_sha256=original.candidate_content_sha256,
        source_locator_sha256=original.source_locator_sha256,
        decision=original.decision,
        reviewer=original.reviewer,
        reviewed_at=original.reviewed_at,
        document_date="1700",
        date_precision="year",
        available_at=original.available_at,
        source_type=original.source_type,
        license=original.license,
        reason=original.reason,
    )

    review = rebuilt_review(
        original,
        document_date=review.document_date,
        date_precision=review.date_precision,
    )
    with pytest.raises(ValueError, match="review does not match candidate"):
        promote_candidate(service, candidate, review)

    review_path = service.session_dir / "reviews" / f"{candidate.candidate_id}.json"
    assert not review_path.exists()


def test_candidate_review_from_dict_validates_its_envelope_digest(
    service: ManualAcquisitionService,
) -> None:
    value = approved_review(ingest_candidate(service)).to_dict()
    value["reason"] = "substituted reason"

    with pytest.raises(ValueError, match="review content hash"):
        CandidateReview.from_dict(value)


def test_ingest_is_idempotent_for_the_same_candidate(
    service: ManualAcquisitionService,
) -> None:
    first = ingest_candidate(service)
    second = ingest_candidate(service)

    assert first == second
    candidates = (service.session_dir / "candidates").glob("*.json")
    assert len(tuple(candidates)) == 1


def test_preparation_review_sheet_uses_sanitized_candidate_facing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    github_token = "ghp_" + "a" * 36
    spec = preparation.CandidateSpec(
        source_spec_id="hist001_sanitization_test",
        query=f"lookup {github_token}",
        title="Title person@example.test",
        source_url="https://history.example/source",
        document_date="1812-05",
        source_type="primary",
        license_basis="Stored at C:/Users/name/license.txt",
        domains=("diplomacy",),
        fact_ids=("fact.safe",),
        note="Call +1 (415) 555-2671",
    )
    monkeypatch.setattr(preparation, "SPECS", (spec,))

    result = preparation.prepare(tmp_path)

    review_text = result.review_path.read_text(encoding="utf-8")
    for forbidden in (
        github_token,
        "person@example.test",
        "Users/name",
        "415",
    ):
        assert forbidden not in review_text
    assert "[REDACTED]" in review_text
    assert "[EMAIL]" in review_text
    assert "[LOCAL_PATH]" in review_text
    assert "[PHONE]" in review_text
