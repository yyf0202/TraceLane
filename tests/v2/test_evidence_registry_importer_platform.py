from pathlib import Path

import pytest

from tracelane.evidence_registry import importer as evidence_importer


def test_non_windows_import_fails_before_target_or_staging_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "missing-source"
    target = tmp_path / "evidence"
    parent_metadata = tmp_path.lstat()
    before_entries = tuple(tmp_path.iterdir())

    def fail_if_lock_reached(*args: object, **kwargs: object):
        pytest.fail(f"mutation lock reached: {args!r} {kwargs!r}")

    monkeypatch.setattr(
        evidence_importer,
        "_IMPORT_PLATFORM",
        "posix",
    )
    monkeypatch.setattr(
        evidence_importer,
        "evidence_root_mutation_lock",
        fail_if_lock_reached,
    )

    with pytest.raises(
        ValueError,
        match="^evidence import is unavailable on this platform$",
    ):
        evidence_importer.import_acquisition_project(
            source,
            target,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )

    assert not source.exists()
    assert not target.exists()
    assert not (target.parent / ".tracelane-staging").exists()
    assert not (target.parent / ".tracelane-locks").exists()
    assert tuple(tmp_path.iterdir()) == before_entries
    after_metadata = tmp_path.lstat()
    assert (after_metadata.st_dev, after_metadata.st_ino) == (
        parent_metadata.st_dev,
        parent_metadata.st_ino,
    )
