from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.test_contracts import TASK
from tracelane.contracts import HarnessConfig, canonical_json, load_task, sha256_json
from tracelane.runner import run_task
from tracelane.runtime.stub import DeterministicStubRuntime
from tracelane.v2 import migration as migration_module
from tracelane.v2 import storage as storage_module
from tracelane.v2.contracts import content_digest
from tracelane.v2.migration import MigrationManifest, import_v1_run


class FixedClock:
    def __call__(self) -> datetime:
        return datetime(2026, 7, 24, tzinfo=UTC)


class LaterClock:
    def __call__(self) -> datetime:
        return datetime(2026, 7, 25, tzinfo=UTC)


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def source_root_sha256(root: Path) -> str:
    normalized = os.path.normcase(os.path.normpath(str(root.resolve(strict=True))))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def create_directory_symlink_or_skip(link: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")


def write_v1_run(root: Path) -> Path:
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        DeterministicStubRuntime(),
        root,
        clock=FixedClock(),
    )
    return root / "runs" / result.run_id


def test_v1_import_preserves_bytes_and_does_not_modify_source(tmp_path: Path) -> None:
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        DeterministicStubRuntime(),
        tmp_path / "source",
        clock=FixedClock(),
    )
    source = tmp_path / "source" / "runs" / result.run_id
    before = tree_hashes(source)

    imported = import_v1_run(source, tmp_path / "target", clock=FixedClock())

    assert tree_hashes(source) == before
    assert tree_hashes(imported.payload_dir) == before
    assert imported.manifest.source_format == "tracelane-v1"
    assert imported.manifest.source_run_id == result.run_id
    assert imported.manifest.source_root_sha256 == source_root_sha256(source)
    assert imported.manifest.payload_root_sha256 == sha256_json(imported.manifest.entries)
    assert imported.manifest.import_id == imported.import_dir.name
    assert str(source) not in (imported.import_dir / "manifest.json").read_text(encoding="utf-8")


def test_reimport_is_idempotent(tmp_path: Path) -> None:
    result = run_task(
        load_task(deepcopy(TASK)),
        HarnessConfig(),
        DeterministicStubRuntime(),
        tmp_path / "source",
        clock=FixedClock(),
    )
    source = tmp_path / "source" / "runs" / result.run_id

    first = import_v1_run(source, tmp_path / "target", clock=FixedClock())
    manifest_path = first.import_dir / "manifest.json"
    original_manifest_bytes = manifest_path.read_bytes()
    original_imported_at = first.manifest.imported_at

    second = import_v1_run(source, tmp_path / "target", clock=LaterClock())

    assert first.import_dir == second.import_dir
    assert tree_hashes(first.import_dir) == tree_hashes(second.import_dir)
    assert manifest_path.read_bytes() == original_manifest_bytes
    assert second.manifest.imported_at == original_imported_at


def test_reimport_rejects_missing_payload_root_with_stable_value_error(
    tmp_path: Path,
) -> None:
    source = write_v1_run(tmp_path / "source")
    first = import_v1_run(source, tmp_path / "target", clock=FixedClock())
    manifest_path = first.import_dir / "manifest.json"
    shutil.rmtree(first.payload_dir)
    assert manifest_path.is_file()

    with pytest.raises(ValueError) as error:
        import_v1_run(source, tmp_path / "target", clock=LaterClock())

    assert str(error.value) == "migration tree root is unavailable"
    assert str(first.payload_dir) not in str(error.value)


def test_import_normalizes_descendant_disappearance_during_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    source_file = next(path for path in source.rglob("*") if path.is_file())
    original_assert_safe_tree = migration_module.assert_safe_tree
    source_checks = 0

    def remove_descendant_during_snapshot(root: str | Path) -> None:
        nonlocal source_checks
        original_assert_safe_tree(root)
        if Path(root) == source:
            source_checks += 1
            if source_checks == 2:
                source_file.unlink()
                raise FileNotFoundError(str(source_file))

    monkeypatch.setattr(
        migration_module,
        "assert_safe_tree",
        remove_descendant_during_snapshot,
    )

    with pytest.raises(ValueError) as error:
        import_v1_run(source, target, clock=FixedClock())

    assert str(error.value) == "migration tree changed during snapshot"
    assert str(source_file) not in str(error.value)
    assert not target.exists()


def test_existing_import_rejects_source_changed_while_waiting_for_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    initial = import_v1_run(source, target, clock=FixedClock())
    target_before = tree_hashes(initial.import_dir)
    source_file = next(path for path in source.rglob("*") if path.is_file())
    lock_attempted = threading.Event()
    release_lock = threading.Event()

    @contextmanager
    def delay_lock_entry(_path: Path, *, blocking: bool = False):
        assert blocking
        lock_attempted.set()
        assert release_lock.wait(timeout=10)
        yield

    monkeypatch.setattr(migration_module, "exclusive_file_lock", delay_lock_entry)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(import_v1_run, source, target, clock=LaterClock())
        assert lock_attempted.wait(timeout=10)
        source_file.write_bytes(source_file.read_bytes() + b"changed while waiting")
        release_lock.set()

        with pytest.raises(ValueError, match="source root changed"):
            future.result(timeout=20)

    assert tree_hashes(initial.import_dir) == target_before


def test_existing_import_rejects_directory_link_added_while_waiting_for_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    initial = import_v1_run(source, target, clock=FixedClock())
    target_before = tree_hashes(initial.import_dir)
    lock_attempted = threading.Event()
    release_lock = threading.Event()

    @contextmanager
    def delay_lock_entry(_path: Path, *, blocking: bool = False):
        assert blocking
        lock_attempted.set()
        assert release_lock.wait(timeout=10)
        yield

    monkeypatch.setattr(migration_module, "exclusive_file_lock", delay_lock_entry)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(import_v1_run, source, target, clock=LaterClock())
        assert lock_attempted.wait(timeout=10)
        try:
            create_directory_symlink_or_skip(
                source / "late-linked-directory",
                tmp_path / "outside-waiting",
            )
        finally:
            release_lock.set()

        with pytest.raises(ValueError, match="link|reparse"):
            future.result(timeout=20)

    assert tree_hashes(initial.import_dir) == target_before


def test_new_import_rejects_post_lock_source_mutation_before_creating_import_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    source_file = next(path for path in source.rglob("*") if path.is_file())
    lock_attempted = threading.Event()
    release_lock = threading.Event()

    @contextmanager
    def delay_lock_entry(_path: Path, *, blocking: bool = False):
        assert blocking
        lock_attempted.set()
        assert release_lock.wait(timeout=10)
        yield

    monkeypatch.setattr(migration_module, "exclusive_file_lock", delay_lock_entry)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(import_v1_run, source, target, clock=FixedClock())
        assert lock_attempted.wait(timeout=10)
        source_file.write_bytes(source_file.read_bytes() + b"changed before lock acquisition")
        release_lock.set()

        with pytest.raises(ValueError, match="source root changed"):
            future.result(timeout=20)

    assert not (target / "imports").exists()
    assert not any(target.rglob("payload"))
    assert not any(target.rglob("manifest.json"))


def test_existing_import_rechecks_source_after_target_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    initial = import_v1_run(source, target, clock=FixedClock())
    target_before = tree_hashes(initial.import_dir)
    source_file = next(path for path in source.rglob("*") if path.is_file())
    original_validate = migration_module._validate_existing_import
    mutated = False

    def validate_then_mutate(**kwargs: object):
        nonlocal mutated
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        if not mutated:
            mutated = True
            source_file.write_bytes(source_file.read_bytes() + b"changed after authentication")
        return result

    monkeypatch.setattr(
        migration_module,
        "_validate_existing_import",
        validate_then_mutate,
    )

    with pytest.raises(ValueError, match="source root changed"):
        import_v1_run(source, target, clock=LaterClock())

    assert mutated
    assert tree_hashes(initial.import_dir) == target_before


def test_existing_import_rejects_directory_link_added_after_target_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    initial = import_v1_run(source, target, clock=FixedClock())
    target_before = tree_hashes(initial.import_dir)
    original_validate = migration_module._validate_existing_import
    linked = False

    def validate_then_link(**kwargs: object):
        nonlocal linked
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        if not linked:
            create_directory_symlink_or_skip(
                source / "late-linked-directory",
                tmp_path / "outside-authentication",
            )
            linked = True
        return result

    monkeypatch.setattr(
        migration_module,
        "_validate_existing_import",
        validate_then_link,
    )

    with pytest.raises(ValueError, match="link|reparse"):
        import_v1_run(source, target, clock=LaterClock())

    assert linked
    assert tree_hashes(initial.import_dir) == target_before


def test_identical_source_clones_at_different_roots_do_not_alias(
    tmp_path: Path,
) -> None:
    source = write_v1_run(tmp_path / "source")
    clone = tmp_path / "clone" / "runs" / source.name
    shutil.copytree(source, clone)

    first = import_v1_run(source, tmp_path / "target", clock=FixedClock())
    second = import_v1_run(clone, tmp_path / "target", clock=FixedClock())

    assert first.manifest.entries == second.manifest.entries
    assert first.manifest.source_run_id == second.manifest.source_run_id
    assert first.manifest.source_root_sha256 != second.manifest.source_root_sha256
    assert first.manifest.import_id != second.manifest.import_id
    assert first.import_dir != second.import_dir


def test_two_importers_with_different_clocks_return_same_persisted_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    partial = import_v1_run(source, target, clock=FixedClock())
    (partial.import_dir / "manifest.json").unlink()
    original_exists = Path.exists
    original_atomic_create = storage_module.atomic_create_bytes
    arrival_lock = threading.Lock()
    both_arrived = threading.Event()
    serialized_lock = threading.Lock()
    lock_state = threading.local()
    both_observed_without_lock = threading.Event()
    both_publications_finished = threading.Event()
    unlocked_manifest_checks: dict[int, int] = {}
    arrivals = 0
    publication_finishes = 0

    @contextmanager
    def synchronized_import_lock(_path: Path, *, blocking: bool = False):
        nonlocal arrivals
        assert blocking
        with arrival_lock:
            arrivals += 1
            if arrivals == 2:
                both_arrived.set()
        assert both_arrived.wait(timeout=20)
        with serialized_lock:
            lock_state.held = True
            try:
                yield
            finally:
                lock_state.held = False

    def synchronize_if_import_lock_is_removed(path: Path) -> bool:
        is_import_manifest = (
            path.name == "manifest.json"
            and path.parent.parent.name == "v1"
            and path.parent.parent.parent.name == "imports"
        )
        if not is_import_manifest or getattr(lock_state, "held", False):
            return original_exists(path)
        thread_id = threading.get_ident()
        with arrival_lock:
            check_count = unlocked_manifest_checks.get(thread_id, 0)
            unlocked_manifest_checks[thread_id] = check_count + 1
            if check_count == 0:
                observed = original_exists(path)
                if len(unlocked_manifest_checks) == 2:
                    both_observed_without_lock.set()
        if check_count == 0:
            assert both_observed_without_lock.wait(timeout=20)
            assert not observed
            return observed
        if check_count == 1:
            return False
        return original_exists(path)

    def synchronize_publication_if_import_lock_is_removed(
        path: Path,
        data: bytes,
        **kwargs: object,
    ) -> None:
        nonlocal publication_finishes
        if kwargs.get("label") != "migration manifest" or getattr(lock_state, "held", False):
            original_atomic_create(path, data, **kwargs)  # type: ignore[arg-type]
            return
        try:
            original_atomic_create(path, data, **kwargs)  # type: ignore[arg-type]
        finally:
            with arrival_lock:
                publication_finishes += 1
                if publication_finishes == 2:
                    both_publications_finished.set()
            assert both_publications_finished.wait(timeout=20)

    monkeypatch.setattr(
        migration_module,
        "exclusive_file_lock",
        synchronized_import_lock,
    )
    monkeypatch.setattr(Path, "exists", synchronize_if_import_lock_is_removed)
    monkeypatch.setattr(
        storage_module,
        "atomic_create_bytes",
        synchronize_publication_if_import_lock_is_removed,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(import_v1_run, source, target, clock=FixedClock())
        second_future = executor.submit(import_v1_run, source, target, clock=LaterClock())
        first = first_future.result(timeout=20)
        second = second_future.result(timeout=20)

    persisted = (first.import_dir / "manifest.json").read_bytes()
    assert first.manifest == second.manifest
    assert canonical_json(first.manifest.to_dict()).encode("utf-8") + b"\n" == persisted


def test_different_clock_concurrency_regression_detects_removed_import_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    partial = import_v1_run(source, target, clock=FixedClock())
    (partial.import_dir / "manifest.json").unlink()
    original_exists = Path.exists
    original_atomic_create = storage_module.atomic_create_bytes
    arrival_lock = threading.Lock()
    both_observed_manifest = threading.Event()
    both_publications_finished = threading.Event()
    manifest_checks: dict[int, int] = {}
    publication_finishes = 0

    @contextmanager
    def removed_import_lock(_path: Path, *, blocking: bool = False):
        assert blocking
        yield

    def synchronize_manifest_existence(path: Path) -> bool:
        is_import_manifest = (
            path.name == "manifest.json"
            and path.parent.parent.name == "v1"
            and path.parent.parent.parent.name == "imports"
        )
        if not is_import_manifest:
            return original_exists(path)
        thread_id = threading.get_ident()
        with arrival_lock:
            check_count = manifest_checks.get(thread_id, 0)
            manifest_checks[thread_id] = check_count + 1
            if check_count == 0:
                observed = original_exists(path)
                if len(manifest_checks) == 2:
                    both_observed_manifest.set()
        if check_count == 0:
            assert both_observed_manifest.wait(timeout=20)
            assert not observed
            return observed
        if check_count == 1:
            return False
        return original_exists(path)

    def synchronize_manifest_publications(
        path: Path,
        data: bytes,
        **kwargs: object,
    ) -> None:
        nonlocal publication_finishes
        if kwargs.get("label") != "migration manifest":
            original_atomic_create(path, data, **kwargs)  # type: ignore[arg-type]
            return
        try:
            original_atomic_create(path, data, **kwargs)  # type: ignore[arg-type]
        finally:
            with arrival_lock:
                publication_finishes += 1
                if publication_finishes == 2:
                    both_publications_finished.set()
            assert both_publications_finished.wait(timeout=20)

    monkeypatch.setattr(migration_module, "exclusive_file_lock", removed_import_lock)
    monkeypatch.setattr(Path, "exists", synchronize_manifest_existence)
    monkeypatch.setattr(
        storage_module,
        "atomic_create_bytes",
        synchronize_manifest_publications,
    )

    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(import_v1_run, source, target, clock=FixedClock()),
            executor.submit(import_v1_run, source, target, clock=LaterClock()),
        )
        for future in futures:
            try:
                results.append(future.result(timeout=20))
            except ValueError as exc:
                errors.append(exc)

    assert len(results) == 1
    assert len(errors) == 1
    assert str(errors[0]) == "existing migration manifest does not match requested import"
    assert publication_finishes == 2
    assert sorted(manifest_checks.values()) == [2, 3]
    assert not any(path.name.endswith(".tmp") for path in partial.import_dir.iterdir())


def test_source_mutation_during_semantic_inspection_rejects_before_target_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    original_inspect = migration_module.inspect_run

    def mutate_after_inspection(path: Path) -> None:
        original_inspect(path)
        source_file = next(item for item in path.rglob("*") if item.is_file())
        source_file.write_bytes(source_file.read_bytes() + b"mutated")

    monkeypatch.setattr(migration_module, "inspect_run", mutate_after_inspection)

    with pytest.raises(ValueError, match="source root changed"):
        import_v1_run(source, target, clock=FixedClock())

    assert not target.exists()


def test_payload_create_race_rejects_unrelated_bytes_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    original_create = storage_module.atomic_create_bytes
    raced_path: Path | None = None

    def race_payload(path: Path, data: bytes, **kwargs: object) -> None:
        nonlocal raced_path
        if kwargs.get("label") == "migration payload file" and raced_path is None:
            raced_path = path
            original_create(path, b"unrelated", **kwargs)  # type: ignore[arg-type]
        original_create(path, data, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(storage_module, "atomic_create_bytes", race_payload)

    with pytest.raises(ValueError, match="payload.*source|payload.*match"):
        import_v1_run(source, target, clock=FixedClock())

    assert raced_path is not None
    assert raced_path.read_bytes() == b"unrelated"
    assert not any(target.rglob("manifest.json"))


def test_manifest_create_race_authenticates_identical_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_v1_run(tmp_path / "source")
    target = tmp_path / "target"
    original_create = storage_module.atomic_create_bytes
    marker_raced = False

    def race_marker(path: Path, data: bytes, **kwargs: object) -> None:
        nonlocal marker_raced
        if kwargs.get("label") == "migration manifest" and not marker_raced:
            marker_raced = True
            original_create(path, data, **kwargs)  # type: ignore[arg-type]
            raise ValueError("migration manifest already exists")
        original_create(path, data, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(storage_module, "atomic_create_bytes", race_marker)

    result = import_v1_run(source, target, clock=FixedClock())

    assert marker_raced
    assert result == migration_module._validate_existing_import(
        import_dir=result.import_dir,
        payload_dir=result.payload_dir,
        expected_import_id=result.manifest.import_id,
        expected_source_run_id=result.manifest.source_run_id,
        expected_source_root_sha256=result.manifest.source_root_sha256,
        expected_entries=result.manifest.entries,
    )


def test_existing_import_must_match_requested_source_identity(tmp_path: Path) -> None:
    source = write_v1_run(tmp_path / "source")
    result = import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())
    manifest_path = result.import_dir / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["source_run_id"] = "a" * 64
    value["content_sha256"] = content_digest(value)
    manifest_path.write_text(canonical_json(value) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source run"):
        import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("import_id", "0" * 24, "identity"),
        ("source_root_sha256", "f" * 64, "source root"),
        ("payload_root_sha256", "f" * 64, "payload root"),
    ],
)
def test_existing_import_rejects_manifest_substitution(
    tmp_path: Path,
    field: str,
    replacement: object,
    message: str,
) -> None:
    source = write_v1_run(tmp_path / "source")
    result = import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())
    manifest_path = result.import_dir / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value[field] = replacement
    value["content_sha256"] = content_digest(value)
    manifest_path.write_text(canonical_json(value) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())


def test_existing_import_rejects_self_consistent_entries_substitution(tmp_path: Path) -> None:
    source = write_v1_run(tmp_path / "source")
    result = import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())
    manifest_path = result.import_dir / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_entries = tuple(
        {**entry, "sha256": "f" * 64} if index == 0 else entry
        for index, entry in enumerate(value["entries"])
    )
    value["entries"] = changed_entries
    value["source_root_sha256"] = sha256_json(changed_entries)
    value["payload_root_sha256"] = sha256_json(changed_entries)
    value["content_sha256"] = content_digest(value)
    manifest_path.write_text(canonical_json(value) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="existing v1 import entries do not match source"):
        import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())


def test_migration_manifest_rejects_unsafe_normalized_entry_path(tmp_path: Path) -> None:
    source = write_v1_run(tmp_path / "source")
    result = import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())
    value = result.manifest.to_dict()
    entries = list(value["entries"])
    entries[0] = {**entries[0], "path": "input/../escape.json"}
    value["entries"] = entries
    value["source_root_sha256"] = sha256_json(entries)
    value["payload_root_sha256"] = sha256_json(entries)
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match="path"):
        MigrationManifest.from_dict(value)


def test_import_rejects_target_nested_inside_source_before_mutation(tmp_path: Path) -> None:
    source = write_v1_run(tmp_path / "source")
    target = source / "nested-target"
    before = tree_hashes(source)

    with pytest.raises(ValueError, match="overlap|inside"):
        import_v1_run(source, target, clock=FixedClock())

    assert tree_hashes(source) == before
    assert not target.exists()


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("extra", "v1 import bytes"),
        ("modified", "partial v1 import payload"),
    ],
)
def test_partial_import_rejects_payload_mutation_without_recreating_manifest(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    source = write_v1_run(tmp_path / "source")
    result = import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())
    manifest_path = result.import_dir / "manifest.json"
    manifest_path.unlink()

    if mutation == "extra":
        (result.payload_dir / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    else:
        existing_file = next(path for path in result.payload_dir.rglob("*") if path.is_file())
        existing_file.write_bytes(b"modified")

    with pytest.raises(ValueError, match=message):
        import_v1_run(source, tmp_path / "artifacts", clock=FixedClock())

    assert not manifest_path.exists()
