# TraceLane v0.2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a programmatic, reproducible Harness research loop that acquires and freezes evidence, runs the HIST-001 Napoleon counterfactual through real tool use, diagnoses the first critical failure, replays one controlled repair, compares repeated control/treatment runs, and exports auditable research and training artifacts.

**Architecture:** Preserve the v0.1 API and byte-stable golden artifacts, and add versioned v2 modules beside them. JSON Schema Draft 2020-12 is the wire contract; frozen dataclasses are the in-process contract; JSON/JSONL artifacts are authoritative. Manual evidence acquisition is isolated from scored frozen evaluation, while all runs share content-addressed storage, typed traces, stable manifests, hashes, and lineage.

**Tech Stack:** Python 3.11/3.12, standard library, `jsonschema>=4.22,<5`, pytest, Ruff, and the OpenAI-compatible Chat Completions HTTP protocol for the optional hosted model runtime.

Manual Codex-assisted acquisition is the v0.2 source-discovery boundary.
Codex or a human supplies a source URL and curated note; TraceLane binds,
reviews, and freezes those bytes. The scored agent has no network tool.
Automated search providers and raw HTTP fetching are outside v0.2.

## Global Constraints

- Keep every existing v0.1 public function and all 79 existing tests compatible.
- Do not change the v0.1 golden artifact format; v2 uses separate modules and schemas.
- Use JSON Schema Draft 2020-12 with `additionalProperties: false` for every durable object.
- Use canonical UTF-8 JSON, UTC RFC 3339 timestamps, lowercase SHA-256, and forward-slash `tracelane://` URIs.
- JSON/JSONL is authoritative; Markdown is generated from structured artifacts.
- The scored agent has no network tools; manually acquired artifacts are frozen before evaluation.
- Never store API keys, authorization headers, local absolute paths, email addresses, phone numbers, or hidden chain-of-thought.
- Treat supplied curated notes as untrusted data and never concatenate them into system instructions.
- A promoted repair changes exactly one declared Harness variable.
- Each implementation task follows red → green → refactor and ends with a one-line Conventional Commit.
- Core scope is the deterministic offline end-to-end loop. Automatic source-code rewriting, RL/SFT, swarm orchestration, and automatic repair promotion are out of scope.

## Delivery Order and Two-Week Budget

| Day | Deliverable | Tasks |
|---|---|---|
| 1 | Versioned schema, references, blob storage | 1–2 |
| 2 | Run manifests, checksums, typed trace | 3–4 |
| 3 | Manual acquisition boundary and frozen history contracts | 5–6 |
| 4 | HIST-001 curated suite and provenance validation | 7 |
| 5 | Actual Tool Use Loop and history workflow | 8–9 |
| 6 | Report contract and deterministic history runtime | 10 |
| 7 | Hard graders, fault fixtures, first critical failure | 11–12 |
| 8 | Change manifest and checkpoint branch replay | 13 |
| 9 | Five-repeat comparison and training export | 14–15 |
| 10 | Hosted model runtime, CLI, documentation, release verification | 16–17 |

## File Map

```text
src/tracelane/
├── v2/
│   ├── schema.py              # schema loading and stable validation errors
│   ├── contracts.py           # common envelope and ArtifactRef
│   ├── storage.py             # URI resolution and content-addressed blobs
│   ├── manifests.py           # execution fingerprints, run/checksum manifests
│   ├── tracing.py             # typed trace v2 and span identity
│   └── checkpoint.py          # v2 checkpoint chain and branch metadata
├── acquisition/
│   ├── contracts.py           # manual candidate and bound review records
│   └── service.py             # manual session writer and bound candidate promotion
├── history/
│   ├── contracts.py           # case, evidence, claim and report types
│   ├── loader.py              # manifest-driven suite loading
│   ├── workflows.py           # four declarative workflow arms
│   ├── orchestrator.py        # history stage machine and checkpoints
│   ├── graders.py             # hard and research-quality metrics
│   └── report.py              # JSON-to-Markdown renderer
├── tools/
│   ├── contracts.py           # ToolSpec, ToolCall and ToolResult
│   ├── registry.py            # allowlisted execution and trace hooks
│   └── evidence.py            # list_evidence and read_evidence
├── diagnosis/
│   ├── contracts.py           # violation and diagnosis contracts
│   └── diagnoser.py           # first critical failure localization
├── experiments/
│   ├── change.py              # one-variable ChangeManifest validation
│   ├── replay.py              # checkpoint suffix branching
│   └── v2_runner.py           # paired repeats and comparison
├── exporters/
│   ├── training.py            # trajectory, preference, reward JSONL
│   └── otel.py                # TraceEventV2 to OTel-compatible spans
└── runtime/
    ├── agent.py               # tool-capable runtime protocol
    ├── history_stub.py        # deterministic offline runtime
    └── openai_compatible.py   # injectable hosted-model HTTP runtime
```

Schemas live under `src/tracelane/schemas/v2/`, fixtures under
`fixtures/v0.2/`, and matching tests use the same package boundaries under
`tests/v2/`.

---

### Task 1: Versioned Schema Registry and Common Object Envelope

**Files:**
- Create: `src/tracelane/v2/__init__.py`
- Create: `src/tracelane/v2/schema.py`
- Create: `src/tracelane/v2/contracts.py`
- Create: `src/tracelane/schemas/v2/object-envelope.schema.json`
- Create: `src/tracelane/schemas/v2/artifact-ref.schema.json`
- Create: `tests/v2/__init__.py`
- Create: `tests/v2/test_schema.py`
- Create: `tests/v2/test_common_contracts.py`

**Interfaces:**
- Produces: `validate_document(name: str, value: Mapping[str, object]) -> None`
- Produces: `ArtifactRef.from_dict(value: Mapping[str, object]) -> ArtifactRef`
- Produces: `content_digest(value: Mapping[str, object]) -> str`
- Produces: `make_object_id(prefix: str, value: Mapping[str, object]) -> str`

- [ ] **Step 1: Write failing schema and contract tests**

```python
def test_schema_error_has_code_uri_and_json_pointer() -> None:
    with pytest.raises(SchemaValidationError) as captured:
        validate_document("artifact-ref", {"kind": "evidence_record"})
    assert captured.value.code == "schema_validation_failed"
    assert captured.value.schema_id == "tracelane://schemas/artifact-ref/v2"
    assert captured.value.pointer == "/"


def test_artifact_ref_requires_safe_uri_and_matching_digest_shape() -> None:
    value = {
        "kind": "evidence_record",
        "uri": "tracelane://fixtures/v0.2/history/hist-001/case.json",
        "media_type": "application/json",
        "sha256": "a" * 64,
        "size_bytes": 42,
        "schema_id": "tracelane://schemas/case/v2",
    }
    assert ArtifactRef.from_dict(value).uri.endswith("/case.json")
    with pytest.raises(ValueError, match="unsafe artifact URI"):
        ArtifactRef.from_dict({**value, "uri": "tracelane://fixtures/../secret"})
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

Run:

```powershell
python -m pytest tests/v2/test_schema.py tests/v2/test_common_contracts.py -v
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'tracelane.v2'`.

- [ ] **Step 3: Implement stable schema validation**

```python
# src/tracelane/v2/schema.py
class SchemaValidationError(ValueError):
    def __init__(self, *, schema_id: str, pointer: str, message: str) -> None:
        super().__init__(f"{schema_id} at {pointer}: {message}")
        self.code = "schema_validation_failed"
        self.schema_id = schema_id
        self.pointer = pointer


@lru_cache(maxsize=None)
def _schema(name: str) -> Mapping[str, object]:
    path = files("tracelane").joinpath("schemas", "v2", f"{name}.schema.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"schema must be an object: {name}")
    return value


def validate_document(name: str, value: Mapping[str, object]) -> None:
    schema = _schema(name)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(json.loads(canonical_json(value))),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(
            str(part).replace("~", "~0").replace("/", "~1") for part in error.absolute_path
        )
        raise SchemaValidationError(
            schema_id=str(schema["$id"]),
            pointer=pointer,
            message=error.message,
        )
```

- [ ] **Step 4: Implement the immutable common reference**

```python
# src/tracelane/v2/contracts.py
_URI = re.compile(r"^tracelane://[a-z0-9][a-z0-9._/-]*$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def content_digest(value: Mapping[str, object]) -> str:
    payload = {key: item for key, item in value.items() if key != "content_sha256"}
    return sha256_json(payload)


def make_object_id(prefix: str, value: Mapping[str, object]) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", prefix):
        raise ValueError("object ID prefix is invalid")
    return f"{prefix}_{content_digest(value)[:24]}"


@dataclass(frozen=True)
class ArtifactRef:
    kind: str
    uri: str
    media_type: str
    sha256: str
    size_bytes: int
    schema_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        value = {
            "kind": self.kind,
            "uri": self.uri,
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "schema_id": self.schema_id,
        }
        validate_document("artifact-ref", value)
        return value

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "ArtifactRef":
        validate_document("artifact-ref", value)
        uri = str(value["uri"])
        if not _URI.fullmatch(uri) or ".." in PurePosixPath(uri.removeprefix("tracelane://")).parts:
            raise ValueError("unsafe artifact URI")
        return cls(
            kind=str(value["kind"]),
            uri=uri,
            media_type=str(value["media_type"]),
            sha256=str(value["sha256"]),
            size_bytes=int(value["size_bytes"]),
            schema_id=str(value["schema_id"]) if value.get("schema_id") is not None else None,
        )
```

The two JSON schemas require the exact fields above, use the
`tracelane://schemas/.../v2` `$id`, reject unknown fields, require non-negative
`size_bytes`, and require lowercase 64-character SHA-256.

- [ ] **Step 5: Run focused and v0.1 regression tests**

Run:

```powershell
python -m pytest tests/v2/test_schema.py tests/v2/test_common_contracts.py tests/test_contracts.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tracelane/v2 src/tracelane/schemas/v2 tests/v2
git commit -m "feat: add versioned artifact contracts"
```

---

### Task 2: Safe URI Resolution and Content-Addressed Blob Storage

**Files:**
- Create: `src/tracelane/v2/storage.py`
- Create: `tests/v2/test_storage.py`

**Interfaces:**
- Consumes: `ArtifactRef`, `content_digest`
- Produces: `ArtifactRoot.resolve(uri: str) -> Path`
- Produces: `BlobStore.put_bytes(data: bytes, media_type: str, kind: str) -> ArtifactRef`
- Produces: `BlobStore.verify(ref: ArtifactRef) -> Path`

- [ ] **Step 1: Write failing containment, deduplication, and integrity tests**

```python
def test_blob_store_deduplicates_and_verifies_content(tmp_path: Path) -> None:
    store = BlobStore(ArtifactRoot(tmp_path))
    first = store.put_bytes(b"same payload", "text/plain", "evidence_blob")
    second = store.put_bytes(b"same payload", "text/plain", "evidence_blob")
    assert first == second
    assert store.verify(first).read_bytes() == b"same payload"


def test_artifact_root_rejects_traversal_and_reparse_escape(tmp_path: Path) -> None:
    root = ArtifactRoot(tmp_path)
    with pytest.raises(ValueError, match="escapes artifact root"):
        root.resolve("tracelane://artifacts/../outside.json")


def test_verify_detects_tampered_blob(tmp_path: Path) -> None:
    store = BlobStore(ArtifactRoot(tmp_path))
    ref = store.put_bytes(b"original", "application/octet-stream", "raw_fetch")
    store.root.resolve(ref.uri).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.verify(ref)
```

- [ ] **Step 2: Run the tests and confirm the missing-storage failure**

Run:

```powershell
python -m pytest tests/v2/test_storage.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tracelane.v2.storage'`.

- [ ] **Step 3: Implement safe resolution and atomic blob writes**

```python
@dataclass(frozen=True)
class ArtifactRoot:
    path: Path

    def __post_init__(self) -> None:
        supplied = Path(self.path)
        supplied.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "path", supplied.resolve())

    def resolve(self, uri: str) -> Path:
        prefix = "tracelane://artifacts/"
        if not uri.startswith(prefix):
            raise ValueError("artifact URI has the wrong root")
        relative = PurePosixPath(uri.removeprefix(prefix))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("artifact URI escapes artifact root")
        candidate = (self.path / Path(*relative.parts)).resolve(strict=False)
        try:
            candidate.relative_to(self.path)
        except ValueError as exc:
            raise ValueError("artifact URI escapes artifact root") from exc
        return candidate


class BlobStore:
    def __init__(self, root: ArtifactRoot) -> None:
        self.root = root

    def put_bytes(self, data: bytes, media_type: str, kind: str) -> ArtifactRef:
        digest = hashlib.sha256(data).hexdigest()
        uri = f"tracelane://artifacts/blobs/sha256/{digest[:2]}/{digest}.blob"
        target = self.root.resolve(uri)
        if not target.exists():
            _atomic_write_bytes(target, data)
        ref = ArtifactRef(kind, uri, media_type, digest, len(data))
        self.verify(ref)
        return ref

    def verify(self, ref: ArtifactRef) -> Path:
        path = self.root.resolve(ref.uri)
        data = path.read_bytes()
        if len(data) != ref.size_bytes:
            raise ValueError("artifact size mismatch")
        if hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError("artifact hash mismatch")
        return path
```

`_atomic_write_bytes` uses a sibling UUID temporary file, flushes and calls
`os.fsync`, then calls `os.replace`; it removes the temporary file in `finally`.

- [ ] **Step 4: Run focused tests and security regressions**

Run:

```powershell
python -m pytest tests/v2/test_storage.py tests/test_artifacts.py tests/test_security.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/tracelane/v2/storage.py tests/v2/test_storage.py
git commit -m "feat: add content addressed blob storage"
```

---

### Task 3: Execution Fingerprints, Run Manifests, and Checksums

**Files:**
- Create: `src/tracelane/v2/manifests.py`
- Create: `src/tracelane/v2/migration.py`
- Create: `src/tracelane/schemas/v2/run-manifest.schema.json`
- Create: `src/tracelane/schemas/v2/checksums.schema.json`
- Create: `src/tracelane/schemas/v2/migration-manifest.schema.json`
- Create: `tests/v2/test_manifests.py`
- Create: `tests/v2/test_migration.py`

**Interfaces:**
- Produces: `ExecutionFingerprint.run_id -> str`
- Produces: `RunManifest.to_dict() -> dict[str, object]`
- Produces: `write_checksums(run_dir: Path, authoritative_paths: Sequence[Path]) -> ArtifactRef`
- Produces: `validate_run(run_dir: Path) -> None`
- Produces: `import_v1_run(source_run_dir: Path, artifact_root: Path) -> MigrationResult`

- [ ] **Step 1: Write failing identity, immutability, and checksum tests**

```python
def test_run_id_changes_for_code_grader_runtime_or_repeat() -> None:
    base = fingerprint()
    assert base.run_id == fingerprint().run_id
    assert base.run_id != replace(base, repeat=2).run_id
    assert base.run_id != replace(base, code_revision="b" * 40).run_id
    assert base.run_id != replace(base, grader_set_sha256="f" * 64).run_id


def test_validate_run_detects_missing_or_tampered_authoritative_file(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    validate_run(run_dir)
    (run_dir / "input" / "case.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        validate_run(run_dir)


def test_v1_import_preserves_bytes_and_does_not_modify_source(tmp_path: Path) -> None:
    source = run_v1_demo(tmp_path / "source")
    before = tree_hashes(source)
    result = import_v1_run(source, tmp_path / "target")
    assert tree_hashes(source) == before
    assert tree_hashes(result.payload_dir) == before
    assert result.manifest.source_format == "tracelane-v1"
    assert result.manifest.import_id == result.import_dir.name
```

- [ ] **Step 2: Run and confirm the missing-manifest failure**

Run:

```powershell
python -m pytest tests/v2/test_manifests.py tests/v2/test_migration.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'tracelane.v2.manifests'`.

- [ ] **Step 3: Implement the fingerprint and manifest contracts**

```python
@dataclass(frozen=True)
class ExecutionFingerprint:
    case_sha256: str
    evidence_bundle_sha256: str
    harness_config_sha256: str
    runtime_config_sha256: str
    grader_set_sha256: str
    repeat: int
    code_revision: str

    @property
    def run_id(self) -> str:
        return sha256_json(asdict(self))


@dataclass(frozen=True)
class RunManifest:
    schema_id: str
    schema_version: str
    run_id: str
    lifecycle_status: str
    started_at: datetime
    completed_at: datetime | None
    case_ref: ArtifactRef
    evidence_manifest_ref: ArtifactRef
    harness_config_ref: ArtifactRef
    runtime_config_ref: ArtifactRef
    grader_set_ref: ArtifactRef
    code_revision: str
    environment_fingerprint: str
    semantic_convention_version: str
    redaction_policy_id: str
    trace_ref: ArtifactRef | None
    checkpoint_refs: tuple[ArtifactRef, ...]
    output_refs: tuple[ArtifactRef, ...]
    grade_report_ref: ArtifactRef | None
    checksums_ref: ArtifactRef | None
    parent_run_id: str | None = None
    branch_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        value = json.loads(canonical_json(self))
        validate_document("run-manifest", value)
        return value
```

- [ ] **Step 4: Implement checksum publication and validation**

```python
def write_checksums(run_dir: Path, authoritative_paths: Sequence[Path]) -> Path:
    forbidden = {run_dir / "manifest.json", run_dir / "checksums.json"}
    if any(path in forbidden for path in authoritative_paths):
        raise ValueError("manifest and checksum files cannot hash themselves")
    rows = []
    for path in sorted(authoritative_paths, key=lambda item: item.as_posix()):
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(run_dir.resolve()).as_posix()
        data = resolved.read_bytes()
        rows.append(
            {
                "uri": f"tracelane://artifacts/runs/{run_dir.name}/{relative}",
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    target = run_dir / "checksums.json"
    atomic_write_json(target, {"entries": rows, "root_sha256": sha256_json(rows)})
    return target


def validate_run(run_dir: Path) -> None:
    manifest = read_and_validate(run_dir / "manifest.json", "run-manifest")
    checksums = read_and_validate(run_dir / "checksums.json", "checksums")
    for entry in checksums["entries"]:
        path = uri_to_run_path(run_dir, entry["uri"])
        data = path.read_bytes()
        if len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"run checksum mismatch: {entry['uri']}")
    if sha256_json(checksums["entries"]) != checksums["root_sha256"]:
        raise ValueError("run checksum root mismatch")
    if manifest["run_id"] != run_dir.name:
        raise ValueError("run manifest identity mismatch")
```

- [ ] **Step 5: Import v1 runs without pretending they are native v2 runs**

```python
def import_v1_run(source_run_dir: Path, artifact_root: Path) -> MigrationResult:
    source = Path(source_run_dir).resolve(strict=True)
    inspect_run(source)
    entries = tree_checksum_entries(source)
    import_id = sha256_json(
        {
            "source_format": "tracelane-v1",
            "source_run_id": source.name,
            "entries": entries,
        }
    )[:24]
    import_dir = Path(artifact_root).resolve() / "imports" / "v1" / import_id
    payload_dir = import_dir / "payload"
    copy_tree_without_links(source, payload_dir)
    copied_entries = tree_checksum_entries(payload_dir)
    if copied_entries != entries:
        raise ValueError("v1 import bytes do not match source")
    manifest = MigrationManifest(
        schema_id="tracelane://schemas/migration-manifest/v2",
        schema_version="2.0.0",
        import_id=import_id,
        source_format="tracelane-v1",
        source_run_id=source.name,
        imported_at=datetime.now(UTC),
        entries=tuple(copied_entries),
        payload_root_sha256=sha256_json(copied_entries),
    )
    atomic_write_json(import_dir / "manifest.json", manifest.to_dict())
    return MigrationResult(import_dir, payload_dir, manifest)
```

The importer rejects links and reparse points, copies files in stable relative
path order, and records the source path only in the local command result—not in
the portable Migration Manifest.

- [ ] **Step 6: Run focused tests and v0.1 artifact regressions**

Run:

```powershell
python -m pytest tests/v2/test_manifests.py tests/v2/test_migration.py tests/test_artifacts.py tests/test_reproducibility.py -v
```

Expected: all selected tests PASS and the v0.1 golden output remains unchanged.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/v2/manifests.py src/tracelane/v2/migration.py src/tracelane/schemas/v2 tests/v2
git commit -m "feat: add reproducible run manifests"
```

---

### Task 4: Typed Trace v2, Redaction, and Span Identity

**Files:**
- Create: `src/tracelane/v2/tracing.py`
- Create: `src/tracelane/schemas/v2/trace-event.schema.json`
- Modify: `src/tracelane/security.py`
- Create: `tests/v2/test_tracing.py`
- Modify: `tests/test_security.py`

**Interfaces:**
- Produces: `TraceContext(trace_id: str, span_id: str, parent_span_id: str | None)`
- Produces: `TraceRecorderV2.emit(...) -> TraceEventV2`
- Produces: `classify_and_redact(value: object) -> RedactedPayload`

- [ ] **Step 1: Write failing trace identity and redaction tests**

```python
def test_trace_event_has_stable_causation_and_span_fields(tmp_path: Path) -> None:
    recorder = recorder_v2(tmp_path, fixed_clock())
    called = recorder.emit("tool.called", {"authorization": "Bearer secret"}, stage="research")
    observed = recorder.emit(
        "tool.observed",
        {"result": "ok"},
        stage="research",
        correlation_id="call_001",
        causation_id=called.event_id,
        parent_span_id=called.span_id,
    )
    assert observed.sequence == 2
    assert observed.causation_id == called.event_id
    assert observed.parent_span_id == called.span_id
    row = json.loads(
        (tmp_path / "runs" / RUN_ID / "trace/events.jsonl").read_text().splitlines()[0]
    )
    assert row["payload"]["authorization"] == "[REDACTED]"
    assert row["redaction_applied"] is True


def test_trace_rejects_unknown_event_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="event_type"):
        recorder_v2(tmp_path, fixed_clock()).emit("anything.happened", {})
```

- [ ] **Step 2: Run and confirm the missing TraceRecorderV2 failure**

Run:

```powershell
python -m pytest tests/v2/test_tracing.py tests/test_security.py -v
```

Expected: FAIL because `TraceRecorderV2` and `classify_and_redact` do not exist.

- [ ] **Step 3: Extend structured redaction**

```python
@dataclass(frozen=True)
class RedactedPayload:
    value: object
    payload_classification: str
    redaction_applied: bool


def classify_and_redact(value: object) -> RedactedPayload:
    canonical_json(value)
    sanitized = redact(value)
    changed = canonical_json(sanitized) != canonical_json(value)
    return RedactedPayload(
        value=sanitized,
        payload_classification="restricted" if changed else "internal",
        redaction_applied=changed,
    )
```

Extend `_SENSITIVE_KEY` to match `cookie`, `set-cookie`, `email`, `phone`, and
`local_path`; replace Windows drive and POSIX home paths with `[LOCAL_PATH]`.

- [ ] **Step 4: Implement typed append-only events**

```python
_EVENT_TYPES = frozenset(
    {
        "run.started",
        "run.completed",
        "evidence.collected",
        "evidence.rejected",
        "context.selected",
        "plan.created",
        "model.called",
        "model.observed",
        "tool.called",
        "tool.observed",
        "claim.created",
        "assumption.created",
        "scenario.branched",
        "checkpoint.saved",
        "constraint.checked",
        "violation.detected",
        "stage.started",
        "stage.completed",
        "stage.failed",
        "answer.finalized",
        "grade.completed",
        "diagnosis.completed",
        "repair.proposed",
        "repair.approved",
        "replay.started",
        "replay.completed",
    }
)


@dataclass(frozen=True)
class TraceEventV2:
    schema_id: str
    schema_version: str
    event_id: str
    sequence: int
    event_type: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    correlation_id: str | None
    causation_id: str | None
    run_id: str
    stage: str | None
    recorded_at: datetime
    attributes: Mapping[str, object]
    payload: object
    payload_classification: str
    redaction_applied: bool

    def to_dict(self) -> dict[str, object]:
        return json.loads(canonical_json(self))


def emit(
    self,
    event_type: str,
    payload: Mapping[str, object],
    *,
    stage: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    parent_span_id: str | None = None,
) -> TraceEventV2:
    if event_type not in _EVENT_TYPES:
        raise ValueError("event_type is not registered")
    sequence = self._next_sequence
    trace_id = hashlib.sha256(self.run_id.encode()).hexdigest()[:32]
    span_id = hashlib.sha256(f"{self.run_id}:{sequence}".encode()).hexdigest()[:16]
    redacted = classify_and_redact(payload)
    identity = {"run_id": self.run_id, "sequence": sequence, "event_type": event_type}
    event = TraceEventV2(
        schema_id="tracelane://schemas/trace-event/v2",
        schema_version="2.0.0",
        event_id=f"evt_{sha256_json(identity)[:24]}",
        sequence=sequence,
        event_type=event_type,
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        run_id=self.run_id,
        stage=stage,
        recorded_at=self.clock().astimezone(UTC),
        attributes={},
        payload=redacted.value,
        payload_classification=redacted.payload_classification,
        redaction_applied=redacted.redaction_applied,
    )
    validate_document("trace-event", event.to_dict())
    self.store.append_jsonl("trace/events.jsonl", event.to_dict())
    self._next_sequence += 1
    return event
```

`TraceRecorderV2` validates existing rows on reopen, including schema,
contiguous sequence, `run_id`, `event_id`, `trace_id`, and direct
`causation_id` references. It exposes read-only `run_id` and `store`
properties so Tool Loop and graders never reach into private fields.

- [ ] **Step 5: Run focused tests, security tests, and v0.1 golden tests**

Run:

```powershell
python -m pytest tests/v2/test_tracing.py tests/test_security.py tests/test_tracing.py tests/test_reproducibility.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tracelane/v2/tracing.py src/tracelane/schemas/v2/trace-event.schema.json src/tracelane/security.py tests
git commit -m "feat: add typed and redacted trace events"
```

---

### Task 5: Manual Codex-Assisted Evidence Acquisition Lane

**Files:**
- Create: `src/tracelane/acquisition/__init__.py`
- Create: `src/tracelane/acquisition/contracts.py`
- Create: `src/tracelane/acquisition/service.py`
- Create: `src/tracelane/schemas/v2/acquisition-session.schema.json`
- Create: `src/tracelane/schemas/v2/evidence-candidate.schema.json`
- Create: `src/tracelane/schemas/v2/candidate-review.schema.json`
- Create: `tests/v2/test_acquisition.py`

**Interfaces:**
- Produces: `ManualAcquisitionService.ingest(...) -> EvidenceCandidate`
- Produces: `ManualAcquisitionService.promote(candidate_id: str, review: CandidateReview) -> ArtifactRef`
- Produces: `EvidenceCandidate.from_dict(value: Mapping[str, object]) -> EvidenceCandidate`
- Produces: `CandidateReview.create(candidate: EvidenceCandidate, ...) -> CandidateReview`

- [ ] **Step 1: Write failing manual acquisition and binding tests**

```python
def test_manual_acquisition_binds_curated_bytes_and_source_metadata(tmp_path: Path) -> None:
    service = ManualAcquisitionService(
        tmp_path,
        session_id="acq_hist001_20260724",
        clock=fixed_clock(),
    )
    candidate = service.ingest(
        query="Treaty of Tilsit",
        title="Primary source",
        source_url="https://history.example/treaty",
        document_date="1807-07",
        date_precision="month",
        curated_text="Curated treaty note",
    )
    assert candidate.content_sha256 == hashlib.sha256(b"Curated treaty note").hexdigest()
    assert candidate.trust_level == "untrusted_external"
    assert "system_prompt" not in candidate.to_dict()


def test_promotion_rejects_stale_approval(tmp_path: Path) -> None:
    service, candidate = manual_candidate(tmp_path)
    review = replace(
        approved_review(candidate),
        candidate_record_sha256="f" * 64,
    )
    with pytest.raises(ValueError, match="review"):
        service.promote(candidate.candidate_id, review)
```

- [ ] **Step 2: Run and confirm the manual acquisition module is absent**

Run:

```powershell
python -m pytest tests/v2/test_acquisition.py -v
```

Expected: FAIL during import of `tracelane.acquisition`.

- [ ] **Step 3: Implement candidate and review contracts**

```python
@dataclass(frozen=True)
class EvidenceCandidate:
    schema_id: str
    schema_version: str
    record_sha256: str
    candidate_id: str
    query: str
    title: str
    source_url: str
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    retrieved_at: datetime
    content_ref: ArtifactRef
    content_sha256: str
    trust_level: Literal["untrusted_external"] = "untrusted_external"


@dataclass(frozen=True)
class CandidateReview:
    content_sha256: str
    candidate_id: str
    candidate_record_sha256: str
    candidate_content_sha256: str
    source_locator_sha256: str
    decision: Literal["approved", "rejected"]
    reviewer: str
    reviewed_at: datetime
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    available_at: datetime
    source_type: Literal["primary", "secondary", "dataset"]
    license: str
    reason: str
```

- [ ] **Step 4: Implement the manual session writer**

```python
class ManualAcquisitionService:
    def ingest(
        self,
        *,
        query: str,
        title: str,
        source_url: str,
        document_date: str,
        date_precision: str,
        curated_text: str,
    ) -> EvidenceCandidate:
        canonical_url = canonical_source_url(source_url)
        redacted = classify_and_redact(curated_text)
        content_ref = self._blob_store.put_bytes(
            redacted.value.encode("utf-8"),
            "text/plain",
            "evidence_blob",
        )
        candidate = EvidenceCandidate.create(
            candidate_id=compute_candidate_id(...),
            query=query,
            title=title,
            source_url=canonical_url,
            document_date=document_date,
            date_precision=date_precision,
            retrieved_at=self._now(),
            content_ref=content_ref,
        )
        return self._write_or_load_candidate(candidate)
```

The session manifest records `mode: codex_manual` and
`network_access_available_to_agent: false`. The supplied URL is provenance
metadata, not a fetch instruction. Candidate identity binds canonical source
metadata to the exact redacted bytes in content-addressed storage.

- [ ] **Step 5: Bind explicit review to the exact candidate**

`promote` accepts only an approved `CandidateReview`. It revalidates the stored
candidate, verifies the candidate blob, and requires exact candidate ID, record
digest, content digest, source-locator digest, document date, date precision,
availability, source type, license, reviewer, and reason bindings. Candidate
and review records are immutable once written.

- [ ] **Step 6: Run the offline acquisition and security tests**

Run:

```powershell
python -m pytest tests/v2/test_acquisition.py tests/test_security.py -v
```

Expected: all selected tests PASS. The acquisition lane has no network
transport or search-provider dependency.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/acquisition src/tracelane/schemas/v2 tests/v2/test_acquisition.py
git commit -m "feat: add auditable evidence acquisition"
```

---

### Task 6: Historical Case, Evidence, and Suite Contracts

**Files:**
- Create: `src/tracelane/history/__init__.py`
- Create: `src/tracelane/history/contracts.py`
- Create: `src/tracelane/history/loader.py`
- Create: `src/tracelane/schemas/v2/suite-manifest.schema.json`
- Create: `src/tracelane/schemas/v2/case.schema.json`
- Create: `src/tracelane/schemas/v2/evidence-record.schema.json`
- Create: `src/tracelane/schemas/v2/evidence-manifest.schema.json`
- Create: `src/tracelane/schemas/v2/fault-fixture.schema.json`
- Create: `tests/v2/test_history_contracts.py`
- Create: `tests/v2/test_history_loader.py`

**Interfaces:**
- Produces: `load_history_case(path: Path) -> HistoryCase`
- Produces: `load_evidence_manifest(path: Path) -> EvidenceManifest`
- Produces: `load_history_suite(root: Path, split: str) -> tuple[HistoryScenarioEntry, ...]`
- Produces: `freeze_history_evidence(case: HistoryCase, manifest: EvidenceManifest) -> FrozenHistoryBundle`

- [ ] **Step 1: Write failing provenance, cutoff, and manifest-driven loader tests**

```python
def test_evidence_record_preserves_date_precision_and_provenance() -> None:
    record = EvidenceRecordV2.from_dict(evidence_record_value())
    assert record.document_date == "1812-05"
    assert record.date_precision == "month"
    assert record.known_by_cutoff == "plausibly_known"
    assert record.content_ref.kind == "evidence_blob"


def test_freeze_rejects_future_record_even_if_manifest_lists_it_as_admitted(tmp_path: Path) -> None:
    case, manifest = case_and_manifest(tmp_path, admitted_available_at="1812-06-25T00:00:00Z")
    with pytest.raises(ValueError, match="after decision cutoff"):
        freeze_history_evidence(case, manifest)


def test_suite_loader_uses_declared_split_not_directory_scanning(tmp_path: Path) -> None:
    write_minimal_history_suite(
        tmp_path,
        development=["hist-001/clean"],
        heldout=["hist-001/fault/logistics-context-omission"],
    )
    (tmp_path / "history" / "undeclared").mkdir()
    entries = load_history_suite(tmp_path, "development")
    assert [entry.scenario_id for entry in entries] == ["hist-001/clean"]
```

- [ ] **Step 2: Run and confirm missing history contracts**

Run:

```powershell
python -m pytest tests/v2/test_history_contracts.py tests/v2/test_history_loader.py -v
```

Expected: FAIL during import of `tracelane.history`.

- [ ] **Step 3: Implement evidence and case dataclasses**

```python
@dataclass(frozen=True)
class EvidenceRecordV2:
    schema_id: str
    schema_version: str
    evidence_id: str
    document_date: str
    date_precision: Literal["day", "month", "year", "estimated"]
    available_at: datetime
    known_by_cutoff: Literal["known", "plausibly_known", "unavailable"]
    source_type: Literal["primary", "secondary", "dataset"]
    source_title: str
    source_locator: str
    license: str
    excerpt_kind: Literal["verbatim", "translated", "paraphrased"]
    content_ref: ArtifactRef
    fact_ids: tuple[str, ...]
    transformation_refs: tuple[ArtifactRef, ...]
    provenance_sha256: str

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "EvidenceRecordV2":
        validate_document("evidence-record", value)
        return cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            evidence_id=str(value["evidence_id"]),
            document_date=str(value["document_date"]),
            date_precision=str(value["date_precision"]),
            available_at=parse_utc(str(value["available_at"])),
            known_by_cutoff=str(value["known_by_cutoff"]),
            source_type=str(value["source_type"]),
            source_title=str(value["source_title"]),
            source_locator=str(value["source_locator"]),
            license=str(value["license"]),
            excerpt_kind=str(value["excerpt_kind"]),
            content_ref=ArtifactRef.from_dict(value["content_ref"]),
            fact_ids=tuple(str(item) for item in value["fact_ids"]),
            transformation_refs=tuple(
                ArtifactRef.from_dict(item) for item in value["transformation_refs"]
            ),
            provenance_sha256=str(value["provenance_sha256"]),
        )


@dataclass(frozen=True)
class HistoryCase:
    case_id: str
    title: str
    decision_maker: str
    cutoff_at: datetime
    intervention: str
    projection_end: str
    minimum_alternatives: int
    minimum_scenario_branches: int
    required_domains: tuple[str, ...]
    evidence_manifest_ref: ArtifactRef
    rubric_refs: tuple[ArtifactRef, ...]


@dataclass(frozen=True)
class HistoryScenarioEntry:
    scenario_id: str
    case_id: str
    case_ref: ArtifactRef
    evidence_manifest_ref: ArtifactRef
    fault_ref: ArtifactRef | None
```

`EvidenceManifest` contains `case_id`, `cutoff_at`, `record_refs`,
`rejected_future_refs`, `source_licenses`, `transformations`, and
`bundle_sha256`. `FrozenHistoryBundle` contains resolved admitted records and
rejected IDs; its hash covers record refs, cutoff, and transformation refs.

- [ ] **Step 4: Implement manifest-only suite loading**

```python
def load_history_suite(root: Path, split: str) -> tuple[HistoryScenarioEntry, ...]:
    root = Path(root).resolve(strict=True)
    manifest = read_json_object(root / "manifest.json")
    validate_document("suite-manifest", manifest)
    split_path = resolve_fixture_ref(root, manifest["splits"][split])
    split_value = read_json_object(split_path)
    scenario_ids = tuple(str(item) for item in split_value["scenario_ids"])
    by_id = {str(item["scenario_id"]): item for item in manifest["scenarios"]}
    if len(scenario_ids) != len(set(scenario_ids)) or set(scenario_ids) - set(by_id):
        raise ValueError("suite split references are invalid")
    return tuple(
        HistoryScenarioEntry.from_dict(by_id[scenario_id], fixture_root=root)
        for scenario_id in scenario_ids
    )
```

`resolve_fixture_ref` accepts only `tracelane://fixtures/v0.2/` URIs, rejects
absolute paths, `..`, symlinks, and reparse points, and verifies size and hash
before parsing the referenced object.

- [ ] **Step 5: Run focused tests and v0.1 suite compatibility**

Run:

```powershell
python -m pytest tests/v2/test_history_contracts.py tests/v2/test_history_loader.py tests/test_suite.py tests/test_v01_fixtures.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tracelane/history src/tracelane/schemas/v2 tests/v2
git commit -m "feat: add historical evidence contracts"
```

---

### Task 7: Curated HIST-001 Fixture, Splits, and Provenance Lock

**Files:**
- Create: `scripts/build_hist001.py`
- Create: `fixtures/v0.2/manifest.json`
- Create: `fixtures/v0.2/splits/development.json`
- Create: `fixtures/v0.2/splits/heldout.json`
- Create: `fixtures/v0.2/history/hist-001/case.json`
- Create: `fixtures/v0.2/history/hist-001/suite-entry.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/manifest.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-0001.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-0002.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-0003.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-0004.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-0005.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-0006.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-future-0001.json`
- Create: `fixtures/v0.2/history/hist-001/evidence/blobs/sha256/`
- Create: `fixtures/v0.2/history/hist-001/rubrics/historical-research-v1.json`
- Create: `fixtures/v0.2/history/hist-001/rubrics/diagnosis-v1.json`
- Create: `tests/v2/test_hist001_fixture.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: history loaders and schema validation from Task 6
- Produces: packaged `fixtures/v0.2` with byte-stable manifest hashes
- Produces: `build_hist001.verify(root: Path) -> None`

- [ ] **Step 1: Write the fixture acceptance test before adding data**

```python
def test_hist001_has_locked_cutoff_domains_sources_and_future_control() -> None:
    development = load_history_suite(FIXTURES_V02, "development")
    heldout = load_history_suite(FIXTURES_V02, "heldout")
    entry = next(item for item in development if item.scenario_id == "hist-001/clean")
    case = load_history_case(entry.case_ref_path)
    bundle = freeze_history_evidence(
        case,
        load_evidence_manifest(entry.evidence_manifest_path),
    )
    assert case.case_id == "hist-001"
    assert case.cutoff_at == datetime(1812, 6, 23, 23, 59, 59, tzinfo=UTC)
    assert case.intervention == "Napoleon does not cross the Niemen or launch the Russian campaign."
    assert set(case.required_domains) == {
        "diplomacy",
        "military",
        "logistics",
        "economy",
        "iberia",
        "imperial_governance",
    }
    assert len(bundle.records) == 6
    assert bundle.rejected_future_ids == ("hist-001-ev-future-0001",)
    assert all(record.available_at <= case.cutoff_at for record in bundle.records)
    assert all(record.license and record.source_locator for record in bundle.records)
    assert {item.scenario_id for item in development} == {
        "hist-001/clean",
        "hist-001/fault/future-leakage",
        "hist-001/fault/ambiguous-source-contract",
    }
    assert {item.scenario_id for item in heldout} == {"hist-001/fault/logistics-context-omission"}
```

- [ ] **Step 2: Run and confirm the fixture is absent**

Run:

```powershell
python -m pytest tests/v2/test_hist001_fixture.py -v
```

Expected: FAIL because `fixtures/v0.2/manifest.json` does not exist.

- [ ] **Step 3: Curate and review six evidence records with fixed research questions**

Use these exact prompts for manual Codex- or human-assisted source discovery.
For each accepted source, supply its URL and a curated note to
`ManualAcquisitionService` and retain the complete manual acquisition session:

```text
"Treaty of Tilsit" 1807 full text public domain
"Berlin Decree" "Milan Decree" full text public domain
Russia tariff decree 1810 continental system primary source
Wellington dispatch Peninsular War 1811 full text
Napoleon correspondence supplies magazines 1812 before June full text
French conscription allied contingents decree 1811 1812 primary source
```

Accept one record per query only when:

1. the source is a public archive, national library, university collection, Wikisource with provenance, or scanned public-domain volume;
2. the underlying document existed before the cutoff;
3. the excerpt supports exactly one or more declared facts without importing a later historian's conclusion;
4. the repository may legally store the selected excerpt or faithful paraphrase;
5. a human review entry records reviewer, review time, decision, and reason.

Use these stable fixture identities and topics:

```python
EVIDENCE_TOPICS = {
    "hist-001-ev-0001": ("diplomacy", "treaty_of_tilsit"),
    "hist-001-ev-0002": ("economy", "berlin_decree"),
    "hist-001-ev-0003": ("economy", "diplomacy", "russian_tariff_and_trade"),
    "hist-001-ev-0004": ("logistics", "prewar_supply_correspondence"),
    "hist-001-ev-0005": ("iberia", "peninsular_commitment"),
    "hist-001-ev-0006": ("military", "imperial_governance", "conscription_and_allied_forces"),
    "hist-001-ev-future-0001": ("military", "post_campaign_outcome_control"),
}
```

The future control uses a public-domain document created after 1812-06-23 and
is present only in `rejected_future_refs`.

- [ ] **Step 4: Stop for provenance review before freezing the fixture**

Generate `artifacts/acquisition/<session-id>/candidate-review.md` from the
structured candidates. Present source title, locator, document date, permitted
excerpt/paraphrase, license basis, supported fact IDs, and content hash to the
user. Continue only after explicit approval. Record the actual approver and
approval timestamp in each review object; never pre-fill a person's name or
invent approval.

- [ ] **Step 5: Implement deterministic fixture generation and verification**

```python
def verify(root: Path) -> None:
    development = load_history_suite(root, "development")
    heldout = load_history_suite(root, "heldout")
    scenario_ids = {entry.scenario_id for entry in (*development, *heldout)}
    expected = {
        "hist-001/clean",
        "hist-001/fault/future-leakage",
        "hist-001/fault/ambiguous-source-contract",
        "hist-001/fault/logistics-context-omission",
    }
    if scenario_ids != expected:
        raise ValueError("hist-001 scenario splits are invalid")
    clean = next(entry for entry in development if entry.scenario_id == "hist-001/clean")
    case = load_history_case(clean.case_ref_path)
    manifest = load_evidence_manifest(clean.evidence_manifest_path)
    bundle = freeze_history_evidence(case, manifest)
    if len(bundle.records) != 6:
        raise ValueError("hist-001 must contain six admitted evidence records")
    if bundle.rejected_future_ids != ("hist-001-ev-future-0001",):
        raise ValueError("hist-001 future control is invalid")
    verify_fixture_tree_hashes(root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", type=Path, required=True)
    verify(parser.parse_args().verify)
```

All blobs are named from their real SHA-256. Every JSON object is canonicalized
before hashing. `fixtures/v0.2/manifest.json` explicitly lists every case,
split, rubric, workflow, fault, and generator revision.

- [ ] **Step 6: Package and verify the fixture**

Add this wheel include:

```toml
[tool.hatch.build.targets.wheel.force-include]
"fixtures/v0.1" = "tracelane/fixtures/v0.1"
"fixtures/v0.2" = "tracelane/fixtures/v0.2"
```

Run:

```powershell
python scripts/build_hist001.py --verify fixtures/v0.2
python -m pytest tests/v2/test_hist001_fixture.py tests/test_package.py -v
python -m build
```

Expected: verification succeeds, tests PASS, and the wheel contains both
`tracelane/fixtures/v0.1` and `tracelane/fixtures/v0.2`.

- [ ] **Step 7: Commit**

```powershell
git add scripts/build_hist001.py fixtures/v0.2 tests/v2/test_hist001_fixture.py pyproject.toml
git commit -m "data: add the HIST-001 evidence suite"
```

---

### Task 8: Tool Contracts, Registry, and Frozen Evidence Tools

**Files:**
- Create: `src/tracelane/tools/__init__.py`
- Create: `src/tracelane/tools/contracts.py`
- Create: `src/tracelane/tools/registry.py`
- Create: `src/tracelane/tools/evidence.py`
- Create: `src/tracelane/schemas/v2/tool-call.schema.json`
- Create: `src/tracelane/schemas/v2/tool-result.schema.json`
- Create: `tests/v2/test_tools.py`

**Interfaces:**
- Produces: `ToolSpec`, `ToolCall`, `ToolResult`, `ToolContext`
- Produces: `ToolRegistry.execute(call: ToolCall, context: ToolContext) -> ToolResult`
- Produces: `build_frozen_evidence_registry(bundle: FrozenHistoryBundle) -> ToolRegistry`

- [ ] **Step 1: Write failing discovery, execution, and PIT guard tests**

```python
def test_registry_lists_and_reads_only_admitted_evidence(hist001_bundle) -> None:
    registry = build_frozen_evidence_registry(hist001_bundle)
    names = [spec.name for spec in registry.specs]
    assert names == ["list_evidence", "read_evidence"]
    listed = registry.execute(ToolCall("call_1", "list_evidence", {}), tool_context())
    assert listed.is_error is False
    assert len(listed.output["records"]) == 6
    read = registry.execute(
        ToolCall("call_2", "read_evidence", {"evidence_id": "hist-001-ev-0001"}),
        tool_context(),
    )
    assert read.output["evidence_id"] == "hist-001-ev-0001"


def test_read_evidence_hard_blocks_rejected_future_record(hist001_bundle) -> None:
    registry = build_frozen_evidence_registry(hist001_bundle)
    result = registry.execute(
        ToolCall("call_3", "read_evidence", {"evidence_id": "hist-001-ev-future-0001"}),
        tool_context(),
    )
    assert result.is_error is True
    assert result.error_code == "evidence_not_admitted"


def test_frozen_registry_has_no_network_tool(hist001_bundle) -> None:
    tool_names = {spec.name for spec in build_frozen_evidence_registry(hist001_bundle).specs}
    assert {"search_web", "fetch_url"}.isdisjoint(tool_names)
```

- [ ] **Step 2: Run and confirm missing tools**

Run:

```powershell
python -m pytest tests/v2/test_tools.py -v
```

Expected: FAIL during import of `tracelane.tools`.

- [ ] **Step 3: Implement typed tool calls and allowlisted execution**

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    tool_name: str
    arguments: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        value = json.loads(canonical_json(self))
        validate_document("tool-call", value)
        return value


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    tool_name: str
    output: Mapping[str, object]
    is_error: bool
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        value = json.loads(canonical_json(self))
        validate_document("tool-result", value)
        return value


@dataclass(frozen=True)
class ToolContext:
    run_mode: Literal["frozen_eval", "live_research"]
    bundle: FrozenHistoryBundle
    fixture_resolver: FixtureResolver


class ToolExecutionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Tool(Protocol):
    spec: ToolSpec

    def execute(
        self, arguments: Mapping[str, object], context: ToolContext
    ) -> Mapping[str, object]: ...


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools = {tool.spec.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("tool names must be unique")

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        tool = self._tools.get(call.tool_name)
        if tool is None:
            return ToolResult(call.call_id, call.tool_name, {}, True, "tool_not_allowed")
        validate_json_schema(tool.spec.input_schema, call.arguments)
        try:
            output = tool.execute(call.arguments, context)
            validate_json_schema(tool.spec.output_schema, output)
            return ToolResult(call.call_id, call.tool_name, output, False)
        except ToolExecutionError as exc:
            return ToolResult(call.call_id, call.tool_name, {}, True, exc.code)
```

- [ ] **Step 4: Implement `list_evidence` and `read_evidence`**

`list_evidence` returns only ID, dates, type, title, fact IDs, and source
locator. Both tools receive an immutable record allowlist when the registry is
built. `read_evidence` resolves an allowlisted record's `content_ref`, verifies
its size and hash, and returns the content plus provenance. It rejects any ID
outside that selected subset, including omitted records and
`rejected_future_ids`; it never trusts a model-supplied or context-supplied
record list.

```python
def execute(self, arguments: Mapping[str, object], context: ToolContext) -> Mapping[str, object]:
    evidence_id = str(arguments["evidence_id"])
    if evidence_id not in self._records_by_id:
        raise ToolExecutionError("evidence_not_admitted")
    record = self._records_by_id[evidence_id]
    text = context.fixture_resolver.read_text(record.content_ref)
    return {
        "evidence_id": record.evidence_id,
        "text": text,
        "fact_ids": list(record.fact_ids),
        "document_date": record.document_date,
        "date_precision": record.date_precision,
        "available_at": record.available_at,
        "known_by_cutoff": record.known_by_cutoff,
        "source_type": record.source_type,
        "source_title": record.source_title,
        "source_locator": record.source_locator,
        "license": record.license,
        "provenance_sha256": record.provenance_sha256,
    }
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/v2/test_tools.py tests/v2/test_hist001_fixture.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/tracelane/tools src/tracelane/schemas/v2 tests/v2/test_tools.py
git commit -m "feat: add guarded evidence tools"
```

---

### Task 9: Tool-Capable Runtime Protocol and Bounded Agent Loop

**Files:**
- Create: `src/tracelane/runtime/agent.py`
- Create: `src/tracelane/runtime/history_stub.py`
- Create: `src/tracelane/history/config.py`
- Create: `src/tracelane/history/workflows.py`
- Create: `src/tracelane/schemas/v2/harness-config.schema.json`
- Create: `src/tracelane/schemas/v2/workflow-config.schema.json`
- Create: `fixtures/v0.2/history/hist-001/workflows/direct.json`
- Create: `fixtures/v0.2/history/hist-001/workflows/evidence-ledger.json`
- Create: `fixtures/v0.2/history/hist-001/workflows/evidence-ledger-counterargument.json`
- Create: `fixtures/v0.2/history/hist-001/workflows/evidence-ledger-counterargument-scenarios.json`
- Create: `tests/v2/test_agent_loop.py`
- Create: `tests/v2/test_workflows.py`

**Interfaces:**
- Produces: `AgentTurnRequest`, `AgentTurnResponse`
- Produces: `ToolCapableRuntime.complete_turn(request: AgentTurnRequest) -> AgentTurnResponse`
- Produces: `run_tool_stage(...) -> Mapping[str, object]`
- Produces: `load_workflow(path: Path) -> WorkflowSpec`
- Produces: `HarnessConfigV2.from_dict(value: Mapping[str, object]) -> HarnessConfigV2`

- [ ] **Step 1: Write failing multi-turn Tool Use Loop tests**

```python
def test_agent_loop_returns_tool_observation_to_runtime(hist001_bundle, trace_v2) -> None:
    runtime = ScriptedRuntime(
        [
            AgentTurnResponse(tool_calls=(ToolCall("c1", "list_evidence", {}),), output=None),
            AgentTurnResponse(
                tool_calls=(ToolCall("c2", "read_evidence", {"evidence_id": "hist-001-ev-0001"}),),
                output=None,
            ),
            AgentTurnResponse(
                tool_calls=(), output={"ledger": [{"evidence_id": "hist-001-ev-0001"}]}
            ),
        ]
    )
    output = run_tool_stage(
        stage="evidence_ledger",
        runtime=runtime,
        registry=build_frozen_evidence_registry(hist001_bundle),
        context=tool_context(),
        trace=trace_v2,
        max_turns=4,
        role="evidence-researcher",
        instruction="Build an evidence ledger from admitted records.",
        prior_output={},
        output_schema=LEDGER_SCHEMA,
        seed=7,
    )
    assert output["ledger"][0]["evidence_id"] == "hist-001-ev-0001"
    assert runtime.requests[1].observations[0].call_id == "c1"
    assert [row["event_type"] for row in trace_rows(trace_v2)] == [
        "model.called",
        "model.observed",
        "tool.called",
        "tool.observed",
        "model.called",
        "model.observed",
        "tool.called",
        "tool.observed",
        "model.called",
        "model.observed",
    ]


def test_agent_loop_stops_infinite_tool_requests(hist001_bundle, trace_v2) -> None:
    runtime = EndlessToolRuntime()
    with pytest.raises(ValueError, match="turn budget"):
        run_tool_stage(
            stage="evidence_ledger",
            runtime=runtime,
            registry=build_frozen_evidence_registry(hist001_bundle),
            context=tool_context(),
            trace=trace_v2,
            max_turns=3,
            role="evidence-researcher",
            instruction="Build an evidence ledger from admitted records.",
            prior_output={},
            output_schema=LEDGER_SCHEMA,
            seed=7,
        )
```

- [ ] **Step 2: Run and confirm missing agent protocol**

Run:

```powershell
python -m pytest tests/v2/test_agent_loop.py tests/v2/test_workflows.py -v
```

Expected: FAIL because `tracelane.runtime.agent` does not exist.

- [ ] **Step 3: Implement the runtime request/response protocol**

```python
@dataclass(frozen=True)
class AgentTurnRequest:
    run_id: str
    stage: str
    role: str
    instruction: str
    tool_specs: tuple[ToolSpec, ...]
    observations: tuple[ToolResult, ...]
    prior_output: Mapping[str, object]
    output_schema: Mapping[str, object]
    seed: int


@dataclass(frozen=True)
class AgentTurnResponse:
    tool_calls: tuple[ToolCall, ...]
    output: Mapping[str, object] | None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0


class ToolCapableRuntime(Protocol):
    runtime_id: str

    def complete_turn(self, request: AgentTurnRequest) -> AgentTurnResponse: ...


@dataclass(frozen=True)
class ContextPolicyV2:
    required_domains: tuple[str, ...]
    budget_chars: int


@dataclass(frozen=True)
class HarnessConfigV2:
    schema_id: str
    schema_version: str
    run_mode: Literal["frozen_eval", "live_research"]
    workflow_id: str
    context_policy: ContextPolicyV2
    recovery_policy: Literal["restart", "checkpoint"]
    max_agent_turns: int
    seed: int

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "HarnessConfigV2":
        validate_document("harness-config", value)
        policy = value["context_policy"]
        return cls(
            schema_id=str(value["schema_id"]),
            schema_version=str(value["schema_version"]),
            run_mode=str(value["run_mode"]),
            workflow_id=str(value["workflow_id"]),
            context_policy=ContextPolicyV2(
                required_domains=tuple(str(item) for item in policy["required_domains"]),
                budget_chars=int(policy["budget_chars"]),
            ),
            recovery_policy=str(value["recovery_policy"]),
            max_agent_turns=int(value["max_agent_turns"]),
            seed=int(value["seed"]),
        )
```

- [ ] **Step 4: Implement the bounded loop with paired trace events**

```python
def run_tool_stage(
    *,
    stage: str,
    runtime: ToolCapableRuntime,
    registry: ToolRegistry,
    context: ToolContext,
    trace: TraceRecorderV2,
    max_turns: int,
    role: str,
    instruction: str,
    prior_output: Mapping[str, object],
    output_schema: Mapping[str, object],
    seed: int,
) -> Mapping[str, object]:
    observations: list[ToolResult] = []
    for turn in range(1, max_turns + 1):
        request = AgentTurnRequest(
            trace.run_id,
            stage,
            role,
            instruction,
            registry.specs,
            tuple(observations),
            prior_output,
            output_schema,
            seed,
        )
        called = trace.emit(
            "model.called", {"turn": turn, "runtime_id": runtime.runtime_id}, stage=stage
        )
        response = runtime.complete_turn(request)
        trace.emit(
            "model.observed",
            {
                "turn": turn,
                "tool_call_count": len(response.tool_calls),
                "has_output": response.output is not None,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "cached_tokens": response.cached_tokens,
                "latency_ms": response.latency_ms,
            },
            stage=stage,
            causation_id=called.event_id,
            parent_span_id=called.span_id,
        )
        if response.tool_calls:
            for call in response.tool_calls:
                tool_called = trace.emit("tool.called", call.to_dict(), stage=stage)
                result = registry.execute(call, context)
                trace.emit(
                    "tool.observed",
                    result.to_dict(),
                    stage=stage,
                    correlation_id=call.call_id,
                    causation_id=tool_called.event_id,
                    parent_span_id=tool_called.span_id,
                )
                observations.append(result)
            continue
        if response.output is None:
            raise ValueError("runtime returned neither tool calls nor output")
        validate_json_schema(output_schema, response.output)
        return response.output
    raise ValueError(f"agent turn budget exceeded: {max_turns}")
```

- [ ] **Step 5: Load four explicit workflow arms**

Each workflow JSON contains exact ordered stages from this set:

```python
WORKFLOW_STAGES = {
    "direct": ("analyze_alternatives", "finalize"),
    "evidence_ledger": ("evidence_ledger", "analyze_alternatives", "finalize"),
    "evidence_ledger_counterargument": (
        "evidence_ledger",
        "analyze_alternatives",
        "counterargument",
        "finalize",
    ),
    "evidence_ledger_counterargument_scenarios": (
        "evidence_ledger",
        "analyze_alternatives",
        "counterargument",
        "scenario_branches",
        "finalize",
    ),
}
```

`load_workflow` validates ID, version, ordered stages, prompt template hashes,
maximum turns per stage, and output schema refs. The history stub requests
`list_evidence`, reads every admitted record once in stable ID order, and then
returns deterministic structured stage output.

- [ ] **Step 6: Run focused and v0.1 runtime tests**

Run:

```powershell
python -m pytest tests/v2/test_agent_loop.py tests/v2/test_workflows.py tests/test_orchestrator.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/runtime src/tracelane/history/workflows.py src/tracelane/schemas/v2 fixtures/v0.2 tests/v2
git commit -m "feat: add a bounded tool use loop"
```

---

### Task 10: Historical Report Contract, Orchestrator, and Markdown View

**Files:**
- Modify: `src/tracelane/history/contracts.py`
- Create: `src/tracelane/history/orchestrator.py`
- Create: `src/tracelane/history/report.py`
- Create: `src/tracelane/v2/checkpoint.py`
- Create: `src/tracelane/schemas/v2/research-report.schema.json`
- Create: `src/tracelane/schemas/v2/checkpoint.schema.json`
- Create: `tests/v2/test_history_orchestrator.py`
- Create: `tests/v2/test_history_report.py`

**Interfaces:**
- Produces: `ResearchClaim`, `StrategicAlternative`, `ScenarioBranch`, `ResearchReport`
- Produces: `run_history_case(...) -> HistoryRunResult`
- Produces: `render_research_report(report: ResearchReport) -> str`
- Produces: `CheckpointStoreV2.save/load/verify`

- [ ] **Step 1: Write failing report semantics and end-to-end stub tests**

```python
def test_report_distinguishes_fact_assumption_inference_scenario_and_unknown() -> None:
    report = ResearchReport.from_dict(valid_report_value())
    assert {claim.kind for claim in report.claims} == {
        "observed_fact",
        "assumption",
        "inference",
        "scenario",
        "unknown",
    }
    assert report.selected_strategy_id in {
        alternative.strategy_id for alternative in report.alternatives
    }


def test_history_stub_writes_json_markdown_trace_and_checkpoints(tmp_path: Path) -> None:
    result = run_hist001_stub(tmp_path)
    assert result.status == "completed"
    run_dir = tmp_path / "runs" / result.run_id
    assert (run_dir / "output/research-report.json").exists()
    assert (run_dir / "output/research-report.md").exists()
    assert (run_dir / "trace/events.jsonl").exists()
    assert len(list((run_dir / "checkpoints").glob("*.json"))) == 7
    report = ResearchReport.from_dict(read_json(run_dir / "output/research-report.json"))
    assert len(report.alternatives) >= 2
    assert len(report.scenario_branches) >= 3
```

- [ ] **Step 2: Run and confirm report/orchestrator failures**

Run:

```powershell
python -m pytest tests/v2/test_history_orchestrator.py tests/v2/test_history_report.py -v
```

Expected: FAIL because the report and orchestrator contracts do not exist.

- [ ] **Step 3: Implement the report domain types**

```python
ClaimKind = Literal["observed_fact", "assumption", "inference", "scenario", "unknown"]


@dataclass(frozen=True)
class ResearchClaim:
    claim_id: str
    kind: ClaimKind
    text: str
    evidence_ids: tuple[str, ...]
    parent_claim_ids: tuple[str, ...]
    confidence: Literal["low", "medium", "high"]


@dataclass(frozen=True)
class StrategicAlternative:
    strategy_id: str
    title: str
    actions: tuple[str, ...]
    required_resources: tuple[str, ...]
    constraints: tuple[str, ...]
    supporting_claim_ids: tuple[str, ...]
    failure_modes: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioBranch:
    branch_id: str
    condition: str
    consequence_claim_ids: tuple[str, ...]
    probability_label: Literal["less_likely", "plausible", "more_likely", "unknown"]


@dataclass(frozen=True)
class ResearchReport:
    case_id: str
    decision_context: str
    evidence_ledger: tuple[str, ...]
    unknowns: tuple[str, ...]
    alternatives: tuple[StrategicAlternative, ...]
    selected_strategy_id: str
    selection_rationale: str
    scenario_branches: tuple[ScenarioBranch, ...]
    counterarguments: tuple[str, ...]
    claims: tuple[ResearchClaim, ...]
    uncertainty_summary: str
    conclusion: str


@dataclass(frozen=True)
class HistoryRunResult:
    run_id: str
    run_dir: Path
    status: Literal["completed", "failed"]
    report_ref: ArtifactRef | None
    parent_run_id: str | None
    branch_id: str | None
```

`from_dict` validates the report schema, unique IDs, all cross-references,
minimum two alternatives, minimum three scenario branches, evidence references
against the frozen bundle, and selected strategy membership.

- [ ] **Step 4: Implement the history stage machine and v2 checkpoints**

```python
STAGE_ORDER = (
    "select_context",
    "evidence_ledger",
    "analyze_alternatives",
    "counterargument",
    "scenario_branches",
    "finalize",
)


def run(self) -> ResearchReport:
    state = self.checkpoints.load_state()
    if not state.completed_stages:
        root = self.checkpoints.save(
            "evidence_frozen",
            {
                "case_sha256": self.case_sha256,
                "bundle_sha256": self.bundle.bundle_sha256,
                "outputs": {},
            },
        )
        self.trace.emit(
            "checkpoint.saved",
            {
                "checkpoint_sha256": root.checkpoint_sha256,
                "checkpoint_sequence": root.sequence,
            },
            stage="evidence_frozen",
        )
        state = self.checkpoints.load_state()
    for stage in ("select_context", *self.workflow.stages):
        if stage in state.completed_stages:
            continue
        self.trace.emit("stage.started", {}, stage=stage)
        if stage == "select_context":
            output = select_context(self.bundle, self.config.context_policy)
            self.trace.emit(
                "context.selected",
                {
                    "admitted_evidence_ids": output["admitted_evidence_ids"],
                    "omitted_evidence_ids": output["omitted_evidence_ids"],
                },
                stage=stage,
            )
            self.registry = build_frozen_evidence_registry(
                self.bundle.subset(tuple(output["admitted_evidence_ids"]))
            )
        else:
            output = run_tool_stage(
                stage=stage,
                runtime=self.runtime,
                registry=self.registry,
                context=self.tool_context,
                trace=self.trace,
                max_turns=self.workflow.max_turns[stage],
                role=self.workflow.roles[stage],
                instruction=self.workflow.instructions[stage],
                prior_output=state.outputs,
                output_schema=self.workflow.output_schemas[stage],
                seed=self.config.seed,
            )
        state = state.with_output(stage, output)
        checkpoint = self.checkpoints.save(stage, state.to_dict())
        self.trace.emit(
            "checkpoint.saved",
            {
                "checkpoint_sha256": checkpoint.checkpoint_sha256,
                "checkpoint_sequence": checkpoint.sequence,
            },
            stage=stage,
        )
        self.trace.emit("stage.completed", {"output_sha256": sha256_json(output)}, stage=stage)
    return ResearchReport.from_dict(state.outputs["finalize"], bundle=self.bundle)
```

`CheckpointStoreV2` carries the v2 execution fingerprint, hash chain,
completed stages, state hash, previous checkpoint hash, optional parent run,
and branch/change IDs. It uses the same atomic and path-containment rules as
Task 2.

- [ ] **Step 5: Implement Markdown as a pure generated view**

```python
def render_research_report(report: ResearchReport) -> str:
    ledger = "\n".join(f"- `{evidence_id}`" for evidence_id in report.evidence_ledger)
    unknowns = "\n".join(f"- {item}" for item in report.unknowns)
    alternatives = "\n".join(
        f"### {item.title}\n\n"
        + "\n".join(f"- Action: {action}" for action in item.actions)
        + "\n"
        + "\n".join(f"- Constraint: {constraint}" for constraint in item.constraints)
        + "\n"
        + "\n".join(f"- Failure mode: {failure}" for failure in item.failure_modes)
        for item in report.alternatives
    )
    scenarios = "\n".join(
        f"- `{branch.branch_id}` If {branch.condition} "
        f"({branch.probability_label}): {', '.join(branch.consequence_claim_ids)}"
        for branch in report.scenario_branches
    )
    counterarguments = "\n".join(f"- {item}" for item in report.counterarguments)
    citations = {
        claim.claim_id: " ".join(f"[{evidence_id}]" for evidence_id in claim.evidence_ids)
        for claim in report.claims
    }
    return (
        f"# HIST-001 Research Report\n\n"
        f"## Decision context\n\n{report.decision_context}\n\n"
        f"## Evidence ledger\n\n{ledger}\n\n"
        f"## Known unknowns\n\n{unknowns}\n\n"
        f"## Alternatives\n\n{alternatives}\n\n"
        f"## Selected strategy\n\n{report.selection_rationale}\n\n"
        f"## Scenario branches\n\n{scenarios}\n\n"
        f"## Counterarguments\n\n{counterarguments}\n\n"
        f"## Uncertainty\n\n{report.uncertainty_summary}\n\n"
        f"## Conclusion\n\n{report.conclusion}\n\n"
        f"## Claim references\n\n"
        + "\n".join(f"- `{claim_id}` {refs}" for claim_id, refs in citations.items())
        + "\n"
    )
```

The renderer performs no scoring and introduces no facts or numbers absent from
the JSON report.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
python -m pytest tests/v2/test_history_orchestrator.py tests/v2/test_history_report.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/history src/tracelane/v2/checkpoint.py src/tracelane/schemas/v2 tests/v2
git commit -m "feat: run structured historical research"
```

---

### Task 11: Hard Graders, Research Metrics, and Fault Fixtures

**Files:**
- Create: `src/tracelane/history/graders.py`
- Create: `src/tracelane/schemas/v2/grader-set.schema.json`
- Create: `src/tracelane/schemas/v2/grade-report.schema.json`
- Create: `fixtures/v0.2/history/hist-001/faults/future-leakage.json`
- Create: `fixtures/v0.2/history/hist-001/faults/logistics-context-omission.json`
- Create: `fixtures/v0.2/history/hist-001/faults/ambiguous-source-contract.json`
- Create: `tests/v2/test_history_graders.py`
- Create: `tests/v2/test_fault_fixtures.py`

**Interfaces:**
- Produces: `MetricResult`
- Produces: `HistoryGradeReport`
- Produces: `grade_history_run(run_dir: Path) -> HistoryGradeReport`
- Produces: `apply_fault_fixture(case_root: Path, fault: FaultFixture) -> FaultedInput`

- [ ] **Step 1: Write failing hard-grader and planted-fault tests**

```python
def test_clean_hist001_passes_all_hard_graders(tmp_path: Path) -> None:
    report = grade_history_run(run_hist001_stub(tmp_path).run_dir)
    by_id = {metric.metric_id: metric for metric in report.metrics}
    assert by_id["temporal_integrity"].value == 1.0
    assert by_id["citation_validity"].value == 1.0
    assert by_id["intervention_compliance"].value == 1.0
    assert by_id["claim_type_validity"].value == 1.0
    assert by_id["artifact_integrity"].value == 1.0
    assert report.hard_passed is True


@pytest.mark.parametrize(
    ("fault_id", "failed_metric"),
    [
        ("future-leakage", "temporal_integrity"),
        ("logistics-context-omission", "evidence_coverage"),
        ("ambiguous-source-contract", "source_contract_validity"),
    ],
)
def test_fault_fixture_fails_expected_metric(
    tmp_path: Path, fault_id: str, failed_metric: str
) -> None:
    result = run_faulted_hist001(tmp_path, fault_id)
    report = grade_history_run(result.run_dir)
    assert (
        next(metric for metric in report.metrics if metric.metric_id == failed_metric).passed
        is False
    )
```

- [ ] **Step 2: Run and confirm graders/faults are missing**

Run:

```powershell
python -m pytest tests/v2/test_history_graders.py tests/v2/test_fault_fixtures.py -v
```

Expected: FAIL because history graders and fault fixture files do not exist.

- [ ] **Step 3: Implement a uniform metric result**

```python
@dataclass(frozen=True)
class MetricResult:
    grader_id: str
    grader_version: str
    metric_id: str
    value: float
    unit: str
    passed: bool
    threshold: float | None
    evidence_refs: tuple[str, ...]
    reason_code: str
    explanation: str


@dataclass(frozen=True)
class HistoryGradeReport:
    run_id: str
    metrics: tuple[MetricResult, ...]
    hard_passed: bool
    score: float

    def to_dict(self) -> dict[str, object]:
        value = json.loads(canonical_json(self))
        validate_document("grade-report", value)
        return value


@dataclass(frozen=True)
class GraderSet:
    grader_set_id: str
    grader_set_version: str
    hard_metric_ids: tuple[str, ...]
    quality_metric_ids: tuple[str, ...]
    cost_metric_ids: tuple[str, ...]
    rubric_refs: tuple[ArtifactRef, ...]
```

- [ ] **Step 4: Implement deterministic hard graders**

```python
HARD_METRICS = (
    "temporal_integrity",
    "citation_validity",
    "intervention_compliance",
    "claim_type_validity",
    "artifact_integrity",
    "source_contract_validity",
)


def grade_temporal_integrity(report: ResearchReport, bundle: FrozenHistoryBundle) -> MetricResult:
    rejected = set(bundle.rejected_future_ids)
    violations = sorted(
        {
            evidence_id
            for claim in report.claims
            for evidence_id in claim.evidence_ids
            if evidence_id in rejected
        }
    )
    return metric(
        "temporal_integrity",
        1.0 if not violations else 0.0,
        passed=not violations,
        evidence_refs=tuple(violations),
        reason_code="ok" if not violations else "future_evidence_cited",
        explanation="No post-cutoff evidence was cited."
        if not violations
        else f"Post-cutoff evidence was cited: {', '.join(violations)}",
    )
```

`intervention_compliance` rejects report actions that cross the Niemen or launch
the Russian campaign; `citation_validity` resolves every evidence ID;
`claim_type_validity` enforces citation rules (`observed_fact` requires
evidence; `assumption`, `scenario`, and `unknown` may be uncited);
`artifact_integrity` calls `validate_run`; `source_contract_validity` checks
date precision, availability, locator, license, and provenance hash.

- [ ] **Step 5: Implement research-quality and cost metrics**

Deterministic research metrics are:

```text
evidence_faithfulness
evidence_coverage
causal_coherence
alternative_quality
constraint_preservation
counterargument_quality
uncertainty_calibration
report_completeness
model_calls
tool_calls
input_tokens
output_tokens
latency_ms
```

Each metric has a documented numerator and denominator. For example,
`evidence_coverage` is the number of `required_domains` represented by cited
facts divided by total required domains; `causal_coherence` is the fraction of
inference/scenario claims with at least one valid `parent_claim_id`;
`alternative_quality` passes only when at least two alternatives have distinct
action sets and each has resources, constraints, and failure modes.

The aggregate `score` is the unweighted mean of the eight research-quality
metrics only when every hard metric passes; otherwise it is `0.0`. Costs are
reported separately and never hidden inside the quality score.

`grade_history_run` also accepts a failed run with no research report. It grades
input, trace, constraint, and artifact integrity first; unavailable
research-quality metrics receive value `0.0`, `passed=False`, and reason code
`report_not_produced`. This is how a future-evidence admission attempt or an
invalid tool result remains diagnosable without publishing an invalid report.

- [ ] **Step 6: Define faults as data-only patches**

```json
{
  "schema_id": "tracelane://schemas/fault-fixture/v2",
  "schema_version": "2.0.0",
  "fault_id": "logistics-context-omission",
  "target_failure_type": "under_specified_intent",
  "expected_responsible_layer": "context_policy",
  "operations": [
    {
      "op": "remove",
      "target": "/context_policy/required_domains",
      "value": "logistics"
    }
  ]
}
```

The future leakage fault attempts to add the rejected future evidence ref to an
admitted context manifest; the hard PIT gate stops the run before model use and
records the attempted ref. The ambiguous source fault removes `date_precision`
from one tool result contract; output-schema validation stops the stage. The
fixture applier supports only `add`, `remove`, and `replace` on an explicit
whitelist and never edits source files. For set-like arrays, `add` and `remove`
carry an explicit `value`, reject duplicates/missing members, and canonicalize
the resulting order.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
python -m pytest tests/v2/test_history_graders.py tests/v2/test_fault_fixtures.py -v
```

Expected: all selected tests PASS; the clean run passes every hard metric and
each planted fault fails its declared metric.

- [ ] **Step 8: Commit**

```powershell
git add src/tracelane/history/graders.py src/tracelane/schemas/v2 fixtures/v0.2 tests/v2
git commit -m "feat: grade historical research traces"
```

---

### Task 12: Constraint Log and First Critical Failure Diagnosis

**Files:**
- Create: `src/tracelane/diagnosis/__init__.py`
- Create: `src/tracelane/diagnosis/contracts.py`
- Create: `src/tracelane/diagnosis/diagnoser.py`
- Create: `src/tracelane/schemas/v2/constraint-violation.schema.json`
- Create: `src/tracelane/schemas/v2/diagnosis.schema.json`
- Create: `tests/v2/test_diagnosis.py`

**Interfaces:**
- Produces: `ConstraintViolation`
- Produces: `Diagnosis`
- Produces: `diagnose_run(run_dir: Path, rubric: DiagnosisRubric) -> Diagnosis`
- Produces: `write_diagnosis(run_dir: Path, diagnosis: Diagnosis) -> ArtifactRef`

- [ ] **Step 1: Write failing planted-fault and no-op diagnosis tests**

```python
@pytest.mark.parametrize(
    ("fault_id", "failure_type", "responsible_layer"),
    [
        ("future-leakage", "temporal_leakage", "evidence_data"),
        ("logistics-context-omission", "under_specified_intent", "context_policy"),
        ("ambiguous-source-contract", "invalid_invocation", "tool_schema"),
    ],
)
def test_diagnoser_finds_expected_first_critical_failure(
    tmp_path: Path, fault_id: str, failure_type: str, responsible_layer: str
) -> None:
    result = run_faulted_hist001(tmp_path, fault_id)
    diagnosis = diagnose_run(result.run_dir, hist001_diagnosis_rubric())
    assert diagnosis.failure_type == failure_type
    assert diagnosis.responsible_layer == responsible_layer
    assert diagnosis.critical_sequence == min(diagnosis.evidence_sequences)
    assert diagnosis.review_status == "fixture_calibrated"


def test_clean_run_returns_no_failure_instead_of_inventing_repair(tmp_path: Path) -> None:
    result = run_hist001_stub(tmp_path)
    diagnosis = diagnose_run(result.run_dir, hist001_diagnosis_rubric())
    assert diagnosis.failure_type == "no_failure"
    assert diagnosis.responsible_layer == "none"
    assert diagnosis.critical_sequence is None
```

- [ ] **Step 2: Run and confirm missing diagnoser**

Run:

```powershell
python -m pytest tests/v2/test_diagnosis.py -v
```

Expected: FAIL during import of `tracelane.diagnosis`.

- [ ] **Step 3: Implement violation and diagnosis contracts**

```python
FailureType = Literal[
    "no_failure",
    "plan_adherence_failure",
    "invented_information",
    "invalid_invocation",
    "misinterpreted_tool_output",
    "intent_plan_misalignment",
    "under_specified_intent",
    "unsupported_intent",
    "guardrail_triggered",
    "system_failure",
    "temporal_leakage",
    "unsupported_causal_claim",
    "counterfactual_constraint_violation",
]
ResponsibleLayer = Literal[
    "none",
    "prompt",
    "context_policy",
    "tool_schema",
    "workflow",
    "memory_state",
    "recovery_policy",
    "runtime_model",
    "evidence_data",
    "evaluation",
]


@dataclass(frozen=True)
class ConstraintViolation:
    violation_id: str
    run_id: str
    trace_sequence: int
    constraint_id: str
    failure_type: FailureType
    responsible_layer: ResponsibleLayer
    evidence_refs: tuple[str, ...]
    recoverable_without_change: bool
    reason_code: str


@dataclass(frozen=True)
class Diagnosis:
    diagnosis_id: str
    run_id: str
    critical_sequence: int | None
    failure_type: FailureType
    responsible_layer: ResponsibleLayer
    evidence_sequences: tuple[int, ...]
    constraint_violation_ids: tuple[str, ...]
    explanation: str
    confidence: float
    review_status: Literal["unreviewed", "fixture_calibrated", "human_reviewed"]


@dataclass(frozen=True)
class DiagnosisRubric:
    rubric_id: str
    version: str
    reason_explanations: Mapping[str, str]
    expected_fixture_labels: Mapping[str, tuple[FailureType, ResponsibleLayer]]
    review_status: Literal["unreviewed", "fixture_calibrated", "human_reviewed"]

    @property
    def has_fixture_label(self) -> bool:
        return bool(self.expected_fixture_labels)

    def explanation_for(self, reason_code: str) -> str:
        return self.reason_explanations.get(reason_code, "Registered constraint failure.")
```

- [ ] **Step 4: Emit violations from deterministic constraint checks**

Each hard grader writes one `constraint.checked` event. Failed constraints also
write `violation.detected` and append the same validated object to
`diagnosis/violations.jsonl`. The responsible-layer rules are:

```python
RESPONSIBILITY_RULES = {
    "future_evidence_admitted": ("temporal_leakage", "evidence_data"),
    "admitted_domain_omitted": ("under_specified_intent", "context_policy"),
    "tool_result_missing_date_precision": ("invalid_invocation", "tool_schema"),
    "report_cites_unknown_evidence": ("invented_information", "runtime_model"),
    "intervention_reversed": ("counterfactual_constraint_violation", "prompt"),
    "grader_reference_missing": ("system_failure", "evaluation"),
}
```

The event sequence is the earliest trace event that contains the invalid input
or decision, not the final grade sequence.

- [ ] **Step 5: Implement first-critical-failure selection**

```python
def diagnose_run(run_dir: Path, rubric: DiagnosisRubric) -> Diagnosis:
    trace = read_validated_trace(run_dir)
    violations = collect_violations(run_dir, trace, rubric)
    if not violations:
        return Diagnosis(
            diagnosis_id=make_diagnosis_id(run_dir.name, None, rubric.version),
            run_id=run_dir.name,
            critical_sequence=None,
            failure_type="no_failure",
            responsible_layer="none",
            evidence_sequences=(),
            constraint_violation_ids=(),
            explanation="No registered constraint failure was observed.",
            confidence=1.0,
            review_status=rubric.review_status,
        )
    ordered = sorted(violations, key=lambda item: (item.trace_sequence, item.violation_id))
    critical = next(
        (item for item in ordered if not item.recoverable_without_change),
        ordered[0],
    )
    related = tuple(item for item in ordered if item.failure_type == critical.failure_type)
    return Diagnosis(
        diagnosis_id=make_diagnosis_id(run_dir.name, critical.trace_sequence, rubric.version),
        run_id=run_dir.name,
        critical_sequence=critical.trace_sequence,
        failure_type=critical.failure_type,
        responsible_layer=critical.responsible_layer,
        evidence_sequences=tuple(item.trace_sequence for item in related),
        constraint_violation_ids=tuple(item.violation_id for item in related),
        explanation=rubric.explanation_for(critical.reason_code),
        confidence=1.0 if rubric.has_fixture_label else 0.75,
        review_status=rubric.review_status,
    )
```

- [ ] **Step 6: Run focused diagnosis and no-op tests**

Run:

```powershell
python -m pytest tests/v2/test_diagnosis.py tests/v2/test_fault_fixtures.py -v
```

Expected: all selected tests PASS; the three faults map to their declared layer
and the clean run yields `no_failure`.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/diagnosis src/tracelane/schemas/v2 tests/v2/test_diagnosis.py
git commit -m "feat: locate first critical failures"
```

---

### Task 13: Change Manifest and Checkpoint Suffix Replay

**Files:**
- Create: `src/tracelane/experiments/change.py`
- Create: `src/tracelane/experiments/replay.py`
- Create: `src/tracelane/schemas/v2/change-manifest.schema.json`
- Create: `tests/v2/test_change_manifest.py`
- Create: `tests/v2/test_replay.py`

**Interfaces:**
- Produces: `ChangeManifest`
- Produces: `diff_single_variable(control: Mapping, treatment: Mapping) -> ConfigDifference`
- Produces: `apply_approved_change(config: HarnessConfigV2, change: ChangeManifest) -> HarnessConfigV2`
- Produces: `replay_from_checkpoint(request: ReplayRequest) -> HistoryRunResult`

- [ ] **Step 1: Write failing single-variable and branch-lineage tests**

```python
def test_change_manifest_rejects_multiple_config_differences() -> None:
    change = approved_change(
        target="/context_policy/required_domains",
        control_value=["diplomacy"],
        treatment_value=["diplomacy", "logistics"],
    )
    control = harness_config(required_domains=("diplomacy",), workflow_id="direct")
    treatment = harness_config(
        required_domains=("diplomacy", "logistics"),
        workflow_id="evidence-ledger",
    )
    with pytest.raises(ValueError, match="exactly one variable"):
        validate_change_against_configs(change, control, treatment)


def test_replay_branches_after_failure_checkpoint_and_preserves_prefix(tmp_path: Path) -> None:
    parent = run_faulted_hist001(tmp_path, "logistics-context-omission")
    diagnosis = diagnose_run(parent.run_dir, hist001_diagnosis_rubric())
    checkpoint = checkpoint_before_sequence(parent.run_dir, diagnosis.critical_sequence)
    assert checkpoint.stage == "evidence_frozen"
    treatment = replay_from_checkpoint(replay_request(parent, checkpoint, approved_context_fix()))
    assert treatment.parent_run_id == parent.run_id
    assert treatment.branch_id
    lineage = read_json(treatment.run_dir / "input/lineage.json")
    assert lineage["prefix_sha256"] == hash_trace_through_checkpoint(parent.run_dir, checkpoint)
    assert read_trace(treatment.run_dir)[0]["event_type"] == "replay.started"
    assert (
        read_json(treatment.run_dir / "input/lineage.json")["change_id"]
        == approved_context_fix().change_id
    )
```

- [ ] **Step 2: Run and confirm missing change/replay modules**

Run:

```powershell
python -m pytest tests/v2/test_change_manifest.py tests/v2/test_replay.py -v
```

Expected: FAIL because `change.py` and `replay.py` do not exist.

- [ ] **Step 3: Implement a pre-registered Change Manifest**

```python
@dataclass(frozen=True)
class ChangeManifest:
    change_id: str
    parent_experiment_id: str
    target_failure_type: str
    target_responsible_layer: str
    hypothesis: str
    target_json_pointer: str
    control_value: object
    treatment_value: object
    expected_metric_changes: Mapping[str, float]
    allowed_regressions: Mapping[str, float]
    risk: str
    rollback_condition: str
    approval_status: Literal["proposed", "approved", "rejected"]
    approved_by: str | None
    approved_at: datetime | None


ALLOWED_CHANGE_POINTERS = frozenset(
    {
        "/context_policy/required_domains",
        "/context_policy/budget_chars",
        "/workflow_id",
        "/tool_contracts/read_evidence/required",
        "/prompts/evidence_ledger",
        "/recovery_policy",
    }
)


@dataclass(frozen=True)
class ConfigDifference:
    pointer: str
    before: object
    after: object


@dataclass(frozen=True)
class ReplayRequest:
    parent_run_dir: Path
    checkpoint_ref: ArtifactRef
    parent_checkpoint_sha256: str
    case: HistoryCase
    bundle: FrozenHistoryBundle
    control_config: HarnessConfigV2
    runtime: ToolCapableRuntime
    artifacts_root: Path
    repeat: int
    change: ChangeManifest | None
```

`from_dict` rejects observations and decisions in a pre-run manifest. After
comparison, a separate immutable `ChangeOutcome` stores observed deltas,
prediction result, regressions, and promote/reject/retry decision.

- [ ] **Step 4: Implement structural one-variable diff and approved patching**

```python
def diff_single_variable(
    control: Mapping[str, object], treatment: Mapping[str, object]
) -> ConfigDifference:
    differences = json_pointer_diff(
        json.loads(canonical_json(control)),
        json.loads(canonical_json(treatment)),
    )
    if len(differences) != 1:
        raise ValueError(f"expected exactly one variable difference, found {len(differences)}")
    difference = differences[0]
    if difference.pointer not in ALLOWED_CHANGE_POINTERS:
        raise ValueError(f"change target is not allowlisted: {difference.pointer}")
    return difference


def apply_approved_change(config: HarnessConfigV2, change: ChangeManifest) -> HarnessConfigV2:
    if change.approval_status != "approved" or change.approved_by is None:
        raise ValueError("change must be explicitly approved")
    value = json.loads(canonical_json(config))
    patched = replace_at_pointer(value, change.target_json_pointer, change.treatment_value)
    difference = diff_single_variable(value, patched)
    if difference.before != change.control_value or difference.after != change.treatment_value:
        raise ValueError("change manifest does not match config values")
    return HarnessConfigV2.from_dict(patched)
```

- [ ] **Step 5: Implement trusted prefix and live suffix replay**

```python
def replay_from_checkpoint(request: ReplayRequest) -> HistoryRunResult:
    validate_run(request.parent_run_dir)
    checkpoint = CheckpointStoreV2(request.parent_run_dir).load(request.checkpoint_ref)
    if checkpoint.checkpoint_sha256 != request.parent_checkpoint_sha256:
        raise ValueError("parent checkpoint hash mismatch")
    config = (
        apply_approved_change(request.control_config, request.change)
        if request.change is not None
        else request.control_config
    )
    change_id = request.change.change_id if request.change is not None else "control_noop"
    branch_id = sha256_json(
        {
            "parent_run_id": request.parent_run_dir.name,
            "checkpoint_sha256": checkpoint.checkpoint_sha256,
            "change_id": change_id,
            "repeat": request.repeat,
        }
    )[:24]
    return run_history_case(
        case=request.case,
        bundle=request.bundle,
        config=config,
        runtime=request.runtime,
        artifacts_root=request.artifacts_root,
        repeat=request.repeat,
        initial_state=checkpoint.state,
        completed_stages=checkpoint.completed_stages,
        parent_run_id=request.parent_run_dir.name,
        branch_id=branch_id,
        change_id=change_id,
        replay_mode="suffix_live",
    )
```

The child trace begins with `replay.started` and a `prefix_ref`; it does not
copy parent trace rows into the child. Prefix equality is verified by hashes
and references, while post-checkpoint model/tool calls execute again.

- [ ] **Step 6: Run focused replay and integrity tests**

Run:

```powershell
python -m pytest tests/v2/test_change_manifest.py tests/v2/test_replay.py tests/v2/test_manifests.py -v
```

Expected: all selected tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/experiments/change.py src/tracelane/experiments/replay.py src/tracelane/schemas/v2 tests/v2
git commit -m "feat: replay approved harness changes"
```

---

### Task 14: Five-Repeat Control/Treatment Experiments and Comparison

**Files:**
- Create: `src/tracelane/experiments/v2_runner.py`
- Create: `src/tracelane/schemas/v2/experiment-manifest.schema.json`
- Create: `src/tracelane/schemas/v2/comparison.schema.json`
- Create: `tests/v2/test_experiment_v2.py`
- Create: `tests/v2/test_comparison.py`

**Interfaces:**
- Produces: `ExperimentManifest`
- Produces: `run_paired_experiment(spec: ExperimentSpec) -> ExperimentResult`
- Produces: `compare_arms(experiment_dir: Path) -> Comparison`
- Produces: `render_harness_report(comparison: Comparison) -> str`

- [ ] **Step 1: Write failing repeat isolation and comparison tests**

```python
def test_experiment_writes_five_run_refs_per_arm_without_copying_runs(tmp_path: Path) -> None:
    result = run_logistics_context_experiment(tmp_path, repeats=5)
    experiment_dir = result.experiment_dir
    assert len(list((experiment_dir / "arms/control/repeats").glob("*/run-ref.json"))) == 5
    assert len(list((experiment_dir / "arms/treatment/repeats").glob("*/run-ref.json"))) == 5
    assert len(list((tmp_path / "runs").iterdir())) == 11
    assert not (experiment_dir / "arms/control/runs").exists()
    parent_ids = {
        read_json(path)["parent_run_id"]
        for path in (experiment_dir / "arms").glob("*/repeats/*/run-ref.json")
    }
    assert parent_ids == {result.parent_run_id}


def test_comparison_reports_paired_deltas_failures_cost_and_decision(tmp_path: Path) -> None:
    result = run_logistics_context_experiment(tmp_path, repeats=5)
    comparison = compare_arms(result.experiment_dir)
    assert len(comparison.paired_results) == 5
    assert comparison.metrics["evidence_coverage"].improved_pairs >= 4
    assert comparison.failure_type_counts["treatment"].get("under_specified_intent", 0) == 0
    assert "input_tokens" in comparison.cost_deltas
    assert comparison.decision == "promote"
```

- [ ] **Step 2: Run and confirm missing v2 experiment runner**

Run:

```powershell
python -m pytest tests/v2/test_experiment_v2.py tests/v2/test_comparison.py -v
```

Expected: FAIL because `v2_runner.py` does not exist.

- [ ] **Step 3: Implement pre-registered experiment identity**

```python
@dataclass(frozen=True)
class ExperimentBudget:
    maximum_model_calls: int
    maximum_tool_calls: int
    maximum_total_tokens: int


@dataclass(frozen=True)
class ExperimentSpec:
    research_question: str
    preregistered_hypothesis: str
    suite_ref: ArtifactRef
    independent_variable: str
    control_config: HarnessConfigV2
    treatment_configs: tuple[HarnessConfigV2, ...]
    repeats: int
    primary_metrics: tuple[str, ...]
    guardrail_metrics: tuple[str, ...]
    stopping_rule: str
    budget: ExperimentBudget
    change_manifest: ChangeManifest
    fault_fixture: FaultFixture
    diagnosis_rubric: DiagnosisRubric
    code_revision: str

    @property
    def experiment_id(self) -> str:
        return sha256_json(
            {
                "research_question": self.research_question,
                "hypothesis": self.preregistered_hypothesis,
                "suite_ref": self.suite_ref,
                "independent_variable": self.independent_variable,
                "control": self.control_config,
                "treatments": self.treatment_configs,
                "repeats": self.repeats,
                "metrics": self.primary_metrics,
                "guardrails": self.guardrail_metrics,
                "change_id": self.change_manifest.change_id,
                "code_revision": self.code_revision,
            }
        )[:24]


@dataclass(frozen=True)
class ExperimentResult:
    experiment_id: str
    experiment_dir: Path
    parent_run_id: str


@dataclass(frozen=True)
class ExperimentManifest:
    experiment_id: str
    lifecycle_status: Literal["registered", "running", "completed", "failed"]
    research_question: str
    preregistered_hypothesis: str
    suite_ref: ArtifactRef
    independent_variable: str
    parent_run_id: str | None
    parent_checkpoint_sha256: str | None
    repeat_plan: tuple[int, ...]
    primary_metrics: tuple[str, ...]
    guardrail_metrics: tuple[str, ...]
    change_manifest_ref: ArtifactRef
    run_refs: tuple[ArtifactRef, ...]
    comparison_ref: ArtifactRef | None
    code_revision: str


@dataclass(frozen=True)
class PairedResult:
    repeat: int
    control_run_id: str
    treatment_run_id: str
    metric_deltas: Mapping[str, float]
    has_guardrail_regression: bool

    def delta(self, metric_id: str) -> float:
        return float(self.metric_deltas[metric_id])


@dataclass(frozen=True)
class MetricSummary:
    values: tuple[float, ...]
    mean: float
    minimum: float
    maximum: float
    population_stdev: float


@dataclass(frozen=True)
class MetricComparison:
    control: MetricSummary
    treatment: MetricSummary
    paired_deltas: tuple[float, ...]
    improved_pairs: int


@dataclass(frozen=True)
class Comparison:
    experiment_id: str
    change_id: str
    paired_results: tuple[PairedResult, ...]
    metrics: Mapping[str, MetricComparison]
    failure_type_counts: Mapping[str, Mapping[str, int]]
    cost_deltas: Mapping[str, float]
    new_failure_count: int
    decision: Literal["promote", "reject", "inconclusive"]
```

`ExperimentSpec` requires exactly five repeats for the HIST-001 v0.2 report,
one control, one treatment, paired repeat IDs 1–5, and one structural config
difference matching the approved Change Manifest.

- [ ] **Step 4: Branch both arms from one diagnosed parent checkpoint**

```python
parent = run_history_case(
    case,
    bundle,
    spec.control_config,
    runtime_factory("parent", 0),
    artifacts_root,
    repeat=1,
)
diagnosis = diagnose_run(parent.run_dir, spec.diagnosis_rubric)
checkpoint = checkpoint_before_sequence(parent.run_dir, diagnosis.critical_sequence)
if checkpoint.stage != "evidence_frozen":
    raise ValueError("context repair must branch from evidence_frozen checkpoint")

for arm_name, change in (("control", None), ("treatment", spec.change_manifest)):
    for repeat in range(1, spec.repeats + 1):
        result = replay_from_checkpoint(
            ReplayRequest(
                parent_run_dir=parent.run_dir,
                checkpoint_ref=checkpoint.ref,
                parent_checkpoint_sha256=checkpoint.checkpoint_sha256,
                case=case,
                bundle=bundle,
                control_config=spec.control_config,
                runtime=runtime_factory(arm_name, repeat),
                artifacts_root=artifacts_root,
                repeat=repeat,
                change=change,
            )
        )
        write_json(
            experiment_dir / f"arms/{arm_name}/repeats/{repeat:04d}/run-ref.json",
            {
                "run_id": result.run_id,
                "uri": f"tracelane://artifacts/runs/{result.run_id}/manifest.json",
                "repeat": repeat,
                "arm": arm_name,
                "parent_run_id": parent.run_id,
                "parent_checkpoint_sha256": checkpoint.checkpoint_sha256,
            },
        )
```

The experiment manifest is written before the first run with status
`registered`, changes to `running`, and becomes `completed` only after every
run ref, grade report, diagnosis, and comparison validates.
`build_hist001_demo_spec` applies the selected data-only Fault Fixture before
registration, so `control_config` already contains the planted omission and
the Change Manifest's `control_value` matches that exact config. The runner
stores the Fault Fixture ref for provenance but never reapplies it during a
branch.

- [ ] **Step 5: Implement transparent paired comparison**

```python
def summarize(values: Sequence[float]) -> MetricSummary:
    return MetricSummary(
        values=tuple(values),
        mean=statistics.fmean(values),
        minimum=min(values),
        maximum=max(values),
        population_stdev=statistics.pstdev(values),
    )


def promotion_decision(
    pairs: Sequence[PairedResult], primary_metric: str, guardrails: Sequence[str]
) -> str:
    improved = sum(pair.delta(primary_metric) > 0 for pair in pairs)
    guardrail_regression = any(
        pair.delta(metric_id) < 0 for pair in pairs for metric_id in guardrails
    )
    if guardrail_regression:
        return "reject"
    if improved >= 4:
        return "promote"
    return "inconclusive"
```

The comparison stores every individual value, mean, range, population standard
deviation, paired delta, failure-type counts, target-failure recurrence,
new-failure count, model/tool calls, tokens, and latency. The Markdown harness
report renders these fields without recalculating them.

- [ ] **Step 6: Run focused experiment tests**

Run:

```powershell
python -m pytest tests/v2/test_experiment_v2.py tests/v2/test_comparison.py -v
```

Expected: all selected tests PASS and the deterministic logistics-context
treatment is promoted in at least four of five paired repeats.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/experiments/v2_runner.py src/tracelane/schemas/v2 tests/v2
git commit -m "feat: compare repeated harness interventions"
```

---

### Task 15: Training Exports and OpenTelemetry-Compatible Span Export

**Files:**
- Create: `src/tracelane/exporters/__init__.py`
- Create: `src/tracelane/exporters/training.py`
- Create: `src/tracelane/exporters/otel.py`
- Create: `src/tracelane/schemas/v2/trajectory-export.schema.json`
- Create: `src/tracelane/schemas/v2/preference-export.schema.json`
- Create: `src/tracelane/schemas/v2/reward-event.schema.json`
- Create: `tests/v2/test_training_export.py`
- Create: `tests/v2/test_otel_export.py`

**Interfaces:**
- Produces: `export_experiment(experiment_dir: Path, output_dir: Path) -> ExportSummary`
- Produces: `trace_to_otel_spans(run_dir: Path) -> tuple[Mapping[str, object], ...]`

- [ ] **Step 1: Write failing export safety and lineage tests**

```python
def test_training_export_has_trajectory_reward_and_preference_lineage(tmp_path: Path) -> None:
    experiment = run_logistics_context_experiment(tmp_path, repeats=5)
    summary = export_experiment(experiment.experiment_dir, tmp_path / "exports")
    trajectories = read_jsonl(summary.trajectories_path)
    rewards = read_jsonl(summary.rewards_path)
    preferences = read_jsonl(summary.preferences_path)
    assert {row["run_id"] for row in trajectories} == {row["run_id"] for row in rewards}
    assert all(row["experiment_id"] == experiment.experiment_id for row in preferences)
    assert all(row["chosen_run_id"] != row["rejected_run_id"] for row in preferences)


def test_exports_contain_no_secret_absolute_path_or_chain_of_thought(tmp_path: Path) -> None:
    experiment = run_logistics_context_experiment(tmp_path, repeats=5)
    summary = export_experiment(experiment.experiment_dir, tmp_path / "exports")
    payload = b"".join(path.read_bytes() for path in summary.paths)
    assert b"authorization" not in payload.lower()
    assert str(tmp_path).encode() not in payload
    assert b"chain_of_thought" not in payload
    assert b"reasoning_content" not in payload
```

- [ ] **Step 2: Run and confirm missing exporters**

Run:

```powershell
python -m pytest tests/v2/test_training_export.py tests/v2/test_otel_export.py -v
```

Expected: FAIL during import of `tracelane.exporters`.

- [ ] **Step 3: Implement one-row-per-run trajectory export**

```python
@dataclass(frozen=True)
class ExportSummary:
    trajectories_path: Path
    rewards_path: Path
    preferences_path: Path
    otel_spans_path: Path

    @property
    def paths(self) -> tuple[Path, ...]:
        return (
            self.trajectories_path,
            self.rewards_path,
            self.preferences_path,
            self.otel_spans_path,
        )


def trajectory_row(run_dir: Path) -> dict[str, object]:
    manifest = load_run_manifest(run_dir)
    trace = read_validated_trace(run_dir)
    grade = read_validated_json(run_dir / "output/grades.json", "grade-report")
    return {
        "schema_id": "tracelane://schemas/trajectory-export/v2",
        "schema_version": "2.0.0",
        "run_id": manifest.run_id,
        "case_id": read_case_id(manifest.case_ref),
        "runtime_id": read_runtime_id(manifest.runtime_config_ref),
        "workflow_id": read_workflow_id(manifest.harness_config_ref),
        "events": [training_safe_event(event) for event in trace],
        "output_ref": find_output_ref(manifest, "research_report"),
        "grade_report_ref": manifest.grade_report_ref,
        "parent_run_id": manifest.parent_run_id,
        "branch_id": manifest.branch_id,
    }
```

`training_safe_event` includes observable prompts/instructions, tool calls,
tool observations, public model output, costs, errors, and short
`decision_summary`; it rejects fields named `chain_of_thought`,
`reasoning_content`, credentials, and local paths.

- [ ] **Step 4: Implement metric rewards and conservative preferences**

```python
def reward_rows(run_id: str, grade: HistoryGradeReport) -> list[dict[str, object]]:
    return [
        {
            "schema_id": "tracelane://schemas/reward-event/v2",
            "schema_version": "2.0.0",
            "run_id": run_id,
            "grader_id": metric.grader_id,
            "grader_version": metric.grader_version,
            "metric_id": metric.metric_id,
            "reward": metric.value,
            "passed": metric.passed,
            "evidence_refs": list(metric.evidence_refs),
        }
        for metric in grade.metrics
    ]


def preference_row(pair: PairedResult, comparison: Comparison) -> dict[str, object] | None:
    if comparison.decision != "promote" or pair.has_guardrail_regression:
        return None
    return {
        "schema_id": "tracelane://schemas/preference-export/v2",
        "schema_version": "2.0.0",
        "experiment_id": comparison.experiment_id,
        "chosen_run_id": pair.treatment_run_id,
        "rejected_run_id": pair.control_run_id,
        "metric_deltas": pair.metric_deltas,
        "change_id": comparison.change_id,
    }
```

- [ ] **Step 5: Implement version-pinned OTel mapping**

```python
def event_to_span(event: TraceEventV2) -> dict[str, object]:
    return {
        "trace_id": event.trace_id,
        "span_id": event.span_id,
        "parent_span_id": event.parent_span_id,
        "name": f"tracelane.{event.event_type}",
        "start_time": event.recorded_at,
        "end_time": event.recorded_at,
        "attributes": {
            "gen_ai.operation.name": operation_name(event.event_type),
            "gen_ai.workflow.name": event.stage or "run",
            "tracelane.event_id": event.event_id,
            "tracelane.sequence": event.sequence,
            **event.attributes,
        },
    }
```

The export manifest pins `semantic_convention_version`; no OpenTelemetry SDK is
added as a core dependency.

- [ ] **Step 6: Run focused export tests**

Run:

```powershell
python -m pytest tests/v2/test_training_export.py tests/v2/test_otel_export.py tests/test_security.py -v
```

Expected: all selected tests PASS and every JSONL line validates independently.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/exporters src/tracelane/schemas/v2 tests/v2
git commit -m "feat: export trace rewards and preferences"
```

---

### Task 16: OpenAI-Compatible Hosted Model Runtime

**Files:**
- Create: `src/tracelane/runtime/openai_compatible.py`
- Create: `src/tracelane/schemas/local/openai-compatible.schema.json`
- Create: `src/tracelane/schemas/v2/runtime-config.schema.json`
- Create: `tests/v2/test_openai_compatible_runtime.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `ChatTransport.post_json(...) -> Mapping[str, object]`
- Produces: `OpenAICompatibleRuntime.complete_turn(request: AgentTurnRequest) -> AgentTurnResponse`
- Produces: `load_local_runtime_config(path: Path) -> tuple[HostedRuntimeConfig, str]`
- Produces: `HostedRuntimeConfig.to_public_dict() -> dict[str, object]`

- [ ] **Step 1: Write failing request, tool-call, output, and secret tests**

```python
def test_runtime_serializes_tools_and_parses_tool_call() -> None:
    transport = FakeChatTransport(tool_call_response())
    runtime = OpenAICompatibleRuntime(runtime_config(), "secret-key", transport)
    response = runtime.complete_turn(agent_request())
    sent = transport.requests[0]
    assert sent["url"] == "https://api.example/v1/chat/completions"
    assert sent["json"]["model"] == "example-model"
    assert sent["json"]["tools"][0]["function"]["name"] == "read_evidence"
    assert response.tool_calls[0].tool_name == "read_evidence"
    assert response.output is None


def test_runtime_parses_schema_constrained_final_output() -> None:
    runtime = OpenAICompatibleRuntime(
        runtime_config(),
        "secret-key",
        FakeChatTransport(final_json_response({"ledger": []})),
    )
    assert runtime.complete_turn(agent_request()).output == {"ledger": []}


def test_public_runtime_config_never_contains_api_key() -> None:
    config, api_key = load_local_runtime_config(private_runtime_file())
    public = config.to_public_dict()
    assert api_key == "test-secret"
    assert "api_key" not in canonical_json(public)
    assert public["credential_source"] == "local_private_config"
```

- [ ] **Step 2: Run and confirm missing hosted runtime**

Run:

```powershell
python -m pytest tests/v2/test_openai_compatible_runtime.py -v
```

Expected: FAIL because `openai_compatible.py` does not exist.

- [ ] **Step 3: Implement public runtime configuration**

```python
@dataclass(frozen=True)
class HostedRuntimeConfig:
    runtime_id: str
    base_url: str
    model: str
    credential_source: Literal["local_private_config"]
    timeout_seconds: float = 60.0
    max_retries: int = 2
    supports_json_schema: bool = True

    def to_public_dict(self) -> dict[str, object]:
        value = asdict(self)
        validate_document("runtime-config", value)
        return value
```

Implement the private loader as a narrow boundary:

```python
def load_local_runtime_config(path: Path) -> tuple[HostedRuntimeConfig, str]:
    value = read_json_object(path)
    validate_local_config("openai-compatible", value)
    api_key = str(value["api_key"])
    selected_model = str(value["default_model"])
    if selected_model not in value["models"]:
        raise ValueError("default model must be present in models")
    public = HostedRuntimeConfig(
        runtime_id=str(value["runtime_id"]),
        base_url=str(value["base_url"]),
        model=selected_model,
        credential_source="local_private_config",
        timeout_seconds=float(value["timeout_seconds"]),
        max_retries=int(value["max_retries"]),
        supports_json_schema=bool(value["supports_json_schema"]),
    )
    return public, api_key
```

The private schema requires the key to be non-empty but never includes its value
in validation errors. The caller immediately passes the returned secret to the
runtime constructor. It is never placed in a dataclass, exception string,
trace, manifest, hash, or artifact.

- [ ] **Step 4: Implement Chat Completions serialization and parsing**

```python
def complete_turn(self, request: AgentTurnRequest) -> AgentTurnResponse:
    payload = {
        "model": self.config.model,
        "messages": build_messages(request),
        "tools": [tool_to_openai(spec) for spec in request.tool_specs],
        "tool_choice": "auto",
        "temperature": 0,
        "seed": request.seed,
    }
    if self.config.supports_json_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": f"{request.stage}_output",
                "strict": True,
                "schema": request.output_schema,
            },
        }
    started = time.monotonic()
    value = self.transport.post_json(
        self.config.base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {self._api_key}"},
        value=payload,
        timeout_seconds=self.config.timeout_seconds,
    )
    return parse_chat_completion(value, latency_ms=int((time.monotonic() - started) * 1000))
```

`build_messages` uses a fixed Harness system instruction, puts frozen evidence
only inside delimited untrusted-data messages, includes prior structured output
and tool observations as JSON, and never asks for private reasoning. The parser
requires exactly one assistant choice, validates tool arguments as JSON, rejects
a response containing both tool calls and final output, validates usage values,
and performs bounded retry only for transport/429/5xx errors.

- [ ] **Step 5: Add an optional dependency marker without forcing an SDK**

The implementation uses `urllib.request`, so the base dependency list remains:

```toml
dependencies = ["jsonschema>=4.22,<5"]
```

Add a `hosted` optional group only for certificate compatibility testing:

```toml
[project.optional-dependencies]
hosted = ["certifi>=2025.1"]
```

The runtime works without installing `hosted`; when `certifi` is present it may
use that CA bundle.

- [ ] **Step 6: Run fake-transport tests**

Run:

```powershell
python -m pytest tests/v2/test_openai_compatible_runtime.py tests/v2/test_agent_loop.py -v
```

Expected: all selected tests PASS without a network call or API key.

- [ ] **Step 7: Commit**

```powershell
git add src/tracelane/runtime/openai_compatible.py src/tracelane/schemas/v2/runtime-config.schema.json tests/v2/test_openai_compatible_runtime.py pyproject.toml
git commit -m "feat: add an OpenAI compatible runtime"
```

---

### Task 17: CLI, End-to-End Demo, Documentation, and v0.2 Release Gate

**Files:**
- Modify: `src/tracelane/cli.py`
- Modify: `src/tracelane/__init__.py`
- Create: `src/tracelane/v2/validation.py`
- Create: `configs/runtime/openai-compatible.example.json`
- Create: `tests/v2/test_cli_v2.py`
- Create: `tests/v2/test_end_to_end.py`
- Create: `tests/v2/golden/hist001-stub-summary.json`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `.gitignore`

**Interfaces:**
- Produces CLI commands: `acquire`, `history-run`, `history-demo`, `validate`, `diagnose`, `replay`, `compare`, `export`, `migrate`
- Produces one deterministic offline HIST-001 experiment directory
- Produces v0.2.0 package metadata and documentation

- [ ] **Step 1: Write failing CLI and end-to-end tests**

```python
def test_history_demo_produces_both_reports_and_training_exports(tmp_path: Path, capsys) -> None:
    assert (
        main(
            [
                "history-demo",
                "--artifacts",
                str(tmp_path),
                "--case",
                "hist-001",
                "--fault",
                "logistics-context-omission",
                "--repair",
                "context-required-domains",
                "--repeats",
                "5",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    experiment_dir = next((tmp_path / "experiments").iterdir())
    assert "decision=promote" in output
    assert (experiment_dir / "comparison.json").exists()
    assert (experiment_dir / "harness-report.md").exists()
    assert (experiment_dir / "exports/trajectories.jsonl").exists()
    treatment_ref = next((experiment_dir / "arms/treatment/repeats").glob("*/run-ref.json"))
    treatment_run = read_json(treatment_ref)["run_id"]
    assert (tmp_path / "runs" / treatment_run / "output/research-report.md").exists()


def test_validate_returns_stable_machine_error(tmp_path: Path, capsys) -> None:
    run_dir = write_tampered_v2_run(tmp_path)
    assert main(["validate", "run", "--path", str(run_dir), "--json"]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["code"] == "artifact_integrity_failed"
    assert error["object_uri"].startswith("tracelane://artifacts/runs/")
    assert error["json_pointer"] == "/checksums"
```

- [ ] **Step 2: Run and confirm new commands are absent**

Run:

```powershell
python -m pytest tests/v2/test_cli_v2.py tests/v2/test_end_to_end.py -v
```

Expected: FAIL because argparse rejects `history-demo` and `validate`.

- [ ] **Step 3: Add v2 commands without changing v0.1 command behavior**

```python
history = subparsers.add_parser("history-demo", help="Run the offline HIST-001 research loop.")
history.add_argument("--artifacts", type=Path, required=True)
history.add_argument("--case", choices=["hist-001"], default="hist-001")
history.add_argument(
    "--fault",
    choices=["none", "future-leakage", "logistics-context-omission", "ambiguous-source-contract"],
    default="logistics-context-omission",
)
history.add_argument("--repair", choices=["none", "context-required-domains"], default="none")
history.add_argument("--repeats", type=int, choices=[5], default=5)

history_run = subparsers.add_parser("history-run", help="Run one HIST-001 research task.")
history_run.add_argument("--artifacts", type=Path, required=True)
history_run.add_argument("--runtime", choices=["stub", "openai-compatible"], required=True)
history_run.add_argument("--runtime-config", type=Path)
history_run.add_argument(
    "--workflow",
    choices=[
        "direct",
        "evidence-ledger",
        "evidence-ledger-counterargument",
        "evidence-ledger-counterargument-scenarios",
    ],
    default="evidence-ledger-counterargument-scenarios",
)

validate = subparsers.add_parser("validate", help="Validate a v2 artifact tree.")
validate.add_argument("kind", choices=["artifact", "run", "experiment", "suite"])
validate.add_argument("--path", type=Path, required=True)
validate.add_argument("--json", action="store_true")
```

Add analogous parsers for:

```text
acquire --source-url --title --document-date --date-precision --note-file --artifacts
diagnose --run --rubric
replay --run --checkpoint --change-manifest
compare --experiment
export --experiment --format training|otel
migrate v1-run --source --artifacts
```

`acquire` accepts a human- or Codex-curated note from a local file and records
its source URL as provenance metadata. It has no network option and never
fetches the URL.
`history-run --runtime openai-compatible` requires `--runtime-config`, resolves
the private file's `api_key` only in memory, and persists only
`HostedRuntimeConfig.to_public_dict()`.

- [ ] **Step 4: Implement one deterministic offline demonstration path**

```python
def _history_demo(args: argparse.Namespace) -> int:
    suite = packaged_v02_suite()
    entry = next(
        item
        for item in load_history_suite(suite, "development")
        if item.scenario_id == "hist-001/clean"
    )
    spec = build_hist001_demo_spec(
        entry=entry,
        fault_id=None if args.fault == "none" else args.fault,
        repair_id=None if args.repair == "none" else args.repair,
        repeats=args.repeats,
        artifacts_root=args.artifacts,
    )
    result = run_paired_experiment(spec)
    comparison = compare_arms(result.experiment_dir)
    export_experiment(result.experiment_dir, result.experiment_dir / "exports")
    print(
        f"experiment_id={result.experiment_id} decision={comparison.decision} "
        f"paired_repeats={len(comparison.paired_results)}"
    )
    return 0 if comparison.decision in {"promote", "inconclusive"} else 1
```

The golden summary normalizes timestamps and artifact-root paths but retains
run IDs, event counts, diagnosis, all metric values, costs, and decision.

- [ ] **Step 5: Document what is and is not a tool**

Update both READMEs with:

```text
Agent tools in frozen evaluation:
- list_evidence
- read_evidence

Evidence acquisition is an operator boundary:
- Codex or a human supplies a source URL and curated note.
- TraceLane records, binds, reviews, and freezes those bytes.
- The scored agent has no search or fetch tool.

TraceLane CLI commands are operator controls, not tools available to the Agent.
Graders, checkpoints, and artifact validation are Harness services, not model tools.
```

Add diagrams for acquisition/freeze and inner/outer loops, the exact artifact
tree, the HIST-001 demo command, scorer interpretation, and a caution that the
pilot does not establish universal Harness superiority.

The example runtime config contains no secret:

```json
{
  "schema_id": "tracelane://local-config/openai-compatible/v1",
  "schema_version": "1.0.0",
  "runtime_id": "volcengine-ark-coding-plan",
  "protocol": "openai-compatible",
  "base_url": "https://api.example/v1",
  "api_key": "replace-with-your-local-api-key",
  "models": ["replace-with-model-id"],
  "default_model": "replace-with-model-id",
  "timeout_seconds": 60.0,
  "max_retries": 2,
  "supports_json_schema": true
}
```

Add `.local/` to `.gitignore` so provider-specific local configuration cannot
be committed accidentally.

- [ ] **Step 6: Update version and changelog**

Set:

```python
# src/tracelane/__init__.py
__version__ = "0.2.0"
```

Set `version = "0.2.0"` in `pyproject.toml`. Move the v0.2 features from
`Unreleased` into `## [0.2.0] - 2026-07-24`, retain an empty `Unreleased`
section, and add the compare link for v0.2.0.

- [ ] **Step 7: Run the complete release gate**

Run:

```powershell
python scripts/build_hist001.py --verify fixtures/v0.2
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
python -m build
tracelane history-demo --artifacts artifacts/release-v02 --case hist-001 --fault logistics-context-omission --repair context-required-domains --repeats 5
$experimentDir = (Get-ChildItem artifacts/release-v02/experiments | Select-Object -First 1).FullName
tracelane validate experiment --path $experimentDir
```

Expected:

- fixture verification succeeds;
- Ruff reports no errors or formatting changes;
- all v0.1 and v0.2 tests pass;
- source distribution and wheel build;
- the demo prints `decision=promote paired_repeats=5`;
- experiment validation exits zero;
- the generated experiment contains two research-report formats, diagnosis,
  comparison, Harness report, trajectories, preferences, and reward events.

After the deterministic gate, copy the public template to
`.local/runtime.json`, set the actual provider URL, model ID, and rotated API
key in that ignored local file, and run exactly one hosted smoke test:

```powershell
tracelane history-run --artifacts artifacts/hosted-smoke --runtime openai-compatible --runtime-config .local/runtime.json --workflow evidence-ledger-counterargument-scenarios
$hostedRun = (Get-ChildItem artifacts/hosted-smoke/runs | Select-Object -First 1).FullName
tracelane validate run --path $hostedRun
```

Expected: the hosted run completes, uses both evidence tools, publishes a
schema-valid report, and passes all hard graders. If credentials are not
available, report v0.2 as “offline core verified; hosted acceptance pending”
instead of claiming the full completion criterion.

- [ ] **Step 8: Inspect the final diff and commit**

Run:

```powershell
git diff --check
git status --short
```

Expected: only intentional v0.2 code, fixtures, tests, and documentation are
modified; `git diff --check` produces no output.

Commit:

```powershell
git add src tests fixtures scripts configs README.md README.zh-CN.md CHANGELOG.md pyproject.toml .gitignore
git commit -m "release: prepare TraceLane v0.2.0"
```

Do not create or push a tag until the user reviews the generated research and
Harness reports.

---

## Design Coverage Matrix

| Approved design requirement | Implementation task |
|---|---|
| JSON Schema, stable IDs, hashes, URI references | 1 |
| Content-addressed blobs and safe paths | 2 |
| Run manifests, checksums, v1 import compatibility | 3 |
| Typed, redacted, parent/causation-aware traces | 4 |
| Manual Codex/human acquisition isolated from frozen evaluation | 5 |
| Historical dates, provenance, manifests, scenario splits | 6 |
| Approved HIST-001 evidence pack and future control | 7 |
| Actual Agent tools with PIT enforcement | 8 |
| Bounded Tool Use Loop and four workflow arms | 9 |
| Structured counterfactual report, checkpoints, Markdown view | 10 |
| Hard, quality, reliability, and cost graders | 11 |
| First Critical Failure and no-op diagnosis | 12 |
| One-variable approved repair and suffix replay | 13 |
| Five paired Control/Treatment repeats and comparison | 14 |
| Trajectory, reward, preference, and OTel exports | 15 |
| One real hosted-model protocol | 16 |
| CLI, end-to-end demo, docs, packaging, release gate | 17 |
