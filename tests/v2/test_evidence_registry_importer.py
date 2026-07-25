from __future__ import annotations

import json
import os
import stat
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tracelane.acquisition import ManualAcquisitionService
from tracelane.acquisition import service as acquisition_service
from tracelane.evidence_registry import importer as evidence_importer
from tracelane.evidence_registry import storage as evidence_storage
from tracelane.evidence_registry.contracts import (
    EvidenceImportMetadata,
    EvidenceImportRow,
    EvidenceProject,
)
from tracelane.evidence_registry.importer import import_acquisition_project
from tracelane.evidence_registry.index import (
    rebuild_evidence_indexes,
    verify_evidence_registry,
)
from tracelane.security import RedactedPayload
from tracelane.v2 import locking as v2_locking
from tracelane.v2.storage import ArtifactRoot, BlobStore

NOW = datetime(2026, 7, 25, tzinfo=UTC)
SESSION_ID = "acq_hist001_20260725"
pytestmark = pytest.mark.skipif(
    os.name != "nt",
    reason="acquisition import is a Windows-only capability",
)


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


def _tree_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[object, ...]] = {}
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        snapshot[path.relative_to(root).as_posix() or "."] = (
            metadata.st_mode,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            path.read_bytes() if stat.S_ISREG(metadata.st_mode) else None,
        )
    return snapshot


def _redirect_directory(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        if os.name != "nt":
            raise
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0


def _create_directory_junction(link: Path, target: Path) -> None:
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 0


def test_project_directory_publication_never_replaces_even_an_empty_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "staged-project"
    source.mkdir()
    (source / "project.json").write_text("{}\n", encoding="utf-8")
    destination = tmp_path / "published-project"
    destination.mkdir()
    metadata = destination.lstat()
    destination_identity = metadata.st_dev, metadata.st_ino

    with pytest.raises(FileExistsError, match="destination already exists"):
        evidence_importer._publish_project_directory_no_replace(
            source,
            destination,
        )

    current = destination.lstat()
    assert (current.st_dev, current.st_ino) == destination_identity
    assert list(destination.iterdir()) == []
    assert (source / "project.json").read_text(encoding="utf-8") == "{}\n"


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


@pytest.mark.parametrize("direction", ["target-under-source", "source-under-target"])
def test_import_rejects_overlapping_source_and_target_before_filesystem_mutation(
    tmp_path: Path,
    direction: str,
) -> None:
    if direction == "target-under-source":
        source, _, project, metadata = _import_case(tmp_path / "case", count=1)
        target = source / "nested-evidence"
        observed_root = source
    else:
        target = tmp_path / "outer-target"
        source, _, project, metadata = _import_case(target, count=1)
        observed_root = target
    before = _tree_snapshot(observed_root)

    with pytest.raises(
        ValueError,
        match="acquisition import source and target overlap",
    ) as caught:
        import_acquisition_project(source, target, project, metadata)

    assert _tree_snapshot(observed_root) == before
    assert str(source) not in str(caught.value)
    assert str(target) not in str(caught.value)
    assert not (observed_root / ".tracelane-locks").exists()
    assert not (observed_root / ".tracelane-staging").exists()


@pytest.mark.parametrize(
    ("reserved_name", "target_form"),
    [
        (".tracelane-staging", "equal"),
        (".tracelane-locks", "equal"),
        (".tracelane-staging", "inside"),
        (".tracelane-locks", "inside"),
    ],
)
def test_import_rejects_reserved_control_target_before_filesystem_mutation(
    tmp_path: Path,
    reserved_name: str,
    target_form: str,
) -> None:
    source, _, project, metadata = _import_case(tmp_path / "source-case", count=1)
    target_parent = tmp_path / "target-parent"
    target_parent.mkdir()
    reserved_root = target_parent / reserved_name
    target = reserved_root if target_form == "equal" else reserved_root / "evidence"
    before = _tree_snapshot(tmp_path)

    with pytest.raises(
        ValueError,
        match="^evidence import target is invalid$",
    ):
        import_acquisition_project(source, target, project, metadata)

    assert _tree_snapshot(tmp_path) == before


def test_filesystem_root_target_contains_derived_control_roots(
    tmp_path: Path,
) -> None:
    filesystem_root = Path(tmp_path.anchor)

    assert evidence_importer._target_overlaps_control_roots(filesystem_root)


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


@pytest.mark.parametrize("target_state", ["corrupt-registry", "unsupported-entry"])
def test_import_preflights_invalid_existing_target_before_any_publication(
    tmp_path: Path,
    target_state: str,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    target.mkdir()
    if target_state == "corrupt-registry":
        (target / "registry.json").write_bytes(b"corrupt")
    else:
        (target / "unsupported.bin").write_bytes(b"unsupported")
    before = _tree_snapshot(target)

    with pytest.raises(ValueError, match="evidence import target is invalid"):
        import_acquisition_project(source, target, project, metadata)

    assert _tree_snapshot(target) == before
    assert not (target / "projects").exists()
    assert not (target / "blobs").exists()


def test_import_authenticates_conflicting_project_before_publishing_new_blob(
    tmp_path: Path,
) -> None:
    source, target, project, metadata = _import_case(tmp_path / "first", count=1)
    import_acquisition_project(source, target, project, metadata)
    other_source, _, _, other_metadata = _import_case(
        tmp_path / "second",
        count=1,
    )
    before = _tree_snapshot(target)

    with pytest.raises(ValueError, match="evidence import target conflicts"):
        import_acquisition_project(
            other_source,
            target,
            _project(title="Conflicting HIST-001"),
            other_metadata,
        )

    assert _tree_snapshot(target) == before


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

    with pytest.raises(ValueError, match="^acquisition import source changed$"):
        import_acquisition_project(source, target, project, metadata)

    assert _tree_snapshot(target) == {}


def test_import_reauthenticates_source_after_target_blob_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    first_candidate = next((source / "acquisition" / SESSION_ID / "candidates").glob("*.json"))
    candidate_value = json.loads(first_candidate.read_text(encoding="utf-8"))
    content_path = ArtifactRoot(source).resolve(candidate_value["content_ref"]["uri"])
    original_put = evidence_importer.EvidenceBlobStore.put_bytes
    publication_calls = 0
    mutated = False

    def mutate_after_target_blob(
        self: object,
        data: bytes,
        media_type: str,
        kind: str,
    ):
        nonlocal mutated
        reference = original_put(self, data, media_type, kind)  # type: ignore[arg-type]
        if self.root.path == target.resolve() and not mutated:  # type: ignore[attr-defined]
            mutated = True
            content_path.write_bytes(b"changed after target blob publication")
        return reference

    def observe_project_publication(*args: object, **kwargs: object) -> None:
        nonlocal publication_calls
        del args, kwargs
        publication_calls += 1
        raise AssertionError("project publication followed a stale source snapshot")

    monkeypatch.setattr(
        evidence_importer.EvidenceBlobStore,
        "put_bytes",
        mutate_after_target_blob,
    )
    monkeypatch.setattr(
        evidence_importer,
        "_publish_project_directory_no_replace",
        observe_project_publication,
    )

    with pytest.raises(ValueError, match="^acquisition import source changed$"):
        import_acquisition_project(source, target, project, metadata)

    assert mutated
    assert publication_calls == 0
    assert not (target / "projects" / "hist-001").exists()
    assert not (target / "registry.json").exists()


def test_staged_validation_precedes_project_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_verify = evidence_importer.verify_evidence_registry
    rename_calls = 0

    def reject_staged_registry(root: object, project_id: str | None = None):
        root_path = root.path if hasattr(root, "path") else Path(root)  # type: ignore[arg-type]
        if ".tracelane-staging" in Path(root_path).parts:
            raise ValueError("injected staged validation failure")
        return original_verify(root, project_id)

    def observe_rename(*args: object, **kwargs: object) -> None:
        nonlocal rename_calls
        del args, kwargs
        rename_calls += 1
        raise AssertionError("project publication preceded staged validation")

    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        reject_staged_registry,
    )
    monkeypatch.setattr(
        evidence_importer,
        "_publish_project_directory_no_replace",
        observe_rename,
    )

    with pytest.raises(ValueError, match="^acquisition import metadata is invalid$"):
        import_acquisition_project(source, target, project, metadata)

    assert rename_calls == 0
    assert not (target / "projects" / "hist-001").exists()
    assert not (target / "registry.json").exists()


def test_project_validation_precedes_global_registry_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_publish = evidence_importer._publish_registry
    validation_calls = 0
    registry_publication_calls = 0
    rejected_state: dict[str, tuple[object, ...]] = {}

    def reject_published_project(*args: object, **kwargs: object) -> None:
        nonlocal validation_calls
        del args, kwargs
        validation_calls += 1
        rejected_state.update(_tree_snapshot(target))
        raise ValueError("evidence import target is invalid")

    def observe_registry_publication(*args: object, **kwargs: object):
        nonlocal registry_publication_calls
        registry_publication_calls += 1
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(
        evidence_importer,
        "_authenticate_existing_project",
        reject_published_project,
    )
    monkeypatch.setattr(
        evidence_importer,
        "_publish_registry",
        observe_registry_publication,
    )

    with pytest.raises(ValueError, match="^evidence import target is invalid$"):
        import_acquisition_project(source, target, project, metadata)

    assert validation_calls == 1
    assert registry_publication_calls == 0
    assert rejected_state
    assert _tree_snapshot(target) != rejected_state
    assert not (target / "projects" / "hist-001").exists()
    assert not (target / "registry.json").exists()


def test_post_publication_source_mutation_does_not_invalidate_copied_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    original_publish = evidence_importer._publish_project_directory_no_replace
    first_candidate = next((source / "acquisition" / SESSION_ID / "candidates").glob("*.json"))
    candidate_value = json.loads(first_candidate.read_text(encoding="utf-8"))
    content_path = ArtifactRoot(source).resolve(candidate_value["content_ref"]["uri"])
    original_content = content_path.read_bytes()

    def mutate_after_project_publication(
        source_path: str | Path,
        target_path: str | Path,
        *args: object,
        **kwargs: object,
    ):
        receipt = original_publish(
            Path(source_path),
            Path(target_path),
            *args,
            **kwargs,
        )
        content_path.write_bytes(b"changed during project publication")
        return receipt

    monkeypatch.setattr(
        evidence_importer,
        "_publish_project_directory_no_replace",
        mutate_after_project_publication,
    )

    report = import_acquisition_project(source, target, project, metadata)

    assert report.candidate_count == 2
    assert (target / "projects" / "hist-001" / "index.json").is_file()
    assert (target / "registry.json").is_file()
    assert content_path.read_bytes() != original_content
    assert verify_evidence_registry(target, "hist-001").candidate_count == 2


def test_post_verification_source_mutation_does_not_reverse_success(
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

    report = import_acquisition_project(source, target, project, metadata)

    assert report.candidate_count == 2
    assert (target / "registry.json").is_file()
    assert content_path.read_bytes() != original_content
    assert original_content


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
    original_publish = evidence_importer._publish_project_directory_no_replace
    attempted_source: Path | None = None

    def interrupt_rename(
        source_path: str | Path,
        target_path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal attempted_source
        del args, kwargs
        attempted_source = Path(source_path)
        raise RuntimeError("injected interruption before project publication")

    monkeypatch.setattr(
        evidence_importer,
        "_publish_project_directory_no_replace",
        interrupt_rename,
    )
    with pytest.raises(RuntimeError, match="injected interruption"):
        import_acquisition_project(source, target, project, metadata)
    assert not (target / "projects" / "hist-001").exists()
    assert attempted_source is not None
    assert attempted_source.parents[2].name == ".tracelane-staging"
    assert not any(path.name.startswith(".evidence-import-") for path in tmp_path.iterdir())

    monkeypatch.setattr(
        evidence_importer,
        "_publish_project_directory_no_replace",
        original_publish,
    )
    report = import_acquisition_project(source, target, project, metadata)
    assert report.candidate_count == 2


def test_project_publication_os_error_never_echoes_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)

    def fail_with_path(
        source_path: str | Path,
        target_path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        del args, kwargs
        raise OSError(
            13,
            "rename denied",
            str(source_path),
            None,
            str(target_path),
        )

    monkeypatch.setattr(
        evidence_importer,
        "_publish_project_directory_no_replace",
        fail_with_path,
    )

    with pytest.raises(ValueError, match="evidence import target changed") as caught:
        import_acquisition_project(source, target, project, metadata)

    assert str(source) not in str(caught.value)
    assert str(target) not in str(caught.value)
    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert str(tmp_path) not in rendered


def test_target_namespace_publication_race_preserves_winning_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=2)
    winning_state: dict[str, tuple[object, ...]] = {}
    marker = b"published by competing importer\n"

    def publish_competing_project(
        source_path: str | Path,
        target_path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        del source_path, args, kwargs
        final_project = Path(target_path)
        final_project.mkdir(parents=True)
        (final_project / "winner.marker").write_bytes(marker)
        winning_state.update(_tree_snapshot(target))
        raise FileExistsError("competing project publication won")

    monkeypatch.setattr(
        evidence_importer,
        "_publish_project_directory_no_replace",
        publish_competing_project,
    )

    with pytest.raises(ValueError, match="^evidence import target changed$"):
        import_acquisition_project(source, target, project, metadata)

    assert winning_state
    assert _tree_snapshot(target) == winning_state
    assert (target / "projects" / "hist-001" / "winner.marker").read_bytes() == marker
    assert not (target / "registry.json").exists()


def test_source_read_os_error_never_echoes_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_read = evidence_importer.secure_read_bytes

    def fail_source_manifest_read(
        path: str | Path,
        *,
        root: str | Path,
        label: str,
    ) -> bytes:
        if label == "acquisition import manifest":
            raise OSError(13, "read denied", str(path))
        return original_read(path, root=root, label=label)

    monkeypatch.setattr(
        evidence_importer,
        "secure_read_bytes",
        fail_source_manifest_read,
    )

    with pytest.raises(
        ValueError,
        match="acquisition import source is invalid",
    ) as caught:
        import_acquisition_project(source, target, project, metadata)

    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert caught.value.__cause__ is None
    assert str(tmp_path) not in rendered
    assert not target.exists()


def test_existing_registry_read_os_error_never_echoes_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    import_acquisition_project(source, target, project, metadata)
    before = _tree_snapshot(target)
    original_read = evidence_importer.secure_read_bytes

    def fail_existing_registry_read(
        path: str | Path,
        *,
        root: str | Path,
        label: str,
    ) -> bytes:
        if label == "evidence import existing registry":
            raise OSError(13, "read denied", str(path))
        return original_read(path, root=root, label=label)

    monkeypatch.setattr(
        evidence_importer,
        "secure_read_bytes",
        fail_existing_registry_read,
    )

    with pytest.raises(
        ValueError,
        match="evidence import target is invalid",
    ) as caught:
        import_acquisition_project(source, target, project, metadata)

    rendered = "".join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert caught.value.__cause__ is None
    assert str(tmp_path) not in rendered
    assert _tree_snapshot(target) == before


def test_staging_cleanup_os_error_never_echoes_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError(13, "cleanup denied", str(tmp_path / "private-stage"))

    monkeypatch.setattr(
        evidence_importer,
        "_retire_owned_directory",
        fail_cleanup,
    )

    report = import_acquisition_project(source, target, project, metadata)

    assert report.candidate_count == 1
    assert verify_evidence_registry(target, "hist-001").candidate_count == 1


def test_staging_cleanup_preserves_replacement_swapped_before_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_cleanup = evidence_importer._safe_remove_stage
    saved_owned = tmp_path / "owned-stage"
    marker = b"competing staging replacement\n"
    competing_stage: Path | None = None

    def swap_before_cleanup(
        stage_path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal competing_stage
        competing_stage = Path(stage_path)
        competing_stage.rename(saved_owned)
        competing_stage.mkdir()
        (competing_stage / "winner.marker").write_bytes(marker)
        original_cleanup(stage_path, *args, **kwargs)

    monkeypatch.setattr(
        evidence_importer,
        "_safe_remove_stage",
        swap_before_cleanup,
    )

    report = import_acquisition_project(source, target, project, metadata)

    assert report.candidate_count == 1
    assert competing_stage is not None
    assert (competing_stage / "winner.marker").read_bytes() == marker
    assert saved_owned.is_dir()
    assert verify_evidence_registry(target, "hist-001").candidate_count == 1


def test_project_publication_does_not_use_replacing_os_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)

    def reject_replacing_rename(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("raw os.rename is not an atomic no-replace publication")

    monkeypatch.setattr(evidence_importer.os, "rename", reject_replacing_rename)

    report = import_acquisition_project(source, target, project, metadata)

    assert report.candidate_count == 1
    assert verify_evidence_registry(target, "hist-001").candidate_count == 1


def test_interruption_after_project_publication_rolls_back_and_retry_succeeds(
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
    assert not (target / "projects" / "hist-001").exists()
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


def test_import_report_registry_digest_comes_from_final_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_publish = evidence_importer._publish_registry

    def publish_with_untrusted_return(root: object):
        receipt = original_publish(root)  # type: ignore[arg-type]
        return replace(receipt, reference=replace(receipt.reference, sha256="f" * 64))

    monkeypatch.setattr(
        evidence_importer,
        "_publish_registry",
        publish_with_untrusted_return,
    )

    report = import_acquisition_project(source, target, project, metadata)

    verified = verify_evidence_registry(target, "hist-001")
    assert report.registry_sha256 == verified.registry_sha256
    assert report.registry_sha256 != "f" * 64


def test_final_verification_failure_precedes_commit_and_rolls_back_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_verify = evidence_importer.verify_evidence_registry
    original_commit = evidence_importer.commit_json_replacement
    commit_calls = 0

    def fail_final_verification(root: object, project_id: str | None = None):
        root_path = Path(getattr(root, "path", root))
        if root_path == target.resolve():
            raise ValueError("injected final verification failure")
        return original_verify(root, project_id)

    def record_commit(receipt: object) -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit(receipt)  # type: ignore[arg-type]

    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        fail_final_verification,
    )
    monkeypatch.setattr(evidence_importer, "commit_json_replacement", record_commit)

    with pytest.raises(ValueError, match="evidence import failed"):
        import_acquisition_project(source, target, project, metadata)

    assert commit_calls == 0
    assert not (target / "projects" / "hist-001").exists()
    assert not (target / "registry.json").exists()

    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        original_verify,
    )
    report = import_acquisition_project(source, target, project, metadata)
    assert report.candidate_count == 1
    assert commit_calls == 1
    assert original_verify(target, "hist-001").candidate_count == 1


def test_import_rollback_preserves_competing_project_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_authenticate = evidence_importer._authenticate_existing_project
    saved_owned = tmp_path / "owned-project"
    marker = b"competing project replacement\n"
    replaced = False

    def replace_before_authentication(*args: object, **kwargs: object) -> None:
        nonlocal replaced
        root = args[0]
        final_project = root.path / "projects" / "hist-001"  # type: ignore[attr-defined]
        if final_project.exists() and not replaced:
            replaced = True
            final_project.rename(saved_owned)
            final_project.mkdir()
            (final_project / "winner.marker").write_bytes(marker)
            raise ValueError("competing replacement won")
        original_authenticate(*args, **kwargs)

    monkeypatch.setattr(
        evidence_importer,
        "_authenticate_existing_project",
        replace_before_authentication,
    )

    with pytest.raises(ValueError, match="^evidence import target is invalid$"):
        import_acquisition_project(source, target, project, metadata)

    assert replaced
    assert (target / "projects" / "hist-001" / "winner.marker").read_bytes() == marker
    assert not (target / "registry.json").exists()


def test_import_rollback_preserves_replacement_swapped_after_quarantine_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_verify = evidence_importer.verify_evidence_registry
    original_snapshot = evidence_importer._project_directory_snapshot
    saved_owned = tmp_path / "owned-quarantine"
    marker = b"competing quarantine replacement\n"
    competing_quarantine: Path | None = None

    def fail_final_verification(root: object, project_id: str | None = None):
        root_path = Path(getattr(root, "path", root))
        if root_path == target.resolve():
            raise ValueError("injected final verification failure")
        return original_verify(root, project_id)

    def swap_after_quarantine_snapshot(path: Path):
        nonlocal competing_quarantine
        snapshot = original_snapshot(path)
        candidate = Path(path)
        if competing_quarantine is None and (
            candidate.name.startswith(".evidence-project-rollback-")
            or candidate.name.startswith("project-")
        ):
            competing_quarantine = candidate
            candidate.rename(saved_owned)
            candidate.mkdir()
            (candidate / "winner.marker").write_bytes(marker)
        return snapshot

    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        fail_final_verification,
    )
    monkeypatch.setattr(
        evidence_importer,
        "_project_directory_snapshot",
        swap_after_quarantine_snapshot,
    )

    with pytest.raises(
        ValueError,
        match="^evidence import quarantine requires maintenance$",
    ):
        import_acquisition_project(source, target, project, metadata)

    assert competing_quarantine is not None
    assert (competing_quarantine / "winner.marker").read_bytes() == marker
    assert saved_owned.is_dir()
    assert not (target / "projects" / "hist-001").exists()
    assert not (target / "registry.json").exists()


def test_import_retirement_parent_replacement_cannot_redirect_into_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_verify = evidence_importer.verify_evidence_registry
    original_move = evidence_importer._move_project_directory_no_replace
    saved_retired = tmp_path / "owned-retired-parent"
    competing_marker = b"competing retirement parent\n"
    swapped = False

    def fail_final_verification(root: object, project_id: str | None = None):
        root_path = Path(getattr(root, "path", root))
        if root_path == target.resolve():
            raise ValueError("injected final verification failure")
        return original_verify(root, project_id)

    def replace_parent_before_move(
        source_path: str | Path,
        target_path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal swapped
        destination = Path(target_path)
        if not swapped and destination.name.startswith("project-"):
            swapped = True
            destination.parent.rename(saved_retired)
            _redirect_directory(destination.parent, target / "projects")
            (target / "projects" / "competing.marker").write_bytes(competing_marker)
        original_move(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        fail_final_verification,
    )
    monkeypatch.setattr(
        evidence_importer,
        "_move_project_directory_no_replace",
        replace_parent_before_move,
    )

    with pytest.raises(
        ValueError,
        match="^evidence import quarantine requires maintenance$",
    ):
        import_acquisition_project(source, target, project, metadata)

    assert swapped
    assert (target / "projects" / "competing.marker").read_bytes() == competing_marker
    assert not any(path.name.startswith(("project-", "stage-")) for path in target.rglob("*"))
    assert not (target / "projects" / "hist-001").exists()
    assert not (target / "registry.json").exists()
    retired_names = {path.name for path in saved_retired.iterdir()}
    assert any(name.startswith("project-") for name in retired_names)
    assert any(name.startswith("stage-") for name in retired_names)


def test_publication_time_quarantine_mismatch_preserves_maintenance_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_snapshot = evidence_importer._project_directory_snapshot
    final_project = target / "projects" / "hist-001"
    saved_owned = tmp_path / "publication-owned-quarantine"
    competing_marker = b"publication-time competing quarantine\n"
    target_snapshots = 0
    competing_quarantine: Path | None = None

    def fail_publication_then_swap_quarantine(path: Path):
        nonlocal competing_quarantine, target_snapshots
        candidate = Path(path)
        if candidate == final_project:
            target_snapshots += 1
            if target_snapshots == 1:
                raise ValueError("injected publication validation failure")
        snapshot = original_snapshot(candidate)
        if (
            competing_quarantine is None
            and candidate.parent.name == "retired"
            and candidate.name.startswith("project-")
        ):
            competing_quarantine = candidate
            candidate.rename(saved_owned)
            candidate.mkdir()
            (candidate / "winner.marker").write_bytes(competing_marker)
        return snapshot

    monkeypatch.setattr(
        evidence_importer,
        "_project_directory_snapshot",
        fail_publication_then_swap_quarantine,
    )

    with pytest.raises(
        ValueError,
        match="^evidence import quarantine requires maintenance$",
    ):
        import_acquisition_project(source, target, project, metadata)

    assert target_snapshots >= 2
    assert competing_quarantine is not None
    assert (competing_quarantine / "winner.marker").read_bytes() == competing_marker
    assert saved_owned.is_dir()
    assert not final_project.exists()
    assert not (target / "registry.json").exists()


def test_retirement_handle_acquisition_rejects_post_creation_redirect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "evidence"
    live_retired = target / "projects" / "retired"
    live_retired.mkdir(parents=True)
    staging_namespace = tmp_path / ".tracelane-staging"
    staging_namespace.mkdir()
    retirement_path = staging_namespace / "retired"
    owned_source = tmp_path / "owned-stage"
    owned_source.mkdir()
    (owned_source / "owned.marker").write_bytes(b"owned stage\n")
    owned_metadata = owned_source.lstat()
    ownership_receipt = evidence_importer._DirectoryOwnershipReceipt(
        path=owned_source,
        directory_identity=(owned_metadata.st_dev, owned_metadata.st_ino),
    )
    original_create = evidence_importer.EvidenceRoot.create
    saved_namespace = tmp_path / "authenticated-staging-namespace"
    marker = b"competing handle-acquisition destination\n"
    swapped = False

    def redirect_after_authenticated_creation(path: str | Path):
        nonlocal swapped
        root = original_create(path)
        candidate = Path(path)
        if not swapped and candidate.name == "retired":
            swapped = True
            candidate.parent.rename(saved_namespace)
            (target / "projects" / "competing.marker").write_bytes(marker)
            _redirect_directory(candidate.parent, target / "projects")
        return root

    monkeypatch.setattr(
        evidence_importer.EvidenceRoot,
        "create",
        redirect_after_authenticated_creation,
    )

    with pytest.raises(ValueError):
        retirement_receipt = evidence_importer._open_retirement_directory(
            retirement_path,
            target,
        )
        evidence_importer._retire_owned_directory(
            ownership_receipt,
            retirement_receipt,
            prefix="stage",
        )

    assert swapped
    assert (target / "projects" / "competing.marker").read_bytes() == marker
    assert list(live_retired.iterdir()) == []
    assert saved_namespace.is_dir()
    assert (owned_source / "owned.marker").read_bytes() == b"owned stage\n"


def test_retirement_handle_acquisition_compares_full_authenticated_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "evidence"
    staging_namespace = tmp_path / ".tracelane-staging"
    staging_namespace.mkdir()
    retirement_path = staging_namespace / "retired"

    def mismatched_device_identity(handle: int) -> tuple[int, int]:
        del handle
        metadata = retirement_path.lstat()
        return metadata.st_dev ^ 1, metadata.st_ino

    monkeypatch.setattr(
        evidence_importer,
        "_windows_directory_handle_stat_identity",
        mismatched_device_identity,
        raising=False,
    )
    retirement_receipt = None
    try:
        with pytest.raises(
            ValueError,
            match="^retirement directory changed during handle acquisition$",
        ):
            retirement_receipt = evidence_importer._open_retirement_directory(
                retirement_path,
                target,
            )
    finally:
        if retirement_receipt is not None:
            evidence_importer._close_retirement_directory(retirement_receipt)


def test_retirement_handle_acquisition_rejects_direct_leaf_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "evidence"
    staging_namespace = tmp_path / ".tracelane-staging"
    staging_namespace.mkdir()
    retirement_path = staging_namespace / "retired"
    saved_retirement = tmp_path / "authenticated-retirement"
    original_create = evidence_importer.EvidenceRoot.create
    swapped = False

    def replace_after_authenticated_creation(path: str | Path):
        nonlocal swapped
        root = original_create(path)
        candidate = Path(path)
        if not swapped and candidate.name == "retired":
            swapped = True
            candidate.rename(saved_retirement)
            candidate.mkdir()
        return root

    monkeypatch.setattr(
        evidence_importer.EvidenceRoot,
        "create",
        replace_after_authenticated_creation,
    )

    with pytest.raises(
        ValueError,
        match="^retirement directory changed during handle acquisition$",
    ):
        evidence_importer._open_retirement_directory(
            retirement_path,
            target,
        )

    assert swapped
    assert saved_retirement.is_dir()
    assert retirement_path.is_dir()


def test_stage_only_quarantine_mismatch_preserves_maintenance_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_move = evidence_importer._move_project_directory_no_replace
    saved_owned = tmp_path / "stage-owned-quarantine"
    marker = b"stage-only competing quarantine\n"
    competing_quarantine: Path | None = None

    def fail_before_project_publication(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise ValueError("evidence import target is invalid")

    def swap_stage_after_movement(
        source_path: str | Path,
        target_path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal competing_quarantine
        original_move(source_path, target_path, *args, **kwargs)
        destination = Path(target_path)
        if competing_quarantine is None and destination.name.startswith("stage-"):
            competing_quarantine = destination
            destination.rename(saved_owned)
            destination.mkdir()
            (destination / "winner.marker").write_bytes(marker)

    monkeypatch.setattr(
        evidence_importer,
        "_preflight_existing_target",
        fail_before_project_publication,
    )
    monkeypatch.setattr(
        evidence_importer,
        "_move_project_directory_no_replace",
        swap_stage_after_movement,
    )

    with pytest.raises(
        ValueError,
        match="^evidence import quarantine requires maintenance$",
    ):
        import_acquisition_project(source, target, project, metadata)

    assert competing_quarantine is not None
    assert (competing_quarantine / "winner.marker").read_bytes() == marker
    assert saved_owned.is_dir()
    assert not target.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows dangling junction regression")
def test_project_retirement_treats_dangling_junction_as_live_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_verify = evidence_importer.verify_evidence_registry
    original_move = evidence_importer._move_project_directory_no_replace
    dangling_target = tmp_path / "missing-junction-target"
    dangling_project: Path | None = None

    def fail_final_verification(root: object, project_id: str | None = None):
        root_path = Path(getattr(root, "path", root))
        if root_path == target.resolve():
            raise ValueError("injected final verification failure")
        return original_verify(root, project_id)

    def install_dangling_junction_after_move(
        source_path: str | Path,
        target_path: str | Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal dangling_project
        destination = Path(target_path)
        original_move(source_path, target_path, *args, **kwargs)
        if dangling_project is None and destination.name.startswith("project-"):
            dangling_project = Path(source_path)
            _create_directory_junction(dangling_project, dangling_target)
            assert dangling_project.is_junction()
            assert not dangling_project.exists()

    monkeypatch.setattr(
        evidence_importer,
        "verify_evidence_registry",
        fail_final_verification,
    )
    monkeypatch.setattr(
        evidence_importer,
        "_move_project_directory_no_replace",
        install_dangling_junction_after_move,
    )

    with pytest.raises(
        ValueError,
        match="^evidence import quarantine requires maintenance$",
    ):
        import_acquisition_project(source, target, project, metadata)

    assert dangling_project is not None
    assert dangling_project.is_junction()
    assert dangling_project.lstat()
    assert not (target / "registry.json").exists()


def test_import_exit_only_lock_validation_failure_preserves_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)
    original_validate = v2_locking._validate_acquired_lock
    target_lock_name = (
        f"evidence-import-{evidence_storage._evidence_root_parent_identity(target.resolve())}.lock"
    )
    validations_for_first_lock = 0

    def fail_outer_lock_exit(*args: object, **kwargs: object) -> None:
        nonlocal validations_for_first_lock
        path = Path(args[1])
        original_validate(*args, **kwargs)
        if path.name == target_lock_name:
            validations_for_first_lock += 1
            if validations_for_first_lock == 2:
                raise ValueError("injected exit-only lock validation failure")

    monkeypatch.setattr(v2_locking, "_validate_acquired_lock", fail_outer_lock_exit)

    report = import_acquisition_project(source, target, project, metadata)

    assert validations_for_first_lock == 2
    assert report.candidate_count == 1
    assert verify_evidence_registry(target, "hist-001").candidate_count == 1


def test_import_uses_dedicated_ignored_lock_namespace(tmp_path: Path) -> None:
    source, target, project, metadata = _import_case(tmp_path, count=1)

    import_acquisition_project(source, target, project, metadata)

    assert not (tmp_path / ".locks").exists()
    lock_root = tmp_path / ".tracelane-locks" / ".locks"
    import_locks = tuple(lock_root.glob("*.lock"))
    assert import_locks

    (target / "projects" / "hist-001" / "index.json").unlink()
    (target / "registry.json").unlink()
    rebuild_evidence_indexes(target, "hist-001")

    rebuild_locks = tuple(lock_root.glob("*.lock"))
    assert set(import_locks) < set(rebuild_locks)
    assert len(rebuild_locks) == len(import_locks) + 1
    rebuild_evidence_indexes(target, "hist-001")
    assert tuple(lock_root.glob("*.lock")) == rebuild_locks
