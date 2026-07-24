from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tracelane.artifacts import RunStore
from tracelane.contracts import canonical_json, sha256_json
from tracelane.history.contracts import compute_history_bundle_sha256
from tracelane.v2 import manifests as manifest_module
from tracelane.v2 import storage as storage_module
from tracelane.v2.contracts import ArtifactRef, content_digest
from tracelane.v2.manifests import (
    ExecutionFingerprint,
    RunManifest,
    artifact_ref_for_file,
    validate_run,
    write_checksums,
)
from tracelane.v2.schema import SchemaValidationError, validate_document
from tracelane.v2.tracing import TraceRecorderV2

NOW = datetime(2026, 7, 24, tzinfo=UTC)
RUN_CORRUPTIONS = (
    "fingerprint_substitution",
    "missing_checksum_file",
    "extra_unlisted_file",
    "duplicate_checksum_uri",
    "wrong_artifact_kind",
    "wrong_artifact_schema",
    "wrong_artifact_size",
    "wrong_artifact_digest",
    "escaped_artifact_uri",
)
RUN_CORRUPTION_ERRORS: dict[str, type[ValueError]] = {
    corruption: ValueError for corruption in RUN_CORRUPTIONS
}
for _schema_corruption in (
    "wrong_artifact_kind",
    "wrong_artifact_schema",
    "escaped_artifact_uri",
):
    RUN_CORRUPTION_ERRORS[_schema_corruption] = SchemaValidationError


def artifact_ref_value(
    *,
    kind: str,
    uri: str,
    schema_id: str,
) -> dict[str, object]:
    return {
        "kind": kind,
        "uri": uri,
        "media_type": "application/json",
        "sha256": "a" * 64,
        "size_bytes": 1,
        "schema_id": schema_id,
    }


def object_envelope(schema_name: str, object_id: str) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_id": f"tracelane://schemas/{schema_name}/v2",
        "schema_version": "2.0.0",
        "object_id": object_id,
        "created_at": "2026-07-24T00:00:00Z",
        "content_sha256": "",
    }
    value["content_sha256"] = content_digest(value)
    return value


EVIDENCE_RECORD_REF = ArtifactRef.from_dict(
    artifact_ref_value(
        kind="evidence_record",
        uri="tracelane://fixtures/v0.2/history/hist-001/evidence/records/record.json",
        schema_id="tracelane://schemas/evidence-record/v2",
    )
)
EVIDENCE_MANIFEST_LINK = artifact_ref_value(
    kind="evidence_manifest",
    uri="tracelane://fixtures/v0.2/history/hist-001/evidence/manifest.json",
    schema_id="tracelane://schemas/evidence-manifest/v2",
)
EVIDENCE_MANIFEST_VALUE: dict[str, object] = {
    "schema_id": "tracelane://schemas/evidence-manifest/v2",
    "schema_version": "2.0.0",
    "content_sha256": "",
    "case_id": "hist-001",
    "cutoff_at": "1812-06-23T23:59:59Z",
    "record_refs": [EVIDENCE_RECORD_REF.to_dict()],
    "rejected_future_refs": [],
    "source_licenses": {"hist-001-ev-0001": "Public-Domain"},
    "transformation_refs": [],
    "bundle_sha256": compute_history_bundle_sha256(
        case_id="hist-001",
        cutoff_at=datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC),
        record_refs=(EVIDENCE_RECORD_REF,),
        rejected_future_refs=(),
        transformation_refs=(),
        source_licenses={"hist-001-ev-0001": "Public-Domain"},
    ),
}
EVIDENCE_MANIFEST_VALUE["content_sha256"] = content_digest(EVIDENCE_MANIFEST_VALUE)
_EVIDENCE_MANIFEST_BYTES = (canonical_json(EVIDENCE_MANIFEST_VALUE) + "\n").encode()
EVIDENCE_MANIFEST_LINK["sha256"] = hashlib.sha256(_EVIDENCE_MANIFEST_BYTES).hexdigest()
EVIDENCE_MANIFEST_LINK["size_bytes"] = len(_EVIDENCE_MANIFEST_BYTES)
CASE_VALUE: dict[str, object] = {
    "schema_id": "tracelane://schemas/case/v2",
    "schema_version": "2.0.0",
    "content_sha256": "",
    "case_id": "hist-001",
    "title": "Historical test case",
    "decision_maker": "Test decision maker",
    "cutoff_at": "1812-06-23T23:59:59Z",
    "intervention": "Choose a strategy",
    "projection_end": "1812-12",
    "minimum_alternatives": 2,
    "minimum_scenario_branches": 1,
    "required_domains": ["diplomacy"],
    "evidence_manifest_ref": EVIDENCE_MANIFEST_LINK,
    "rubric_refs": [],
}
CASE_VALUE["content_sha256"] = content_digest(CASE_VALUE)

COMPONENTS = {
    "case_ref": (
        "input/case.json",
        CASE_VALUE,
        "case",
        "tracelane://schemas/case/v2",
        "case_sha256",
    ),
    "evidence_manifest_ref": (
        "input/evidence-manifest.json",
        EVIDENCE_MANIFEST_VALUE,
        "evidence_manifest",
        "tracelane://schemas/evidence-manifest/v2",
        "evidence_manifest_sha256",
    ),
    "harness_config_ref": (
        "input/harness-config.json",
        object_envelope("harness-config", "harness_config_baseline"),
        "harness_config",
        "tracelane://schemas/object-envelope/v2",
        "harness_config_sha256",
    ),
    "runtime_config_ref": (
        "input/runtime-config.json",
        object_envelope("runtime-config", "runtime_config_offline-test"),
        "runtime_config",
        "tracelane://schemas/object-envelope/v2",
        "runtime_config_sha256",
    ),
    "grader_set_ref": (
        "input/grader-set.json",
        object_envelope("grader-set", "grader_set_groundedness"),
        "grader_set",
        "tracelane://schemas/object-envelope/v2",
        "grader_set_sha256",
    ),
}


def json_bytes(value: object) -> bytes:
    return (canonical_json(value) + "\n").encode()


def fingerprint(
    component_values: Mapping[str, object] | None = None,
) -> ExecutionFingerprint:
    values = component_values or {slot: value for slot, (_, value, _, _, _) in COMPONENTS.items()}
    digests = {
        COMPONENTS[slot][4]: hashlib.sha256(json_bytes(value)).hexdigest()
        for slot, value in values.items()
    }
    return ExecutionFingerprint(
        **digests,
        repeat=1,
        code_revision="a" * 40,
    )


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(value))
    return path


def manifest_value(run_dir: Path) -> dict[str, object]:
    value = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def rewrite_manifest(run_dir: Path, mutate: Callable[[dict[str, object]], None]) -> None:
    value = manifest_value(run_dir)
    mutate(value)
    value["content_sha256"] = content_digest(value)
    write_json(run_dir / "manifest.json", value)


def rewrite_checksums(
    run_dir: Path,
    mutate: Callable[[list[dict[str, object]]], None],
) -> None:
    checksums_path = run_dir / "checksums.json"
    value = json.loads(checksums_path.read_text(encoding="utf-8"))
    entries = value["entries"]
    assert isinstance(entries, list)
    mutate(entries)
    value["root_sha256"] = sha256_json(entries)
    value["content_sha256"] = content_digest(value)
    write_json(checksums_path, value)

    checksums_ref = artifact_ref_for_file(
        run_dir.parents[1],
        checksums_path,
        "checksums",
        "tracelane://schemas/checksums/v2",
    )
    rewrite_manifest(
        run_dir,
        lambda manifest: manifest.__setitem__("checksums_ref", checksums_ref.to_dict()),
    )


def write_minimal_run(
    root: Path,
    *,
    include_all_reference_slots: bool = False,
    case_evidence_ref_overrides: Mapping[str, object] | None = None,
    bypass_finalization_validation: bool = False,
) -> Path:
    component_values = {slot: deepcopy(value) for slot, (_, value, _, _, _) in COMPONENTS.items()}
    if case_evidence_ref_overrides:
        case_value = component_values["case_ref"]
        assert isinstance(case_value, dict)
        evidence_ref = case_value["evidence_manifest_ref"]
        assert isinstance(evidence_ref, dict)
        evidence_ref.update(case_evidence_ref_overrides)
        case_value["content_sha256"] = content_digest(case_value)
    execution_fingerprint = fingerprint(component_values)
    run_dir = root / "runs" / execution_fingerprint.run_id
    component_refs = {}
    for slot, (relative, _, kind, schema_id, _) in COMPONENTS.items():
        value = component_values[slot]
        path = write_json(run_dir / relative, value)
        component_refs[slot] = artifact_ref_for_file(root, path, kind, schema_id)

    recorder = TraceRecorderV2(
        RunStore.create(root, execution_fingerprint.run_id), clock=lambda: NOW
    )
    started = recorder.emit("run.started", {"status": "running"})
    if not include_all_reference_slots:
        recorder.emit(
            "run.completed",
            {"status": "completed"},
            causation_id=started.event_id,
            parent_span_id=started.span_id,
        )
    trace_path = run_dir / "trace" / "events.jsonl"
    grade_report_path = write_json(
        run_dir / "output" / "grade-report.json",
        object_envelope("grade-report", "grade_report_result"),
    )
    output_path = write_json(
        run_dir / "output" / "answer.json",
        object_envelope("output", "output_answer"),
    )
    trace_ref = artifact_ref_for_file(
        root,
        trace_path,
        "trace",
        "tracelane://schemas/trace-event/v2",
    )
    grade_report_ref = artifact_ref_for_file(
        root,
        grade_report_path,
        "grade_report",
        "tracelane://schemas/object-envelope/v2",
    )
    checkpoint_refs = ()
    diagnosis_ref = None
    output_refs = (
        artifact_ref_for_file(
            root,
            output_path,
            "output",
            "tracelane://schemas/object-envelope/v2",
        ),
    )
    failure_ref = None
    lifecycle_status = "completed"
    if include_all_reference_slots:
        checkpoint_path = write_json(
            run_dir / "checkpoint" / "state.json",
            object_envelope("checkpoint", "checkpoint_state"),
        )
        diagnosis_path = write_json(
            run_dir / "output" / "diagnosis.json",
            object_envelope("diagnosis", "diagnosis_result"),
        )
        failure_path = write_json(
            run_dir / "output" / "failure.json",
            object_envelope("failure-record", "failure_record_expected"),
        )
        checkpoint_refs = (
            artifact_ref_for_file(
                root,
                checkpoint_path,
                "checkpoint",
                "tracelane://schemas/object-envelope/v2",
            ),
        )
        diagnosis_ref = artifact_ref_for_file(
            root,
            diagnosis_path,
            "diagnosis",
            "tracelane://schemas/object-envelope/v2",
        )
        failure_ref = artifact_ref_for_file(
            root,
            failure_path,
            "failure_record",
            "tracelane://schemas/object-envelope/v2",
        )
        lifecycle_status = "failed"

    checksums_ref = write_checksums(run_dir)
    manifest = RunManifest.create(
        run_id=execution_fingerprint.run_id,
        lifecycle_status=lifecycle_status,
        started_at=NOW,
        completed_at=NOW,
        execution_fingerprint=execution_fingerprint,
        **component_refs,
        environment_fingerprint="python-3.12-windows",
        semantic_convention_version="1.37.0",
        redaction_policy_id="default-v1",
        trace_ref=trace_ref,
        checkpoint_refs=checkpoint_refs,
        diagnosis_ref=diagnosis_ref,
        output_refs=output_refs,
        grade_report_ref=grade_report_ref,
        failure_ref=failure_ref,
        parent_run_id=None,
        branch_id=None,
        checksums_ref=checksums_ref,
    )
    if bypass_finalization_validation:
        write_json(run_dir / "manifest.json", manifest.to_dict())
    else:
        manifest_module.write_run_manifest(run_dir, manifest)
    return run_dir


def test_run_id_changes_for_code_grader_runtime_or_repeat() -> None:
    base = fingerprint()

    assert base.run_id == fingerprint().run_id
    assert base.run_id != replace(base, repeat=2).run_id
    assert base.run_id != replace(base, code_revision="b" * 40).run_id
    assert base.run_id != replace(base, grader_set_sha256="f" * 64).run_id
    assert base.run_id != replace(base, runtime_config_sha256="0" * 64).run_id


def test_execution_fingerprint_round_trips_through_canonical_dict() -> None:
    value = fingerprint().to_dict()

    assert ExecutionFingerprint.from_dict(value) == fingerprint()
    assert list(value) == sorted(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("case_sha256", int("1" * 64)),
        ("code_revision", 123),
        ("repeat", True),
        ("repeat", "1"),
    ],
)
def test_execution_fingerprint_from_dict_rejects_coercible_types(
    field: str,
    replacement: object,
) -> None:
    value = fingerprint().to_dict()
    value[field] = replacement

    with pytest.raises(ValueError, match="fingerprint"):
        ExecutionFingerprint.from_dict(value)


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_execution_fingerprint_from_dict_requires_exact_keys(mutation: str) -> None:
    value = fingerprint().to_dict()
    if mutation == "missing":
        value.pop("case_sha256")
    else:
        value["unexpected"] = "not-allowed"

    with pytest.raises(ValueError, match="fingerprint fields"):
        ExecutionFingerprint.from_dict(value)


def test_run_manifest_create_validates_schema_before_returning(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    existing = RunManifest.from_dict(manifest_value(run_dir))
    values = {
        field.name: getattr(existing, field.name)
        for field in fields(RunManifest)
        if field.name not in {"schema_id", "schema_version", "content_sha256"}
    }
    values["environment_fingerprint"] = ""

    with pytest.raises(ValueError, match="environment_fingerprint"):
        RunManifest.create(**values)


def test_manifest_persists_full_execution_fingerprint(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    value = manifest_value(run_dir)

    assert value["execution_fingerprint"] == fingerprint().to_dict()
    assert value["run_id"] == fingerprint().run_id
    assert "code_revision" not in value


def test_checksums_exclude_manifest_and_themselves(tmp_path: Path) -> None:
    execution_fingerprint = fingerprint()
    run_dir = tmp_path / "runs" / execution_fingerprint.run_id
    write_json(run_dir / "input/case.json", {"case_id": "hist-001"})
    write_json(run_dir / "manifest.json", {"status": "draft"})

    reference = write_checksums(run_dir)

    assert reference.uri.endswith("/checksums.json")
    checksums = json.loads((run_dir / "checksums.json").read_text(encoding="utf-8"))
    assert len(checksums["entries"]) == 1


def test_write_checksums_rejects_symlink_run_root(tmp_path: Path) -> None:
    run_id = fingerprint().run_id
    real_run_dir = tmp_path / "real" / "runs" / run_id
    write_json(real_run_dir / "input" / "case.json", {"case_id": "hist-001"})
    linked_run_dir = tmp_path / "linked" / "runs" / run_id
    linked_run_dir.parent.mkdir(parents=True)
    try:
        linked_run_dir.symlink_to(real_run_dir, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="root.*link|root.*reparse"):
        write_checksums(linked_run_dir)


def test_write_checksums_rejects_symlink_descendant(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / fingerprint().run_id
    run_dir.mkdir(parents=True)
    outside = write_json(tmp_path / "outside.json", {"outside": True})
    link = run_dir / "input.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="link|reparse"):
        write_checksums(run_dir)


def test_write_checksums_rejects_two_paths_for_same_hard_link(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / fingerprint().run_id
    original = write_json(run_dir / "input" / "case.json", {"case_id": "hist-001"})
    duplicate = run_dir / "input" / "case-copy.json"
    try:
        os.link(original, duplicate)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="file identity|hard link"):
        write_checksums(run_dir)


def test_write_checksums_refuses_second_finalization_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / fingerprint().run_id
    write_json(run_dir / "input" / "case.json", {"case_id": "hist-001"})
    write_checksums(run_dir)
    checksums_path = run_dir / "checksums.json"
    original = checksums_path.read_bytes()

    with pytest.raises(ValueError, match="already.*finalized|already exists"):
        write_checksums(run_dir)

    assert checksums_path.read_bytes() == original


def test_write_checksums_uses_hardened_create_new(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "runs" / fingerprint().run_id
    write_json(run_dir / "input" / "case.json", {"case_id": "hist-001"})
    racing_file = tmp_path / "racing-checksums.json"
    racing_file.write_bytes(b"racing checksums")
    original_create = storage_module.atomic_create_bytes
    hardened_create_reached = False

    def create_after_identity_change(
        path: Path,
        data: bytes,
        *,
        root: str | Path | None = None,
        label: str = "file",
    ) -> None:
        nonlocal hardened_create_reached
        hardened_create_reached = True
        try:
            os.link(racing_file, path)
        except OSError:
            pytest.skip("hard links are unavailable on this host")
        original_create(path, data, root=root, label=label)

    monkeypatch.setattr(storage_module, "atomic_create_bytes", create_after_identity_change)
    reference = None
    with pytest.raises(ValueError, match="already.*finalized|already exists|multiple links"):
        reference = write_checksums(run_dir)

    assert hardened_create_reached
    assert reference is None
    assert (run_dir / "checksums.json").read_bytes() == b"racing checksums"


def test_write_run_manifest_refuses_second_finalization_and_preserves_bytes(
    tmp_path: Path,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = RunManifest.from_dict(manifest_value(run_dir))
    manifest_path.unlink()

    written = manifest_module.write_run_manifest(run_dir, manifest)
    original = written.read_bytes()
    with pytest.raises(ValueError, match="already.*finalized|already exists"):
        manifest_module.write_run_manifest(run_dir, manifest)

    assert written == manifest_path
    assert manifest_path.read_bytes() == original


def test_write_run_manifest_uses_hardened_create_new(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = RunManifest.from_dict(manifest_value(run_dir))
    manifest_path.unlink()
    racing_file = tmp_path / "racing-manifest.json"
    racing_file.write_bytes(b"racing manifest")
    original_create = storage_module.atomic_create_bytes
    hardened_create_reached = False

    def create_after_identity_change(
        path: Path,
        data: bytes,
        *,
        root: str | Path | None = None,
        label: str = "file",
    ) -> None:
        nonlocal hardened_create_reached
        hardened_create_reached = True
        try:
            os.link(racing_file, path)
        except OSError:
            pytest.skip("hard links are unavailable on this host")
        original_create(path, data, root=root, label=label)

    monkeypatch.setattr(storage_module, "atomic_create_bytes", create_after_identity_change)
    written = None
    with pytest.raises(ValueError, match="already.*finalized|already exists|multiple links"):
        written = manifest_module.write_run_manifest(run_dir, manifest)

    assert hardened_create_reached
    assert written is None
    assert manifest_path.read_bytes() == b"racing manifest"


def test_write_run_manifest_rejects_invalid_manifest_without_publishing(
    tmp_path: Path,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    manifest = RunManifest.from_dict(manifest_value(run_dir))
    manifest_path.unlink()
    invalid = replace(manifest, environment_fingerprint="")

    with pytest.raises(ValueError):
        manifest_module.write_run_manifest(run_dir, invalid)

    assert not manifest_path.exists()


@pytest.mark.parametrize("lifecycle_status", ["created", "running"])
def test_write_run_manifest_publishes_only_terminal_lifecycle_once(
    tmp_path: Path,
    lifecycle_status: str,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    terminal = RunManifest.from_dict(manifest_value(run_dir))
    manifest_path.unlink()
    values = {
        field.name: getattr(terminal, field.name)
        for field in fields(RunManifest)
        if field.name not in {"schema_id", "schema_version", "content_sha256"}
    }
    values["lifecycle_status"] = lifecycle_status
    values["completed_at"] = None
    nonterminal = RunManifest.create(**values)

    with pytest.raises(ValueError, match="terminal.*manifest|completed or failed"):
        manifest_module.write_run_manifest(run_dir, nonterminal)
    assert not manifest_path.exists()

    written = manifest_module.write_run_manifest(run_dir, terminal)
    original = written.read_bytes()
    with pytest.raises(ValueError, match="already.*finalized|already exists"):
        manifest_module.write_run_manifest(run_dir, terminal)
    assert written == manifest_path
    assert manifest_path.read_bytes() == original


def test_validate_run_accepts_intact_manifest_and_checksums(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)

    validate_run(run_dir)

    manifest = manifest_value(run_dir)
    assert len(manifest["content_sha256"]) == 64
    checksums = json.loads((run_dir / "checksums.json").read_text(encoding="utf-8"))
    assert len(checksums["entries"]) == 8


def test_validate_run_detects_deleted_final_trace_event(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    trace_path = run_dir / "trace" / "events.jsonl"
    lines = trace_path.read_bytes().splitlines(keepends=True)
    assert len(lines) == 2
    trace_path.write_bytes(b"".join(lines[:-1]))

    with pytest.raises(ValueError, match="checksum|trace.*digest|trace.*size"):
        validate_run(run_dir)


def test_validate_run_parses_the_same_checksums_bytes_it_authenticates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    checksums_path = run_dir / "checksums.json"
    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == checksums_path:
            raise AssertionError("checksums.json was read a second time")
        return original_read_text(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", guarded_read_text)

    validate_run(run_dir)


def reanchor_authoritative_file(
    run_dir: Path,
    *,
    relative_path: str,
    data: bytes,
    reference_slot: str,
    kind: str,
    schema_id: str,
) -> None:
    path = run_dir / relative_path
    path.write_bytes(data)
    uri = f"tracelane://artifacts/runs/{run_dir.name}/{relative_path}"

    def update_entry(entries: list[dict[str, object]]) -> None:
        entry = next(item for item in entries if item["uri"] == uri)
        entry["size_bytes"] = len(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()

    rewrite_checksums(run_dir, update_entry)
    reference = artifact_ref_for_file(
        run_dir.parents[1],
        path,
        kind,
        schema_id,
    )
    rewrite_manifest(
        run_dir,
        lambda value: value.__setitem__(reference_slot, reference.to_dict()),
    )


def test_validate_run_semantically_rejects_reanchored_invalid_trace(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    reanchor_authoritative_file(
        run_dir,
        relative_path="trace/events.jsonl",
        data=b"{}\n",
        reference_slot="trace_ref",
        kind="trace",
        schema_id="tracelane://schemas/trace-event/v2",
    )

    with pytest.raises(ValueError, match="trace|schema"):
        validate_run(run_dir)


def test_validate_run_rejects_reanchored_empty_terminal_trace(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    reanchor_authoritative_file(
        run_dir,
        relative_path="trace/events.jsonl",
        data=b"",
        reference_slot="trace_ref",
        kind="trace",
        schema_id="tracelane://schemas/trace-event/v2",
    )

    with pytest.raises(ValueError, match="trace.*non-empty|run.started"):
        validate_run(run_dir)


def test_validate_run_rejects_completed_trace_without_run_started(
    tmp_path: Path,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    alternate_root = tmp_path / "alternate"
    recorder = TraceRecorderV2(
        RunStore.create(alternate_root, run_dir.name),
        clock=lambda: NOW,
    )
    recorder.emit("run.completed", {"status": "completed"})
    completed_only = (
        alternate_root / "runs" / run_dir.name / "trace" / "events.jsonl"
    ).read_bytes()
    reanchor_authoritative_file(
        run_dir,
        relative_path="trace/events.jsonl",
        data=completed_only,
        reference_slot="trace_ref",
        kind="trace",
        schema_id="tracelane://schemas/trace-event/v2",
    )

    with pytest.raises(ValueError, match="run.started"):
        validate_run(run_dir)


def test_validate_run_rejects_completed_trace_without_terminal_event(
    tmp_path: Path,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    trace_path = run_dir / "trace" / "events.jsonl"
    started_only = trace_path.read_bytes().splitlines(keepends=True)[0]
    reanchor_authoritative_file(
        run_dir,
        relative_path="trace/events.jsonl",
        data=started_only,
        reference_slot="trace_ref",
        kind="trace",
        schema_id="tracelane://schemas/trace-event/v2",
    )

    with pytest.raises(ValueError, match="run.completed|terminal"):
        validate_run(run_dir)


def test_failed_run_rejects_run_completed_event(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path, include_all_reference_slots=True)
    alternate_root = tmp_path / "alternate"
    recorder = TraceRecorderV2(
        RunStore.create(alternate_root, run_dir.name),
        clock=lambda: NOW,
    )
    started = recorder.emit("run.started", {"status": "running"})
    recorder.emit(
        "run.completed",
        {"status": "completed"},
        causation_id=started.event_id,
        parent_span_id=started.span_id,
    )
    trace_bytes = (alternate_root / "runs" / run_dir.name / "trace" / "events.jsonl").read_bytes()
    reanchor_authoritative_file(
        run_dir,
        relative_path="trace/events.jsonl",
        data=trace_bytes,
        reference_slot="trace_ref",
        kind="trace",
        schema_id="tracelane://schemas/trace-event/v2",
    )

    with pytest.raises(ValueError, match="run trace"):
        validate_run(run_dir)


def test_completed_run_rejects_duplicate_run_started(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    alternate_root = tmp_path / "alternate"
    recorder = TraceRecorderV2(
        RunStore.create(alternate_root, run_dir.name),
        clock=lambda: NOW,
    )
    first_started = recorder.emit("run.started", {"status": "running"})
    recorder.emit("run.started", {"status": "running"})
    recorder.emit(
        "run.completed",
        {"status": "completed"},
        causation_id=first_started.event_id,
        parent_span_id=first_started.span_id,
    )
    trace_bytes = (alternate_root / "runs" / run_dir.name / "trace" / "events.jsonl").read_bytes()
    reanchor_authoritative_file(
        run_dir,
        relative_path="trace/events.jsonl",
        data=trace_bytes,
        reference_slot="trace_ref",
        kind="trace",
        schema_id="tracelane://schemas/trace-event/v2",
    )

    with pytest.raises(ValueError, match="run trace"):
        validate_run(run_dir)


def test_completed_run_rejects_duplicate_run_completed(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    alternate_root = tmp_path / "alternate"
    recorder = TraceRecorderV2(
        RunStore.create(alternate_root, run_dir.name),
        clock=lambda: NOW,
    )
    started = recorder.emit("run.started", {"status": "running"})
    recorder.emit(
        "run.completed",
        {"status": "completed"},
        causation_id=started.event_id,
        parent_span_id=started.span_id,
    )
    recorder.emit(
        "run.completed",
        {"status": "completed"},
        causation_id=started.event_id,
        parent_span_id=started.span_id,
    )
    trace_bytes = (alternate_root / "runs" / run_dir.name / "trace" / "events.jsonl").read_bytes()
    reanchor_authoritative_file(
        run_dir,
        relative_path="trace/events.jsonl",
        data=trace_bytes,
        reference_slot="trace_ref",
        kind="trace",
        schema_id="tracelane://schemas/trace-event/v2",
    )

    with pytest.raises(ValueError, match="run trace"):
        validate_run(run_dir)


def test_validate_run_rejects_case_evidence_component_identity_mismatch(
    tmp_path: Path,
) -> None:
    run_dir = write_minimal_run(
        tmp_path,
        case_evidence_ref_overrides={"sha256": "f" * 64},
        bypass_finalization_validation=True,
    )

    with pytest.raises(ValueError, match="case.*evidence.*identity|binding"):
        validate_run(run_dir)


def test_validate_run_rejects_reanchored_schema_invalid_domain_document(
    tmp_path: Path,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    reanchor_authoritative_file(
        run_dir,
        relative_path="output/grade-report.json",
        data=b"{}\n",
        reference_slot="grade_report_ref",
        kind="grade_report",
        schema_id="tracelane://schemas/object-envelope/v2",
    )

    with pytest.raises(SchemaValidationError):
        validate_run(run_dir)


def test_validate_run_uses_same_authenticated_component_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = write_minimal_run(tmp_path)
    case_path = run_dir / "input" / "case.json"
    original_read = manifest_module._read_stable_bytes
    case_reads = 0

    def changing_read(path: Path, *args: object, **kwargs: object) -> bytes:
        nonlocal case_reads
        data = original_read(path, *args, **kwargs)  # type: ignore[arg-type]
        if path == case_path:
            case_reads += 1
            if case_reads > 1:
                return b"{}\n"
        return data

    monkeypatch.setattr(manifest_module, "_read_stable_bytes", changing_read)

    validate_run(run_dir)
    assert case_reads == 1


def test_validate_run_rejects_manifest_symlink_to_outside(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(manifest_path.read_bytes())
    manifest_path.unlink()
    try:
        manifest_path.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="link|reparse"):
        validate_run(run_dir)


def test_validate_run_rejects_symlink_descendant(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    outside = write_json(tmp_path / "outside.json", {"outside": True})
    link = run_dir / "output" / "linked.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="link|reparse"):
        validate_run(run_dir)


def test_validate_run_rejects_two_paths_for_same_hard_link(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    original = run_dir / "input" / "case.json"
    duplicate = run_dir / "input" / "case-copy.json"
    try:
        os.link(original, duplicate)
    except OSError:
        pytest.skip("hard links are unavailable on this host")

    with pytest.raises(ValueError, match="file identity|hard link"):
        validate_run(run_dir)


def test_validate_run_detects_tampered_authoritative_file(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    (run_dir / "input" / "case.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum"):
        validate_run(run_dir)


def test_validate_run_detects_tampered_manifest(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    value = manifest_value(run_dir)
    value["lifecycle_status"] = "failed"
    manifest_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="manifest content hash"):
        validate_run(run_dir)


def corrupt_run(run_dir: Path, corruption: str) -> None:
    if corruption == "fingerprint_substitution":

        def substitute_fingerprint(value: dict[str, object]) -> None:
            execution = value["execution_fingerprint"]
            assert isinstance(execution, dict)
            execution["case_sha256"] = "f" * 64
            value["run_id"] = ExecutionFingerprint.from_dict(execution).run_id

        rewrite_manifest(run_dir, substitute_fingerprint)
    elif corruption == "missing_checksum_file":
        (run_dir / "checksums.json").unlink()
    elif corruption == "extra_unlisted_file":
        write_json(run_dir / "output" / "extra.json", {})
    elif corruption == "duplicate_checksum_uri":
        rewrite_checksums(run_dir, lambda entries: entries.append(dict(entries[0])))
    else:
        field, replacement = {
            "wrong_artifact_kind": ("kind", "evidence_manifest"),
            "wrong_artifact_schema": (
                "schema_id",
                "tracelane://schemas/object-envelope/v2",
            ),
            "wrong_artifact_size": ("size_bytes", 0),
            "wrong_artifact_digest": ("sha256", "f" * 64),
            "escaped_artifact_uri": (
                "uri",
                f"tracelane://artifacts/runs/{run_dir.name}/input/../case.json",
            ),
        }[corruption]

        def corrupt_reference(value: dict[str, object]) -> None:
            reference = value["case_ref"]
            assert isinstance(reference, dict)
            reference[field] = replacement

        rewrite_manifest(run_dir, corrupt_reference)


@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [(item, RUN_CORRUPTION_ERRORS[item]) for item in RUN_CORRUPTIONS],
)
def test_public_run_validator_rejects_adversarial_matrix(
    tmp_path: Path,
    corruption: str,
    expected_error: type[ValueError],
) -> None:
    run_dir = write_minimal_run(tmp_path)
    corrupt_run(run_dir, corruption)

    with pytest.raises(expected_error) as captured:
        validate_run(run_dir)

    assert type(captured.value) is expected_error


def test_validate_run_rejects_wrong_component_media_type(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)

    def corrupt_media_type(value: dict[str, object]) -> None:
        reference = value["case_ref"]
        assert isinstance(reference, dict)
        reference["media_type"] = "text/plain"

    rewrite_manifest(run_dir, corrupt_media_type)

    with pytest.raises(SchemaValidationError):
        validate_run(run_dir)


def test_validate_run_rejects_missing_checksum_entry(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    rewrite_checksums(run_dir, lambda entries: entries.pop())

    with pytest.raises(ValueError, match="checksum coverage"):
        validate_run(run_dir)


def test_validate_run_rejects_checksum_for_missing_file(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    missing_uri = f"tracelane://artifacts/runs/{run_dir.name}/output/missing.json"
    rewrite_checksums(
        run_dir,
        lambda entries: entries.append({"uri": missing_uri, "size_bytes": 3, "sha256": "f" * 64}),
    )

    with pytest.raises(ValueError, match="checksum coverage"):
        validate_run(run_dir)


_REFERENCE_SLOT_LOCATORS = [
    ("trace_ref", None),
    ("checkpoint_refs", 0),
    ("diagnosis_ref", None),
    ("output_refs", 0),
    ("grade_report_ref", None),
    ("failure_ref", None),
]


def corrupt_reference_slot(
    value: dict[str, object],
    slot: str,
    index: int | None,
    field: str,
    replacement: object,
) -> None:
    reference: object = value[slot]
    if index is not None:
        assert isinstance(reference, list)
        reference = reference[index]
    assert isinstance(reference, dict)
    reference[field] = replacement


@pytest.mark.parametrize(("slot", "index"), _REFERENCE_SLOT_LOCATORS)
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("kind", "wrong"),
        ("schema_id", "tracelane://schemas/case/v2"),
        ("media_type", "text/plain"),
    ],
)
def test_run_manifest_schema_rejects_wrong_reference_slot_metadata(
    tmp_path: Path,
    slot: str,
    index: int | None,
    field: str,
    replacement: object,
) -> None:
    value = manifest_value(write_minimal_run(tmp_path, include_all_reference_slots=True))
    corrupt_reference_slot(value, slot, index, field, replacement)
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError):
        RunManifest.from_dict(value)


@pytest.mark.parametrize(("slot", "index"), _REFERENCE_SLOT_LOCATORS)
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("kind", "wrong"),
        ("schema_id", "tracelane://schemas/case/v2"),
        ("media_type", "text/plain"),
    ],
)
def test_validate_run_enforces_reference_slot_metadata_in_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slot: str,
    index: int | None,
    field: str,
    replacement: object,
) -> None:
    run_dir = write_minimal_run(tmp_path, include_all_reference_slots=True)
    rewrite_manifest(
        run_dir,
        lambda value: corrupt_reference_slot(value, slot, index, field, replacement),
    )
    monkeypatch.setattr(manifest_module, "validate_document", lambda *_args: None)

    message = {"kind": "kind", "schema_id": "schema", "media_type": "media"}[field]
    with pytest.raises(ValueError, match=message):
        validate_run(run_dir)


def test_validate_run_rejects_component_reference_outside_run(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    outside_path = write_json(tmp_path / "shared" / "case.json", COMPONENTS["case_ref"][1])
    outside_ref = artifact_ref_for_file(
        tmp_path,
        outside_path,
        "case",
        "tracelane://schemas/case/v2",
    )
    rewrite_manifest(
        run_dir,
        lambda value: value.__setitem__("case_ref", outside_ref.to_dict()),
    )

    with pytest.raises(ValueError, match="case.*run directory"):
        validate_run(run_dir)


def test_validate_run_rejects_uncovered_in_run_reference(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    diagnosis_path = write_json(run_dir / "output" / "diagnosis.json", {"status": "ok"})
    diagnosis_ref = artifact_ref_for_file(
        tmp_path,
        diagnosis_path,
        "diagnosis",
        "tracelane://schemas/object-envelope/v2",
    )
    rewrite_manifest(
        run_dir,
        lambda value: value.__setitem__("diagnosis_ref", diagnosis_ref.to_dict()),
    )

    with pytest.raises(ValueError, match="checksum coverage"):
        validate_run(run_dir)


@pytest.mark.parametrize(
    ("status", "completed_at", "message"),
    [
        ("created", NOW.isoformat().replace("+00:00", "Z"), "non-terminal run"),
        ("running", NOW.isoformat().replace("+00:00", "Z"), "non-terminal run"),
        ("completed", None, "terminal run"),
        ("failed", None, "terminal run"),
    ],
)
def test_run_lifecycle_requires_consistent_completion_timestamp(
    tmp_path: Path,
    status: str,
    completed_at: object,
    message: str,
) -> None:
    value = manifest_value(write_minimal_run(tmp_path))
    value["lifecycle_status"] = status
    value["completed_at"] = completed_at
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match=message):
        RunManifest.from_dict(value)


def test_run_completion_cannot_precede_start(tmp_path: Path) -> None:
    value = manifest_value(write_minimal_run(tmp_path))
    value["completed_at"] = (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match="precede"):
        RunManifest.from_dict(value)


@pytest.mark.parametrize(
    ("missing_field", "message"),
    [
        ("trace_ref", "completed run"),
        ("grade_report_ref", "completed run"),
    ],
)
def test_completed_run_requires_trace_and_grade_report(
    tmp_path: Path,
    missing_field: str,
    message: str,
) -> None:
    value = manifest_value(write_minimal_run(tmp_path))
    value[missing_field] = None
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match=message):
        RunManifest.from_dict(value)


def test_completed_run_from_dict_rejects_empty_output_refs(tmp_path: Path) -> None:
    value = manifest_value(write_minimal_run(tmp_path))
    value["output_refs"] = []
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match="output"):
        RunManifest.from_dict(value)


def test_completed_run_create_rejects_empty_output_refs(tmp_path: Path) -> None:
    manifest = RunManifest.from_dict(manifest_value(write_minimal_run(tmp_path)))
    values = {
        key: item
        for key, item in vars(manifest).items()
        if key not in {"schema_id", "schema_version", "content_sha256"}
    }
    values["output_refs"] = ()

    with pytest.raises(ValueError, match="completed run.*output"):
        RunManifest.create(**values)


def test_completed_run_python_invariant_rejects_empty_output_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = manifest_value(write_minimal_run(tmp_path))
    value["output_refs"] = []
    value["content_sha256"] = content_digest(value)
    monkeypatch.setattr(manifest_module, "validate_document", lambda *_args: None)

    with pytest.raises(ValueError, match="completed run.*output"):
        RunManifest.from_dict(value)


def test_completed_run_schema_rejects_empty_output_refs(tmp_path: Path) -> None:
    value = manifest_value(write_minimal_run(tmp_path))
    value["output_refs"] = []
    value["content_sha256"] = content_digest(value)

    with pytest.raises(SchemaValidationError):
        validate_document("run-manifest", value)


def test_validate_run_rejects_completed_run_without_output_ref(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    rewrite_manifest(
        run_dir,
        lambda value: value.__setitem__("output_refs", []),
    )

    with pytest.raises(ValueError, match="output"):
        validate_run(run_dir)


def test_completed_run_rejects_failure_record(tmp_path: Path) -> None:
    value = manifest_value(write_minimal_run(tmp_path, include_all_reference_slots=True))
    value["lifecycle_status"] = "completed"
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match="completed run"):
        RunManifest.from_dict(value)


def test_failed_run_requires_trace_and_failure_record(tmp_path: Path) -> None:
    value = manifest_value(write_minimal_run(tmp_path))
    value["lifecycle_status"] = "failed"
    value["trace_ref"] = None
    value["failure_ref"] = None
    value["content_sha256"] = content_digest(value)

    with pytest.raises(ValueError, match="failed run"):
        RunManifest.from_dict(value)


def test_artifact_ref_for_file_uses_portable_uri(tmp_path: Path) -> None:
    run_id = fingerprint().run_id
    path = write_json(tmp_path / "runs" / run_id / "input" / "case.json", {"case_id": "x"})

    reference = artifact_ref_for_file(tmp_path, path, "case", None)

    assert reference.uri == f"tracelane://artifacts/runs/{run_id}/input/case.json"
    assert reference.size_bytes == path.stat().st_size
    assert reference.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
