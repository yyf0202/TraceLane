from __future__ import annotations

import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tracelane.v2 import storage as storage_module
from tracelane.v2.storage import ArtifactRoot, BlobStore, atomic_write_bytes


def test_blob_store_deduplicates_and_verifies_content(tmp_path: Path) -> None:
    store = BlobStore(ArtifactRoot(tmp_path))

    first = store.put_bytes(b"same payload", "text/plain", "evidence_blob")
    second = store.put_bytes(b"same payload", "text/plain", "evidence_blob")

    digest = hashlib.sha256(b"same payload").hexdigest()
    assert first == second
    assert first.uri == f"tracelane://artifacts/blobs/sha256/{digest[:2]}/{digest}.blob"
    assert store.verify(first).read_bytes() == b"same payload"
    assert len(list((tmp_path / "blobs").rglob("*.blob"))) == 1


def test_blob_store_does_not_leave_temporary_files(tmp_path: Path) -> None:
    store = BlobStore(ArtifactRoot(tmp_path))

    store.put_bytes(b"payload", "application/octet-stream", "raw_fetch")

    assert list(tmp_path.rglob("*.tmp")) == []


@pytest.mark.parametrize(
    "uri",
    [
        "tracelane://artifacts/../outside.json",
        "tracelane://artifacts/blobs/../../outside.json",
        "tracelane://fixtures/v0.2/case.json",
        "file:///tmp/outside.json",
    ],
)
def test_artifact_root_rejects_wrong_root_or_traversal(tmp_path: Path, uri: str) -> None:
    root = ArtifactRoot(tmp_path)

    with pytest.raises(ValueError, match="artifact URI|escapes artifact root"):
        root.resolve(uri)


def test_verify_detects_tampered_blob(tmp_path: Path) -> None:
    store = BlobStore(ArtifactRoot(tmp_path))
    reference = store.put_bytes(b"original", "application/octet-stream", "raw_fetch")
    store.root.resolve(reference.uri).write_bytes(b"tampered")

    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        store.verify(reference)


def test_verify_detects_missing_blob(tmp_path: Path) -> None:
    store = BlobStore(ArtifactRoot(tmp_path))
    reference = store.put_bytes(b"original", "application/octet-stream", "raw_fetch")
    store.root.resolve(reference.uri).unlink()

    with pytest.raises(ValueError, match="unavailable"):
        store.verify(reference)


def test_artifact_root_rejects_symlink_ancestor(tmp_path: Path) -> None:
    root_path = tmp_path / "root"
    outside = tmp_path / "outside"
    root_path.mkdir()
    outside.mkdir()
    link = root_path / "blobs"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this Windows host")

    root = ArtifactRoot(root_path)
    with pytest.raises(ValueError, match="link|reparse|escapes"):
        root.resolve("tracelane://artifacts/blobs/sha256/aa/file.blob")


def test_blob_store_rejects_hard_linked_blob_even_when_bytes_match(tmp_path: Path) -> None:
    store = BlobStore(ArtifactRoot(tmp_path / "artifacts"))
    reference = store.put_bytes(b"same bytes", "text/plain", "evidence_blob")
    target = store.root.resolve(reference.uri)
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


def test_atomic_write_rejects_existing_hard_link_without_replacing_it(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"sentinel")
    target = tmp_path / "target.json"
    try:
        os.link(outside, target)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="link"):
        atomic_write_bytes(target, b"replacement")

    assert outside.read_bytes() == b"sentinel"
    assert target.read_bytes() == b"sentinel"


def test_atomic_write_rejects_replaced_temporary_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"original")
    original_lstat = Path.lstat
    target_lstat_calls = 0

    def replace_temporary_before_publication(
        path: Path,
        *args: object,
        **kwargs: object,
    ):
        nonlocal target_lstat_calls
        if path == target:
            target_lstat_calls += 1
            if target_lstat_calls == 2:
                temporary = next(tmp_path.glob(".target.json.*.tmp"))
                temporary.unlink()
                temporary.write_bytes(b"substituted")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", replace_temporary_before_publication)

    with pytest.raises(ValueError, match="temporary|identity|changed"):
        atomic_write_bytes(target, b"replacement")

    assert target.read_bytes() == b"original"


def test_atomic_create_is_exclusive_under_concurrent_writers(tmp_path: Path) -> None:
    target = tmp_path / "created.json"
    barrier = threading.Barrier(2)

    def create(payload: bytes) -> tuple[str, bytes]:
        barrier.wait()
        try:
            storage_module.atomic_create_bytes(target, payload)
        except ValueError:
            return "rejected", payload
        return "created", payload

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(
            executor.map(
                create,
                (b"first", b"second"),
            )
        )

    assert sorted(status for status, _payload in results) == ["created", "rejected"]
    created_payload = next(payload for status, payload in results if status == "created")
    assert target.read_bytes() == created_payload
    assert list(tmp_path.glob("*.tmp")) == []
