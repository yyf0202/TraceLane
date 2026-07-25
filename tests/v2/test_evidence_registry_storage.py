from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import traceback
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

import tracelane.evidence_registry as evidence_registry_api
from tracelane.evidence_registry import storage as evidence_storage
from tracelane.evidence_registry.storage import (
    EvidenceBlobStore,
    EvidenceRoot,
    evidence_root_identity,
    read_json_object,
    rollback_json_publication,
    write_json_create_or_match,
    write_json_create_or_match_receipt,
)
from tracelane.v2 import storage as v2_storage
from tracelane.v2.contracts import ArtifactRef


@pytest.fixture
def evidence_root(tmp_path: Path) -> EvidenceRoot:
    return EvidenceRoot.create(tmp_path / "evidence")


def test_package_api_does_not_export_unlocked_storage_mutators() -> None:
    for name in (
        "EvidenceBlobStore",
        "EvidenceRoot",
        "JsonPublicationReceipt",
        "evidence_root_mutation_lock",
        "rollback_json_publication",
        "write_json_create_or_match",
        "write_json_create_or_match_receipt",
    ):
        assert not hasattr(evidence_registry_api, name)


def _tree_snapshot(root: EvidenceRoot) -> dict[str, tuple[object, ...]]:
    snapshot: dict[str, tuple[object, ...]] = {}
    for path in (root.path, *sorted(root.path.rglob("*"))):
        metadata = path.lstat()
        snapshot[path.relative_to(root.path).as_posix() or "."] = (
            metadata.st_mode,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_nlink,
            metadata.st_size,
            metadata.st_mtime_ns,
            path.read_bytes() if stat.S_ISREG(metadata.st_mode) else None,
        )
    return snapshot


@pytest.mark.parametrize(
    ("uri", "relative"),
    [
        ("tracelane://evidence/projects/hist-001/project.json", "projects/hist-001/project.json"),
        ("tracelane://evidence/projects/hist-001/index.json", "projects/hist-001/index.json"),
        (
            "tracelane://evidence/projects/hist-001/candidates/candidate_a.json",
            "projects/hist-001/candidates/candidate_a.json",
        ),
        (
            "tracelane://evidence/projects/hist-001/reviews/review_a.json",
            "projects/hist-001/reviews/review_a.json",
        ),
        (
            "tracelane://evidence/projects/hist-001/transformations/transformation_a.json",
            "projects/hist-001/transformations/transformation_a.json",
        ),
    ],
)
def test_resolve_maps_valid_registry_uris(
    evidence_root: EvidenceRoot,
    uri: str,
    relative: str,
) -> None:
    assert evidence_root.resolve(uri) == evidence_root.path / Path(relative)


def test_resolve_maps_logical_blob_uri_to_sharded_physical_path(
    evidence_root: EvidenceRoot,
) -> None:
    digest = hashlib.sha256(b"payload").hexdigest()

    resolved = evidence_root.resolve(f"tracelane://evidence/blobs/sha256/{digest}")

    assert resolved == evidence_root.path / "blobs" / "sha256" / digest[:2] / f"{digest}.blob"


@pytest.mark.parametrize(
    "uri",
    [
        "tracelane://evidence/unlisted/namespace.json",
        "tracelane://evidence/projects/hist-001/import.json",
        "tracelane://evidence/projects/hist-001/project.json/extra",
        "tracelane://evidence/projects/hist-001/candidates",
        "tracelane://evidence/projects/hist-001/candidates/a.json/extra",
        "tracelane://evidence/projects/hist-001/other/a.json",
    ],
)
def test_resolve_rejects_unlisted_or_extra_depth_namespaces(
    evidence_root: EvidenceRoot,
    uri: str,
) -> None:
    with pytest.raises(ValueError, match="evidence URI"):
        evidence_root.resolve(uri)


@pytest.mark.parametrize(
    "uri",
    [
        f"tracelane://evidence/projects/{'a' * 256}/project.json",
        f"tracelane://evidence/projects/hist-001/candidates/{'a' * 251}.json",
    ],
)
def test_resolve_rejects_overlong_disk_components(
    evidence_root: EvidenceRoot,
    uri: str,
) -> None:
    with pytest.raises(ValueError, match="evidence URI"):
        evidence_root.resolve(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "tracelane://artifacts/projects/hist-001/project.json",
        "file:///private/evidence.json",
        "tracelane://evidence/",
        "tracelane://evidence//absolute.json",
        "tracelane://evidence/projects//project.json",
        "tracelane://evidence/projects/./project.json",
        "tracelane://evidence/projects/../outside.json",
        "tracelane://evidence/projects/%2e%2e/outside.json",
        "tracelane://evidence/projects/%2Foutside.json",
        r"tracelane://evidence/projects\hist-001\project.json",
        r"tracelane://evidence/C:\private\evidence.json",
        "tracelane://evidence/blobs/sha256/not-a-digest",
        f"tracelane://evidence/blobs/sha256/{'A' * 64}",
        f"tracelane://evidence/blobs/sha256/{'a' * 63}",
        f"tracelane://evidence/blobs/sha256/{'a' * 64}/extra",
    ],
)
def test_resolve_rejects_wrong_root_and_unsafe_paths(
    evidence_root: EvidenceRoot,
    uri: str,
) -> None:
    with pytest.raises(ValueError, match="evidence URI"):
        evidence_root.resolve(uri)


def test_resolve_must_exist_rejects_missing_target(evidence_root: EvidenceRoot) -> None:
    with pytest.raises(ValueError, match="evidence path is unavailable"):
        evidence_root.resolve(
            "tracelane://evidence/projects/hist-001/project.json",
            must_exist=True,
        )


def test_open_missing_root_does_not_create_it(tmp_path: Path) -> None:
    root = tmp_path / "evidence"

    with pytest.raises(ValueError, match="evidence root is unavailable"):
        EvidenceRoot.open(root)

    assert not root.exists()


def test_create_only_creates_the_exact_root(tmp_path: Path) -> None:
    missing_parent = tmp_path / "missing" / "evidence"

    with pytest.raises(ValueError, match="evidence root is unavailable"):
        EvidenceRoot.create(missing_parent)

    assert not missing_parent.parent.exists()


@pytest.mark.parametrize("failure", ["ancestor", "creation"])
def test_create_failure_has_sanitized_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    sensitive_path = tmp_path / "private-create-path"
    if failure == "ancestor":
        target = sensitive_path / "missing-parent" / "evidence"
    else:
        sensitive_path.mkdir()
        target = sensitive_path / "evidence"

        def fail_creation(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError(13, "creation denied", str(target))

        monkeypatch.setattr(
            evidence_storage,
            "_secure_ensure_directory_chain",
            fail_creation,
        )

    with pytest.raises(ValueError, match="^evidence root") as captured:
        EvidenceRoot.create(target)

    rendered = "".join(traceback.format_exception(captured.type, captured.value, captured.tb))
    assert captured.value.__cause__ is None
    assert str(tmp_path) not in rendered


def test_create_and_open_are_idempotent_for_an_existing_safe_root(tmp_path: Path) -> None:
    root_path = tmp_path / "evidence"

    created = EvidenceRoot.create(root_path)
    created_again = EvidenceRoot.create(root_path)
    opened = EvidenceRoot.open(root_path)

    assert created.path == root_path.resolve()
    assert created_again.path == created.path
    assert opened.path == created.path


def test_existing_root_lock_identity_follows_physical_directory_after_rename(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first-name"
    second = tmp_path / "second-name"
    first.mkdir()
    initial = evidence_root_identity(first)

    first.rename(second)

    assert evidence_root_identity(second) == initial


def test_mutation_lock_keeps_a_common_lock_across_root_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_path = tmp_path / "evidence"
    acquired: list[tuple[str, ...]] = []
    current: list[str] = []

    @contextmanager
    def capture_lock(path: Path, *, blocking: bool):
        assert blocking
        current.append(Path(path).name)
        yield

    monkeypatch.setattr(evidence_storage, "exclusive_file_lock", capture_lock)

    with evidence_storage.evidence_root_mutation_lock(root_path):
        acquired.append(tuple(current))
    current.clear()
    root_path.mkdir()
    with evidence_storage.evidence_root_mutation_lock(root_path):
        acquired.append(tuple(current))

    assert set(acquired[0]) & set(acquired[1])


@pytest.mark.parametrize(
    "target_parts",
    [
        ("..", "outside", "payload"),
        ("safe", "..", "..", "outside", "payload"),
    ],
)
def test_ensure_parent_rejects_dot_segment_escape_without_mutation(
    evidence_root: EvidenceRoot,
    tmp_path: Path,
    target_parts: tuple[str, ...],
) -> None:
    target = evidence_root.path.joinpath(*target_parts)

    with pytest.raises(ValueError, match="evidence path (?:is unsafe|escapes)"):
        evidence_root.ensure_parent(target)

    assert not (tmp_path / "outside").exists()


def test_create_rejects_a_link_in_existing_ancestors(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = tmp_path / "linked"
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="link|reparse"):
        EvidenceRoot.create(linked_parent / "evidence")

    assert not (outside / "evidence").exists()


def test_create_rolls_back_if_parent_is_replaced_before_mkdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original_parent = tmp_path / "original-parent"
    target = parent / "evidence"
    create_name = (
        "_windows_create_directory_at" if os.name == "nt" else "_posix_create_directory_at"
    )
    original_create = getattr(evidence_storage, create_name)

    def replace_parent_then_create(parent_handle: int, name: str):
        parent.rename(original_parent)
        try:
            parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("directory symlinks are unavailable on this host")
        return original_create(parent_handle, name)

    monkeypatch.setattr(evidence_storage, create_name, replace_parent_then_create)

    with pytest.raises(ValueError, match="evidence root"):
        EvidenceRoot.create(target)

    assert not (outside / "evidence").exists()
    assert not (original_parent / "evidence").exists()


def test_resolve_rejects_symlink_descendant(evidence_root: EvidenceRoot, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    projects = evidence_root.path / "projects"
    try:
        projects.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="link|reparse"):
        evidence_root.resolve("tracelane://evidence/projects/hist-001/project.json")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction test")
def test_open_rejects_windows_junction_reparse_point(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = tmp_path / "evidence"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("junction creation is unavailable on this Windows host")

    with pytest.raises(ValueError, match="link|reparse"):
        EvidenceRoot.open(junction)


def test_resolve_rejects_replaced_root_identity(tmp_path: Path) -> None:
    root_path = tmp_path / "evidence"
    root = EvidenceRoot.create(root_path)
    original = tmp_path / "original-evidence"
    root_path.rename(original)
    root_path.mkdir()

    with pytest.raises(ValueError, match="evidence root (?:changed|is unavailable)"):
        root.resolve("tracelane://evidence/registry.json")


def test_errors_do_not_expose_absolute_local_paths(tmp_path: Path) -> None:
    secret_root = tmp_path / "private-root-name" / "evidence"

    with pytest.raises(ValueError) as captured:
        EvidenceRoot.open(secret_root)

    assert str(secret_root) not in str(captured.value)
    assert str(secret_root.resolve(strict=False)) not in str(captured.value)
    rendered = "".join(traceback.format_exception(captured.type, captured.value, captured.tb))
    assert "private-root-name" not in rendered
    assert str(secret_root) not in rendered
    assert str(secret_root.parent) not in rendered


def test_blob_store_uses_logical_uri_and_is_idempotent(evidence_root: EvidenceRoot) -> None:
    store = EvidenceBlobStore(evidence_root)

    first = store.put_bytes(b"same payload", "text/plain", "evidence_blob")
    second = store.put_bytes(b"same payload", "text/plain", "evidence_blob")

    digest = hashlib.sha256(b"same payload").hexdigest()
    assert first == second
    assert first.uri == f"tracelane://evidence/blobs/sha256/{digest}"
    assert ".blob" not in first.uri
    assert f"/{digest[:2]}/" not in first.uri
    assert store.verify(first).read_bytes() == b"same payload"
    assert len(list((evidence_root.path / "blobs").rglob("*.blob"))) == 1


def test_blob_store_rejects_existing_different_bytes_without_overwrite(
    evidence_root: EvidenceRoot,
) -> None:
    store = EvidenceBlobStore(evidence_root)
    data = b"intended"
    digest = hashlib.sha256(data).hexdigest()
    uri = f"tracelane://evidence/blobs/sha256/{digest}"
    target = evidence_root.resolve(uri)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"conflicting")

    corrupt_state = _tree_snapshot(evidence_root)

    with pytest.raises(
        ValueError,
        match="^evidence blob conflicts with existing content$",
    ):
        store.put_bytes(data, "text/plain", "evidence_blob")

    assert target.read_bytes() == b"conflicting"
    assert _tree_snapshot(evidence_root) == corrupt_state


def test_blob_put_rolls_back_directory_if_root_is_replaced_before_mkdir(
    evidence_root: EvidenceRoot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    original_root = tmp_path / "original-evidence"
    create_name = (
        "_windows_create_directory_at" if os.name == "nt" else "_posix_create_directory_at"
    )
    original_create = getattr(evidence_storage, create_name)
    replaced = False

    def replace_root_then_create(parent_handle: int, name: str):
        nonlocal replaced
        if not replaced:
            replaced = True
            evidence_root.path.rename(original_root)
            try:
                evidence_root.path.symlink_to(outside, target_is_directory=True)
            except OSError:
                pytest.skip("directory symlinks are unavailable on this host")
        return original_create(parent_handle, name)

    monkeypatch.setattr(evidence_storage, create_name, replace_root_then_create)

    with pytest.raises(ValueError, match="evidence path|evidence root"):
        EvidenceBlobStore(evidence_root).put_bytes(
            b"payload",
            "text/plain",
            "evidence_blob",
        )

    assert not (outside / "blobs").exists()
    assert not (original_root / "blobs").exists()


def test_blob_put_rejects_invalid_media_type_without_echo(
    evidence_root: EvidenceRoot,
) -> None:
    sensitive = r"C:\private\media-type.txt"

    with pytest.raises(ValueError, match="blob reference metadata") as captured:
        EvidenceBlobStore(evidence_root).put_bytes(
            b"payload",
            sensitive,
            "evidence_blob",
        )

    assert sensitive not in str(captured.value)


@pytest.mark.parametrize(
    ("damage", "category"),
    [
        ("missing", "^evidence path is unavailable$"),
        ("truncated", "^evidence blob size mismatch$"),
        ("replaced", "^evidence blob hash mismatch$"),
    ],
)
def test_blob_verify_detects_missing_truncated_or_replaced_bytes(
    evidence_root: EvidenceRoot,
    damage: str,
    category: str,
) -> None:
    store = EvidenceBlobStore(evidence_root)
    reference = store.put_bytes(b"authenticated payload", "text/plain", "evidence_blob")
    target = evidence_root.resolve(reference.uri)
    if damage == "missing":
        target.unlink()
    elif damage == "truncated":
        target.write_bytes(b"short")
    else:
        replacement = target.with_name("replacement.blob")
        replacement.write_bytes(b"different replacement")
        os.replace(replacement, target)

    corrupt_state = _tree_snapshot(evidence_root)

    with pytest.raises(ValueError, match=category):
        store.verify(reference)

    assert _tree_snapshot(evidence_root) == corrupt_state


def test_blob_verify_rejects_hardlink_even_when_bytes_match(
    evidence_root: EvidenceRoot,
    tmp_path: Path,
) -> None:
    store = EvidenceBlobStore(evidence_root)
    reference = store.put_bytes(b"same bytes", "text/plain", "evidence_blob")
    target = evidence_root.resolve(reference.uri)
    outside = tmp_path / "outside.blob"
    outside.write_bytes(b"same bytes")
    target.unlink()
    try:
        os.link(outside, target)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="link"):
        store.verify(reference)

    assert outside.read_bytes() == b"same bytes"


@pytest.mark.parametrize(
    "reference_change",
    [
        {"kind": "raw_fetch"},
        {"uri": f"tracelane://evidence/blobs/sha256/{'0' * 64}"},
        {"media_type": "invalid"},
        {"sha256": "0" * 64},
        {"size_bytes": 1},
        {"schema_id": "tracelane://schemas/evidence-candidate/v1"},
    ],
)
def test_blob_verify_validates_reference_metadata_and_content_properties(
    evidence_root: EvidenceRoot,
    reference_change: dict[str, object],
) -> None:
    store = EvidenceBlobStore(evidence_root)
    reference = store.put_bytes(b"payload", "text/plain", "evidence_blob")
    altered = replace(reference, **reference_change)
    accepted_state = _tree_snapshot(evidence_root)

    with pytest.raises(ValueError, match="blob reference|unavailable|size|hash"):
        store.verify(altered)

    assert _tree_snapshot(evidence_root) == accepted_state


def test_blob_verify_rejects_stale_open_descriptor(
    evidence_root: EvidenceRoot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EvidenceBlobStore(evidence_root)
    reference = store.put_bytes(b"payload", "text/plain", "evidence_blob")
    target = evidence_root.resolve(reference.uri)
    stale = tmp_path / "stale.blob"
    stale.write_bytes(b"payload")
    original_open = os.open

    def open_stale(path: str | bytes | os.PathLike[str], flags: int, mode: int = 0o777) -> int:
        if Path(path) == target:
            return original_open(stale, flags, mode)
        return original_open(path, flags, mode)

    monkeypatch.setattr(v2_storage.os, "open", open_stale)

    with pytest.raises(ValueError, match="changed during access"):
        store.verify(reference)


def test_blob_verify_rejects_parent_replacement_during_read(
    evidence_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = EvidenceBlobStore(evidence_root)
    reference = store.put_bytes(b"payload", "text/plain", "evidence_blob")
    target = evidence_root.resolve(reference.uri)
    parent = target.parent
    original_validate_directory = v2_storage._validate_directory
    parent_calls = 0

    def validate_changed_parent(path: Path, label: str) -> os.stat_result:
        nonlocal parent_calls
        metadata = original_validate_directory(path, label)
        if Path(path) == parent:
            parent_calls += 1
            if parent_calls >= 2:
                values = list(metadata)
                values[1] = metadata.st_ino + 1
                return os.stat_result(values)
        return metadata

    monkeypatch.setattr(v2_storage, "_validate_directory", validate_changed_parent)

    with pytest.raises(ValueError, match="changed during access"):
        store.verify(reference)


def test_json_write_is_canonical_utf8_and_create_or_match(
    evidence_root: EvidenceRoot,
) -> None:
    uri = "tracelane://evidence/projects/hist-001/project.json"
    value = {"z": "雪", "a": [2, 1]}

    first = write_json_create_or_match(
        evidence_root,
        uri,
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        value,
    )
    second = write_json_create_or_match(
        evidence_root,
        uri,
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        value,
    )

    expected = '{"a":[2,1],"z":"雪"}\n'.encode()
    assert first == second
    assert evidence_root.resolve(uri).read_bytes() == expected
    assert not expected.startswith(b"\xef\xbb\xbf")
    assert expected.endswith(b"\n") and not expected.endswith(b"\n\n")
    assert (
        read_json_object(
            evidence_root,
            first,
            expected_kind="evidence_project",
            expected_schema_id="tracelane://schemas/evidence-project/v1",
        )
        == value
    )


def test_json_publisher_self_cleans_after_post_create_validation_failure(
    evidence_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = "tracelane://evidence/projects/hist-001/project.json"
    target = evidence_root.resolve(uri)

    def fail_validation(*args, **kwargs):
        raise ValueError("injected post-create validation failure")

    monkeypatch.setattr(evidence_storage, "read_json_object", fail_validation)

    with pytest.raises(ValueError):
        write_json_create_or_match(
            evidence_root,
            uri,
            "evidence_project",
            "tracelane://schemas/evidence-project/v1",
            {"project_id": "hist-001"},
        )

    assert not target.exists()


def test_json_publication_receipt_marks_identical_race_as_not_created(
    evidence_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uri = "tracelane://evidence/projects/hist-001/project.json"
    target = evidence_root.resolve(uri)

    def publish_external_then_conflict(path: Path, data: bytes, **kwargs):
        path.write_bytes(data)
        raise ValueError("evidence JSON already exists")

    monkeypatch.setattr(
        evidence_storage,
        "atomic_create_bytes_with_identity",
        publish_external_then_conflict,
        raising=False,
    )

    receipt = evidence_storage.write_json_create_or_match_receipt(
        evidence_root,
        uri,
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        {"project_id": "hist-001"},
    )

    assert receipt.created_by_this_call is False
    assert receipt.filesystem_identity == (target.lstat().st_dev, target.lstat().st_ino)
    evidence_storage.rollback_json_publication(evidence_root, receipt)
    assert target.exists()


@pytest.mark.parametrize("replacement_kind", ["different", "identical"])
def test_json_rollback_preserves_replacement_in_final_retirement_window(
    evidence_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    uri = "tracelane://evidence/projects/hist-001/project.json"
    value = {"project_id": "hist-001"}
    receipt = write_json_create_or_match_receipt(
        evidence_root,
        uri,
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        value,
    )
    target = evidence_root.resolve(uri, must_exist=True)
    replacement_data = (
        target.read_bytes()
        if replacement_kind == "identical"
        else b'{"owned_by":"competing-writer"}\n'
    )
    original_read = evidence_storage._read_json_publication_identity
    injected = False

    def replace_after_final_authentication(path: Path, root: EvidenceRoot):
        nonlocal injected
        data, identity = original_read(path, root)
        if path == target and not injected:
            injected = True
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(replacement_data)
            os.replace(replacement, target)
        return data, identity

    monkeypatch.setattr(
        evidence_storage,
        "_read_json_publication_identity",
        replace_after_final_authentication,
    )

    with pytest.raises(ValueError, match="rollback"):
        rollback_json_publication(evidence_root, receipt)

    assert injected
    assert target.read_bytes() == replacement_data


@pytest.mark.parametrize("replacement_kind", ["different", "identical"])
def test_json_replacement_preserves_competing_writer_at_backup_boundary(
    evidence_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    uri = "tracelane://evidence/projects/hist-001/project.json"
    write_json_create_or_match(
        evidence_root,
        uri,
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        {"version": "previous"},
    )
    target = evidence_root.resolve(uri, must_exist=True)
    previous_data = target.read_bytes()
    competitor_data = (
        previous_data if replacement_kind == "identical" else b'{"owned_by":"competing-writer"}\n'
    )
    original_move = evidence_storage.atomic_move_no_replace
    competitor_identity: tuple[int, int] | None = None
    injected = False

    def compete_before_backup(
        source: Path,
        destination: Path,
        *,
        label: str,
    ) -> None:
        nonlocal competitor_identity, injected
        if Path(source) == target and not injected:
            injected = True
            competitor = target.with_suffix(".competitor")
            competitor.write_bytes(competitor_data)
            os.replace(competitor, target)
            metadata = target.lstat()
            competitor_identity = metadata.st_dev, metadata.st_ino
        original_move(source, destination, label=label)

    monkeypatch.setattr(
        evidence_storage,
        "atomic_move_no_replace",
        compete_before_backup,
    )

    with pytest.raises(ValueError, match="target changed"):
        evidence_storage.replace_json_publication(
            evidence_root,
            uri,
            "evidence_project",
            "tracelane://schemas/evidence-project/v1",
            {"version": "replacement"},
        )

    metadata = target.lstat()
    assert injected
    assert competitor_identity == (metadata.st_dev, metadata.st_ino)
    assert target.read_bytes() == competitor_data


@pytest.mark.parametrize("claimant_kind", ["different", "identical"])
def test_authenticated_retirement_never_overwrites_destination_claimant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    claimant_kind: str,
) -> None:
    target = tmp_path / "owned.json"
    owned_data = b'{"owned":true}\n'
    target.write_bytes(owned_data)
    original_move = getattr(v2_storage, "atomic_move_no_replace", None)
    claimant_path: Path | None = None
    claimant_data = owned_data if claimant_kind == "identical" else b'{"claimant":true}\n'

    def claim_destination_then_move(
        source: Path,
        destination: Path,
        *,
        label: str,
    ) -> None:
        nonlocal claimant_path
        claimant_path = Path(destination)
        claimant_path.write_bytes(claimant_data)
        assert original_move is not None
        original_move(source, destination, label=label)

    monkeypatch.setattr(
        v2_storage,
        "atomic_move_no_replace",
        claim_destination_then_move,
        raising=False,
    )

    with pytest.raises(ValueError, match="could not be retired"):
        v2_storage.retire_authenticated_file(target, owned_data, root=tmp_path)

    assert target.read_bytes() == owned_data
    assert claimant_path is not None
    assert claimant_path.read_bytes() == claimant_data


@pytest.mark.parametrize("claimant_kind", ["different", "identical"])
def test_json_replacement_never_overwrites_backup_destination_claimant(
    evidence_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
    claimant_kind: str,
) -> None:
    uri = "tracelane://evidence/projects/hist-001/project.json"
    write_json_create_or_match(
        evidence_root,
        uri,
        "evidence_project",
        "tracelane://schemas/evidence-project/v1",
        {"version": "previous"},
    )
    target = evidence_root.resolve(uri, must_exist=True)
    previous_data = target.read_bytes()
    original_identity = target.lstat().st_dev, target.lstat().st_ino
    original_move = getattr(v2_storage, "atomic_move_no_replace", None)
    claimant_path: Path | None = None
    claimant_data = previous_data if claimant_kind == "identical" else b'{"claimant":true}\n'

    def claim_destination_then_move(
        source: Path,
        destination: Path,
        *,
        label: str,
    ) -> None:
        nonlocal claimant_path
        claimant_path = Path(destination)
        claimant_path.write_bytes(claimant_data)
        assert original_move is not None
        original_move(source, destination, label=label)

    monkeypatch.setattr(
        evidence_storage,
        "atomic_move_no_replace",
        claim_destination_then_move,
        raising=False,
    )

    with pytest.raises(ValueError, match="target changed"):
        evidence_storage.replace_json_publication(
            evidence_root,
            uri,
            "evidence_project",
            "tracelane://schemas/evidence-project/v1",
            {"version": "replacement"},
        )

    assert target.read_bytes() == previous_data
    assert (target.lstat().st_dev, target.lstat().st_ino) == original_identity
    assert claimant_path is not None
    assert claimant_path.read_bytes() == claimant_data


def test_project_index_json_round_trip(evidence_root: EvidenceRoot) -> None:
    uri = "tracelane://evidence/projects/hist-001/index.json"
    value = {"entries": []}

    reference = write_json_create_or_match(
        evidence_root,
        uri,
        "evidence_project_index",
        "tracelane://schemas/evidence-project-index/v1",
        value,
    )

    assert (
        read_json_object(
            evidence_root,
            reference,
            expected_kind="evidence_project_index",
            expected_schema_id="tracelane://schemas/evidence-project-index/v1",
        )
        == value
    )


def test_json_write_rejects_conflicting_existing_bytes_without_overwrite(
    evidence_root: EvidenceRoot,
) -> None:
    uri = "tracelane://evidence/projects/hist-001/project.json"
    target = evidence_root.resolve(uri)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"existing":true}\n')

    with pytest.raises(ValueError, match="conflict"):
        write_json_create_or_match(
            evidence_root,
            uri,
            "evidence_project",
            "tracelane://schemas/evidence-project/v1",
            {"replacement": True},
        )

    assert target.read_bytes() == b'{"existing":true}\n'


def test_json_read_authenticates_kind_schema_media_hash_and_size(
    evidence_root: EvidenceRoot,
) -> None:
    reference = write_json_create_or_match(
        evidence_root,
        "tracelane://evidence/registry.json",
        "evidence_registry",
        "tracelane://schemas/evidence-registry/v1",
        {"projects": []},
    )
    changes = [
        {"kind": "evidence_project"},
        {"schema_id": "tracelane://schemas/evidence-project/v1"},
        {"media_type": "text/plain"},
        {"sha256": "0" * 64},
        {"size_bytes": 1},
    ]

    for change in changes:
        with pytest.raises(ValueError, match="JSON reference|size|hash"):
            read_json_object(
                evidence_root,
                replace(reference, **change),
                expected_kind="evidence_registry",
                expected_schema_id="tracelane://schemas/evidence-registry/v1",
            )


def test_exported_json_read_sanitizes_injected_path_bearing_failure(
    evidence_root: EvidenceRoot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reference = write_json_create_or_match(
        evidence_root,
        "tracelane://evidence/registry.json",
        "evidence_registry",
        "tracelane://schemas/evidence-registry/v1",
        {"projects": []},
    )
    secret_path = evidence_root.path / "private-json-source" / "registry.json"

    def fail_read(*args: object, **kwargs: object) -> bytes:
        del args, kwargs
        raise OSError(13, "read denied", str(secret_path))

    monkeypatch.setattr(evidence_storage, "_secure_read", fail_read)

    with pytest.raises(ValueError) as captured:
        read_json_object(
            evidence_root,
            reference,
            expected_kind="evidence_registry",
            expected_schema_id="tracelane://schemas/evidence-registry/v1",
        )

    rendered = "".join(traceback.format_exception(captured.type, captured.value, captured.tb))
    assert captured.value.__cause__ is None
    assert "private-json-source" not in rendered
    assert str(secret_path) not in rendered
    assert secret_path.as_posix() not in rendered
    assert str(secret_path).replace("\\", "\\\\") not in rendered


@pytest.mark.parametrize(
    "payload",
    [
        b"\xef\xbb\xbf{}\n",
        b'{ "a": 1 }\n',
        b'{"a":1}',
        b'{"a":1}\n\n',
        b"[]\n",
        b"\xff\n",
    ],
)
def test_json_read_rejects_noncanonical_or_nonobject_payload(
    evidence_root: EvidenceRoot,
    payload: bytes,
) -> None:
    uri = "tracelane://evidence/registry.json"
    target = evidence_root.resolve(uri)
    target.write_bytes(payload)
    reference = ArtifactRef.from_dict(
        {
            "kind": "evidence_registry",
            "uri": uri,
            "media_type": "application/json",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "schema_id": "tracelane://schemas/evidence-registry/v1",
        }
    )

    with pytest.raises(ValueError, match="evidence JSON"):
        read_json_object(
            evidence_root,
            reference,
            expected_kind="evidence_registry",
            expected_schema_id="tracelane://schemas/evidence-registry/v1",
        )
