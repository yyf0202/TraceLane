from pathlib import Path

import pytest

from tracelane.evidence_registry import importer as evidence_importer


def test_non_windows_import_fails_before_target_or_staging_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "missing-source"
    target = tmp_path / "evidence"
    monkeypatch.setattr(
        evidence_importer,
        "_IMPORT_PLATFORM",
        "posix",
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
