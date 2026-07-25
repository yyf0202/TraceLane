from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracelane.acquisition import ManualAcquisitionService
from tracelane.acquisition import service as acquisition_service
from tracelane.evidence_registry import importer as evidence_importer
from tracelane.evidence_registry.contracts import (
    EvidenceImportMetadata,
    EvidenceImportRow,
    EvidenceProject,
)
from tracelane.evidence_registry.importer import import_acquisition_project
from tracelane.evidence_registry.index import verify_evidence_registry
from tracelane.security import RedactedPayload
from tracelane.v2.storage import ArtifactRoot, BlobStore

NOW = datetime(2026, 7, 25, tzinfo=UTC)
SESSION_ID = "acq_hist001_20260725"


def _project(
    *,
    project_id: str = "hist-001",
    title: str = "HIST-001",
) -> EvidenceProject:
    return EvidenceProject.create(
        project_id=project_id,
        title=title,
        research_question="What follows if Napoleon does not invade Russia?",
        historical_cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        intervention="Napoleon does not cross the Niemen.",
        required_domains=("diplomacy", "economy", "logistics"),
        admitted_source_types=("primary",),
        status="active",
    )


def _import_case(
    tmp_path: Path,
    *,
    count: int = 9,
    duplicate_content: bool = False,
    with_transformation: bool = False,
) -> tuple[Path, Path, EvidenceProject, EvidenceImportMetadata]:
    source = tmp_path / "operator-private-source"
    target = tmp_path / "evidence"
    service = ManualAcquisitionService(
        source,
        session_id=SESSION_ID,
        clock=lambda: NOW,
    )
    rows: list[EvidenceImportRow] = []
    transformation_refs = ()
    if with_transformation:
        transformation_refs = (
            BlobStore(ArtifactRoot(source)).put_bytes(
                b"opaque transformation",
                "application/octet-stream",
                "evidence_transformation",
            ),
        )
    for ordinal in range(count):
        future_control = ordinal == count - 1
        candidate = service.ingest(
            query=f"query {ordinal}",
            title=f"source {ordinal}",
            source_url=f"https://history.example/source-{ordinal}",
            document_date="1812-12-03" if future_control else "1812-05-01",
            date_precision="day",
            curated_text=(
                "shared repository paraphrase"
                if duplicate_content
                else f"repository paraphrase {ordinal}"
            ),
            curator="repository curator",
            transformation_refs=transformation_refs if ordinal == 0 else (),
        )
        rows.append(
            EvidenceImportRow.from_dict(
                {
                    "source_spec_id": f"hist001_source_{ordinal % 7}",
                    "candidate_id": candidate.candidate_id,
                    "candidate_record_sha256": candidate.record_sha256,
                    "candidate_content_sha256": candidate.content_sha256,
                    "source_type": "primary",
                    "license_basis": "Repository-authored paraphrase.",
                    "content_authorship": "repository_authored",
                    "retention_policy": "paraphrase_only",
                    "domains": ("diplomacy",) if ordinal % 2 else ("logistics",),
                    "fact_ids": (f"fact.{ordinal}",),
                    "role": "future-control" if future_control else "evidence",
                }
            )
        )
    manifest = json.loads((service.session_dir / "manifest.json").read_text(encoding="utf-8"))
    metadata = EvidenceImportMetadata.create(
        project_id="hist-001",
        session_id=SESSION_ID,
        manifest_sha256=str(manifest["content_sha256"]),
        candidates=tuple(sorted(rows, key=lambda item: item.candidate_id)),
    )
    return source, target, _project(), metadata


def test_imports_nine_authenticated_candidates_and_verifies_registry(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path)

    report = import_acquisition_project(source, target, project, metadata)

    assert report.project_id == "hist-001"
    assert report.candidate_count == 9
    assert report.pending_count == 9
    assert report.future_control_count == 1
    assert report.source_manifest_sha256 == metadata.manifest_sha256
    assert report.source_candidate_ids == tuple(row.candidate_id for row in metadata.candidates)
    verified = verify_evidence_registry(target, "hist-001")
    assert verified.candidate_count == 9
    assert verified.status_counts["pending"] == 9
    assert report.project_index_sha256 == verified.project_index_sha256
    assert report.registry_sha256 == verified.registry_sha256


@pytest.mark.parametrize("mismatch", ["missing", "extra", "record", "content"])
def test_import_rejects_metadata_and_source_identity_mismatch_without_target_mutation(
    tmp_path: Path,
    mismatch: str,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    rows = list(metadata.candidates)
    if mismatch == "missing":
        rows.pop()
    elif mismatch == "extra":
        rows.append(
            replace(
                rows[-1],
                candidate_id="candidate_" + "f" * 24,
                candidate_record_sha256="f" * 64,
                candidate_content_sha256="f" * 64,
            )
        )
    elif mismatch == "record":
        rows[0] = replace(rows[0], candidate_record_sha256="f" * 64)
    else:
        rows[0] = replace(rows[0], candidate_content_sha256="f" * 64)
    changed = EvidenceImportMetadata.create(
        project_id=metadata.project_id,
        session_id=metadata.session_id,
        manifest_sha256=metadata.manifest_sha256,
        candidates=tuple(sorted(rows, key=lambda item: item.candidate_id)),
    )

    with pytest.raises(ValueError, match="acquisition import metadata"):
        import_acquisition_project(source, target, project, changed)

    assert not target.exists()


def test_import_rejects_invalid_retention_pair_without_target_mutation(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    invalid_row = replace(
        metadata.candidates[0],
        content_authorship="third_party",
    )
    invalid_metadata = replace(metadata, candidates=(invalid_row,))

    with pytest.raises(ValueError, match="acquisition import metadata"):
        import_acquisition_project(source, target, project, invalid_metadata)

    assert not target.exists()


def test_import_rejects_opaque_legacy_transformations_without_dropping_lineage(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(
        tmp_path,
        count=1,
        with_transformation=True,
    )

    with pytest.raises(
        ValueError,
        match="acquisition transformations are not importable",
    ):
        import_acquisition_project(source, target, project, metadata)

    assert not target.exists()


def test_identical_rerun_succeeds_but_different_existing_project_fails(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    first = import_acquisition_project(source, target, project, metadata)

    assert import_acquisition_project(source, target, project, metadata) == first
    with pytest.raises(ValueError, match="evidence import target conflicts"):
        import_acquisition_project(
            source,
            target,
            _project(title="A different project"),
            metadata,
        )


def test_import_deduplicates_equal_global_content_blobs(tmp_path: Path) -> None:
    source, target, project, metadata = _import_case(
        tmp_path,
        count=3,
        duplicate_content=True,
    )

    import_acquisition_project(source, target, project, metadata)

    assert len(tuple((target / "blobs" / "sha256").rglob("*.blob"))) == 1


def test_import_repairs_valid_registry_that_becomes_stale_after_project_publication(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    first_project = _project(project_id="hist-000", title="HIST-000")
    first_metadata = EvidenceImportMetadata.create(
        project_id="hist-000",
        session_id=metadata.session_id,
        manifest_sha256=metadata.manifest_sha256,
        candidates=metadata.candidates,
    )
    import_acquisition_project(source, target, first_project, first_metadata)
    stale_registry = (target / "registry.json").read_bytes()

    report = import_acquisition_project(source, target, project, metadata)

    assert (target / "registry.json").read_bytes() != stale_registry
    assert verify_evidence_registry(target).project_count == 2
    assert report.project_id == "hist-001"


def test_import_rejects_corrupt_existing_registry_without_overwrite(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    import_acquisition_project(source, target, project, metadata)
    registry_path = target / "registry.json"
    registry_path.write_bytes(registry_path.read_bytes() + b"corrupt")
    corrupt_bytes = registry_path.read_bytes()

    with pytest.raises(ValueError, match="evidence import target is invalid"):
        import_acquisition_project(source, target, project, metadata)

    assert registry_path.read_bytes() == corrupt_bytes


@pytest.mark.parametrize("member", ["project", "index", "blob"])
def test_identical_rerun_rejects_target_member_tampering_without_overwrite(
    tmp_path: Path,
    member: str,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    import_acquisition_project(source, target, project, metadata)
    if member == "project":
        target_path = target / "projects" / "hist-001" / "project.json"
    elif member == "index":
        target_path = target / "projects" / "hist-001" / "index.json"
    else:
        target_path = next((target / "blobs" / "sha256").rglob("*.blob"))
    target_path.write_bytes(target_path.read_bytes() + b"tampered")
    tampered_bytes = target_path.read_bytes()

    with pytest.raises(ValueError, match="evidence import target"):
        import_acquisition_project(source, target, project, metadata)

    assert target_path.read_bytes() == tampered_bytes


def test_import_revalidates_source_after_snapshot_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_snapshot = ManualAcquisitionService.snapshot_candidates
    calls = 0

    def mutate_after_initial_snapshot(self: ManualAcquisitionService):
        nonlocal calls
        closures = original_snapshot(self)
        calls += 1
        if calls == 1:
            content_path = ArtifactRoot(source).resolve(closures[0].candidate.content_ref.uri)
            content_path.write_bytes(b"changed after initial snapshot")
        return closures

    monkeypatch.setattr(
        ManualAcquisitionService,
        "snapshot_candidates",
        mutate_after_initial_snapshot,
    )

    with pytest.raises(ValueError, match="acquisition import source changed"):
        import_acquisition_project(source, target, project, metadata)

    assert not (target / "projects" / "hist-001").exists()


def test_import_revalidates_source_after_project_publication_before_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_rename = evidence_importer.os.rename
    first_candidate = next((source / "acquisition" / SESSION_ID / "candidates").glob("*.json"))
    candidate_value = json.loads(first_candidate.read_text(encoding="utf-8"))
    content_path = ArtifactRoot(source).resolve(candidate_value["content_ref"]["uri"])
    original_content = content_path.read_bytes()

    def mutate_after_project_publication(
        source_path: str | Path,
        target_path: str | Path,
    ) -> None:
        original_rename(source_path, target_path)
        content_path.write_bytes(b"changed during project publication")

    monkeypatch.setattr(
        evidence_importer.os,
        "rename",
        mutate_after_project_publication,
    )

    with pytest.raises(ValueError, match="acquisition import source changed"):
        import_acquisition_project(source, target, project, metadata)

    assert (target / "projects" / "hist-001" / "index.json").is_file()
    assert not (target / "registry.json").exists()
    content_path.write_bytes(original_content)
    monkeypatch.setattr(evidence_importer.os, "rename", original_rename)
    assert (
        import_acquisition_project(
            source,
            target,
            project,
            metadata,
        ).candidate_count
        == 2
    )


def test_import_revalidates_source_after_target_verification_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_verify = evidence_importer.verify_evidence_registry
    first_candidate = next((source / "acquisition" / SESSION_ID / "candidates").glob("*.json"))
    candidate_value = json.loads(first_candidate.read_text(encoding="utf-8"))
    content_path = ArtifactRoot(source).resolve(candidate_value["content_ref"]["uri"])
    original_content = content_path.read_bytes()
    mutated = False

    def mutate_after_target_verification(
        root: object,
        project_id: str | None = None,
    ):
        nonlocal mutated
        report = original_verify(root, project_id)
        root_path = root.path if hasattr(root, "path") else Path(root)  # type: ignore[arg-type]
        if Path(root_path) == target.resolve() and not mutated:
            mutated = True
            content_path.write_bytes(b"changed after target verification")
        return report

    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        mutate_after_target_verification,
    )

    with pytest.raises(ValueError, match="acquisition import source changed"):
        import_acquisition_project(source, target, project, metadata)

    assert (target / "registry.json").is_file()
    content_path.write_bytes(original_content)
    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        original_verify,
    )
    assert (
        import_acquisition_project(
            source,
            target,
            project,
            metadata,
        ).candidate_count
        == 2
    )


def test_import_rejects_restricted_source_content_before_target_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_classify = acquisition_service.classify_and_redact

    def bypass_acquisition_redaction(
        value: object,
        *,
        secrets: tuple[str, ...] = (),
    ) -> RedactedPayload:
        del secrets
        return RedactedPayload(
            value=value,
            payload_classification="internal",
            redaction_applied=False,
        )

    target = tmp_path / "evidence"
    project = _project()
    unsafe_source = tmp_path / "unsafe-source"
    monkeypatch.setattr(
        acquisition_service,
        "classify_and_redact",
        bypass_acquisition_redaction,
    )
    unsafe_service = ManualAcquisitionService(
        unsafe_source,
        session_id=SESSION_ID,
        clock=lambda: NOW,
    )
    unsafe_candidate = unsafe_service.ingest(
        query="unsafe query",
        title="unsafe source",
        source_url="https://history.example/unsafe",
        document_date="1812-05-01",
        date_precision="day",
        curated_text="operator note C:/Users/example/private.txt",
        curator="repository curator",
    )
    unsafe_manifest = json.loads(
        (unsafe_service.session_dir / "manifest.json").read_text(encoding="utf-8")
    )
    unsafe_metadata = EvidenceImportMetadata.create(
        project_id="hist-001",
        session_id=SESSION_ID,
        manifest_sha256=str(unsafe_manifest["content_sha256"]),
        candidates=(
            EvidenceImportRow.from_dict(
                {
                    "source_spec_id": "hist001_unsafe",
                    "candidate_id": unsafe_candidate.candidate_id,
                    "candidate_record_sha256": unsafe_candidate.record_sha256,
                    "candidate_content_sha256": unsafe_candidate.content_sha256,
                    "source_type": "primary",
                    "license_basis": "Repository-authored paraphrase.",
                    "content_authorship": "repository_authored",
                    "retention_policy": "paraphrase_only",
                    "domains": ("logistics",),
                    "fact_ids": ("fact.unsafe",),
                    "role": "evidence",
                }
            ),
        ),
    )
    monkeypatch.setattr(
        acquisition_service,
        "classify_and_redact",
        original_classify,
    )

    with pytest.raises(ValueError, match="acquisition import source content"):
        import_acquisition_project(
            unsafe_source,
            target,
            project,
            unsafe_metadata,
        )

    assert not target.exists()


def test_interruption_before_project_publication_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_rename = evidence_importer.os.rename

    def interrupt_rename(source_path: str | Path, target_path: str | Path) -> None:
        raise RuntimeError("injected interruption before project publication")

    monkeypatch.setattr(evidence_importer.os, "rename", interrupt_rename)
    with pytest.raises(RuntimeError, match="injected interruption"):
        import_acquisition_project(source, target, project, metadata)
    assert not (target / "projects" / "hist-001").exists()

    monkeypatch.setattr(evidence_importer.os, "rename", original_rename)
    report = import_acquisition_project(source, target, project, metadata)
    assert report.candidate_count == 2


def test_project_publication_os_error_never_echoes_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)

    def fail_with_path(source_path: str | Path, target_path: str | Path) -> None:
        raise OSError(f"cannot rename {source_path} to {target_path}")

    monkeypatch.setattr(evidence_importer.os, "rename", fail_with_path)

    with pytest.raises(ValueError, match="evidence import target changed") as caught:
        import_acquisition_project(source, target, project, metadata)

    assert str(source) not in str(caught.value)
    assert str(target) not in str(caught.value)


def test_interruption_after_project_publication_repairs_registry_on_rerun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_publish_registry = evidence_importer._publish_registry

    def interrupt_registry(*args: object, **kwargs: object):
        raise RuntimeError("injected interruption before registry publication")

    monkeypatch.setattr(
        evidence_importer,
        "_publish_registry",
        interrupt_registry,
    )
    with pytest.raises(RuntimeError, match="injected interruption"):
        import_acquisition_project(source, target, project, metadata)
    assert (target / "projects" / "hist-001" / "index.json").is_file()
    assert not (target / "registry.json").exists()

    monkeypatch.setattr(
        evidence_importer,
        "_publish_registry",
        original_publish_registry,
    )
    report = import_acquisition_project(source, target, project, metadata)
    assert report.candidate_count == 2
    assert (target / "registry.json").is_file()


def test_concurrent_identical_importers_publish_one_complete_project(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=3)

    def run_import(_: int):
        return import_acquisition_project(source, target, project, metadata)

    with ThreadPoolExecutor(max_workers=2) as executor:
        reports = tuple(executor.map(run_import, range(2)))

    assert reports[0] == reports[1]
    assert verify_evidence_registry(target, "hist-001").candidate_count == 3


def test_import_never_persists_or_echoes_source_absolute_path(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    import_acquisition_project(source, target, project, metadata)
    source_bytes = str(source).encode()
    assert all(
        source_bytes not in path.read_bytes() for path in target.rglob("*") if path.is_file()
    )

    bad_metadata = replace(metadata, manifest_sha256="f" * 64)
    with pytest.raises(ValueError) as caught:
        import_acquisition_project(source, target, project, bad_metadata)
    assert str(source) not in str(caught.value)


def test_import_uses_dedicated_ignored_lock_namespace(tmp_path: Path) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)

    import_acquisition_project(source, target, project, metadata)

    assert not (tmp_path / ".locks").exists()
    assert tuple((tmp_path / ".tracelane-locks" / ".locks").glob("*.lock"))
