# TraceLane v0.2 Internal Integrity Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every unreleased v0.2 acquisition, history, run, migration, and trace artifact fail closed when its identity, bytes, references, ordering, approval, or declared provenance is inconsistent.

**Architecture:** Keep v1 unchanged and harden the current v2 contracts in place. JSON Schema remains the wire authority; Python constructors enforce semantic relationships that JSON Schema cannot express; exact file bytes are bound by `ArtifactRef`, complete checksum sets, and trace hash chains. Manual Codex-assisted acquisition remains isolated from the frozen offline benchmark agent.

**Tech Stack:** Python 3.11+, standard library, `jsonschema>=4.22,<5`, pytest, Ruff, SHA-256, canonical JSON, JSONL.

## Global Constraints

- Work directly in `D:\taurui\tracelane` on the local `main` branch; do not create a branch or worktree.
- Do not commit after individual tasks. Make one Conventional Commit only after full verification and the repeated architecture, security, and testing review.
- Do not read, print, copy, or stage `.local/runtime.json`; `.local/` and `artifacts/` remain ignored.
- Do not add Brave Search, another search provider, PKI, certificates, signatures, or key management.
- v1 behavior and fixtures remain byte-compatible.
- v2 is unreleased, so its schemas and Python contracts are corrected in place without a v2 migration.
- Use canonical UTF-8 JSON, UTC RFC 3339 timestamps, lowercase full SHA-256 values, and normalized forward-slash artifact URIs.
- Treat all acquired text as untrusted data; the benchmark agent receives only promoted frozen artifacts through offline tools.
- Every implementation task follows red → green → focused regression.
- Fail closed on integrity, approval, provenance, migration, and trace errors.
- HIST-001 fixture promotion remains blocked until the user explicitly approves the candidate set.

## File Map

```text
src/tracelane/
├── security.py                    # persistence-boundary redaction
├── acquisition/
│   ├── contracts.py               # candidate identity and bound reviews
│   └── service.py                 # safe sessions, ingest, and promotion
├── history/
│   ├── contracts.py               # provenance digest and history contracts
│   └── loader.py                  # kind/schema/cutoff/cross-reference checks
├── v2/
│   ├── schema.py                  # format-aware JSON Schema validation
│   ├── contracts.py               # canonical ArtifactRef
│   ├── storage.py                 # byte verification and safe artifact roots
│   ├── manifests.py               # persisted fingerprint and exact checksums
│   ├── migration.py               # source-bound idempotent v1 import
│   ├── locking.py                 # non-blocking cross-platform file lock
│   └── tracing.py                 # semantic trace reader and hash-chain writer
├── schemas/v2/*.schema.json       # corrected v2 wire contracts
scripts/
├── sync_v2_schema_defs.py         # deterministic ArtifactRef `$defs` sync/check
└── prepare_hist001_candidates.py  # updated manual curated-note API
tests/v2/
├── test_schema.py
├── test_common_contracts.py
├── test_acquisition.py
├── test_history_contracts.py
├── test_history_loader.py
├── test_migration.py
├── test_manifests.py
└── test_tracing.py
```

---

### Task 1: Format-Aware Schemas and One Canonical ArtifactRef

**Files:**
- Create: `scripts/sync_v2_schema_defs.py`
- Modify: `src/tracelane/v2/schema.py`
- Modify: `src/tracelane/schemas/v2/artifact-ref.schema.json`
- Modify: `src/tracelane/schemas/v2/case.schema.json`
- Modify: `src/tracelane/schemas/v2/evidence-candidate.schema.json`
- Modify: `src/tracelane/schemas/v2/evidence-manifest.schema.json`
- Modify: `src/tracelane/schemas/v2/evidence-record.schema.json`
- Modify: `src/tracelane/schemas/v2/run-manifest.schema.json`
- Modify: `src/tracelane/schemas/v2/suite-manifest.schema.json`
- Modify: `tests/v2/test_schema.py`
- Modify: `tests/v2/test_common_contracts.py`

**Interfaces:**
- Produces: `validate_document(name: str, value: Mapping[str, object]) -> None`
- Produces: `artifact_ref_definition() -> dict[str, object]`
- Produces CLI: `python scripts/sync_v2_schema_defs.py --check`

- [ ] **Step 1: Add failing format and schema-drift tests**

```python
def test_json_schema_rejects_invalid_date_time_format() -> None:
    value = {
        "schema_id": "tracelane://schemas/acquisition-session/v2",
        "schema_version": "2.0.0",
        "content_sha256": "a" * 64,
        "session_id": "acq_hist001_20260724",
        "mode": "codex_manual",
        "created_at": "2026-07-24T00:00:00Z",
        "network_access_available_to_agent": False,
    }
    value["created_at"] = "not-a-date"
    with pytest.raises(SchemaValidationError, match="date-time"):
        validate_document("acquisition-session", value)


def test_embedded_artifact_ref_definitions_match_canonical_schema() -> None:
    canonical = artifact_ref_definition()
    schema_root = Path(tracelane.__file__).parent / "schemas" / "v2"
    embedded = []
    for path in sorted(schema_root.glob("*.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        definition = value.get("$defs", {}).get("artifact_ref")
        if definition is not None:
            embedded.append((path.name, definition))
    assert embedded
    assert all(definition == canonical for _, definition in embedded)
```

- [ ] **Step 2: Run the focused tests and observe both failures**

Run:

```powershell
python -m pytest tests/v2/test_schema.py tests/v2/test_common_contracts.py -v
```

Expected: the invalid date is accepted and at least one embedded definition differs from the canonical schema.

- [ ] **Step 3: Enable JSON Schema format checking and expose the canonical definition**

```python
# src/tracelane/v2/schema.py
from jsonschema import Draft202012Validator, FormatChecker


def artifact_ref_definition() -> dict[str, object]:
    schema = _load_schema("artifact-ref")
    return {
        str(key): json.loads(canonical_json(item))
        for key, item in schema.items()
        if key not in {"$schema", "$id", "title"}
    }


def validate_document(name: str, value: Mapping[str, object]) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("schema document must be a mapping")
    schema = _load_schema(name)
    normalized = json.loads(canonical_json(value))
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(normalized),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        raise SchemaValidationError(
            schema_id=str(schema["$id"]),
            pointer=_json_pointer(error.absolute_path),
            message=error.message,
        )
```

- [ ] **Step 4: Add the deterministic definition synchronizer**

```python
# scripts/sync_v2_schema_defs.py
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "src" / "tracelane" / "schemas" / "v2"


def canonical_definition() -> dict[str, object]:
    value = json.loads((SCHEMAS / "artifact-ref.schema.json").read_text(encoding="utf-8"))
    return {key: item for key, item in value.items() if key not in {"$schema", "$id", "title"}}


def rendered(path: Path, definition: dict[str, object]) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    if "artifact_ref" not in value.get("$defs", {}):
        return path.read_text(encoding="utf-8")
    value["$defs"]["artifact_ref"] = definition
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    definition = canonical_definition()
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        output = rendered(path, definition)
        if output != path.read_text(encoding="utf-8"):
            changed.append(path)
            if not args.check:
                path.write_text(output, encoding="utf-8", newline="\n")
    if args.check and changed:
        raise SystemExit("ArtifactRef schema drift: " + ", ".join(p.name for p in changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Fully anchor regexes and synchronize every embedded definition**

Run:

```powershell
python scripts/sync_v2_schema_defs.py
python scripts/sync_v2_schema_defs.py --check
python -m pytest tests/v2/test_schema.py tests/v2/test_common_contracts.py -v
```

Expected: both commands exit 0 and all selected tests pass.

- [ ] **Step 6: Run the v1 schema regression**

Run:

```powershell
python -m pytest tests/test_contracts.py tests/test_validation.py -v
```

Expected: all selected v1 tests pass.

---

### Task 2: Redacted, Path-Safe, Version-Bound Manual Acquisition

**Files:**
- Modify: `src/tracelane/security.py`
- Modify: `src/tracelane/acquisition/contracts.py`
- Modify: `src/tracelane/acquisition/service.py`
- Modify: `src/tracelane/schemas/v2/evidence-candidate.schema.json`
- Modify: `src/tracelane/schemas/v2/candidate-review.schema.json`
- Modify: `scripts/prepare_hist001_candidates.py`
- Modify: `tests/test_security.py`
- Modify: `tests/v2/test_acquisition.py`

**Interfaces:**
- Produces: `classify_and_redact(value: object, *, secrets: Sequence[str] = ()) -> RedactedPayload`
- Produces: `compute_candidate_id(*, query: str, title: str, source_url: str, document_date: str, date_precision: str, content_sha256: str) -> str`
- Produces: `CandidateReview.create(candidate: EvidenceCandidate, *, decision: Literal["approved", "rejected"], reviewer: str, reviewed_at: datetime, available_at: datetime, source_type: Literal["primary", "secondary", "dataset"], license: str, reason: str) -> CandidateReview`
- Produces: `ManualAcquisitionService.candidate_path(candidate_id: str) -> Path`
- Consumes: `ArtifactRoot.resolve(uri: str) -> Path`
- Consumes: `BlobStore.verify(reference: ArtifactRef) -> Path`

- [ ] **Step 1: Add failing value-redaction tests**

```python
@pytest.mark.parametrize(
    ("raw", "forbidden"),
    [
        ("token " + "sk-" + "a" * 24, "sk-"),
        ("https://example.test/?api_key=secret-value", "secret-value"),
        ("mail me at person@example.test", "person@example.test"),
        ("call +86 17610768902", "17610768902"),
        (r"read \\server\share\secret.txt", "server"),
        ("read C:/Users/name/private.txt", "Users/name"),
    ],
)
def test_redact_removes_sensitive_values_inside_ordinary_strings(
    raw: str,
    forbidden: str,
) -> None:
    result = classify_and_redact({"note": raw})
    assert forbidden not in canonical_json(result.value)
    assert result.payload_classification == "restricted"
    assert result.redaction_applied is True


def test_redact_removes_configured_secret_exactly() -> None:
    result = classify_and_redact(
        {"note": "prefix private-runtime-value suffix"},
        secrets=("private-runtime-value",),
    )
    assert result.value == {"note": "prefix [REDACTED] suffix"}
```

- [ ] **Step 2: Add failing acquisition substitution and traversal tests**

```python
def test_promote_rejects_path_syntax_before_reading_candidate(service) -> None:
    review = approved_review_stub(candidate_id="../outside")
    with pytest.raises(ValueError, match="candidate_id"):
        service.promote("../outside", review)


def test_review_is_bound_to_exact_candidate_record(service) -> None:
    candidate = ingest_candidate(service)
    review = CandidateReview.create(
        candidate,
        decision="approved",
        reviewer="yyf",
        reviewed_at=NOW,
        available_at=NOW,
        source_type="primary",
        license="Public-Domain",
        reason="provenance checked",
    )
    candidate_path = service.candidate_path(candidate.candidate_id)
    value = json.loads(candidate_path.read_text(encoding="utf-8"))
    value["title"] = "substituted title"
    value["record_sha256"] = record_digest(value)
    candidate_path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="review.*candidate"):
        service.promote(candidate.candidate_id, review)


def test_existing_session_manifest_is_revalidated(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    manifest = service.session_dir / "manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["session_id"] = "another-session"
    value["content_sha256"] = content_digest(value)
    manifest.write_text(canonical_json(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="session identity"):
        make_service(tmp_path)
```

- [ ] **Step 3: Implement string-value redaction without changing the default caller API**

```python
# src/tracelane/security.py
_API_KEY_VALUE = re.compile(r"\b(?:sk|ark)-[A-Za-z0-9_-]{16,}\b")
_SENSITIVE_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:api[_-]?key|token|secret|authorization|password)=)[^&#\s]+"
)
_EMAIL_VALUE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE_VALUE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_UNC_PATH = re.compile(r"(?i)(?<![A-Z0-9_])\\\\[^\\\s]+\\[^\s]+")
_FORWARD_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Z0-9_])[A-Z]:/[^\s]+")


def _redact_string(value: str, secrets: Sequence[str]) -> str:
    sanitized = value
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = _BEARER_VALUE.sub("Bearer [REDACTED]", sanitized)
    sanitized = _API_KEY_VALUE.sub("[REDACTED]", sanitized)
    sanitized = _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _EMAIL_VALUE.sub("[EMAIL]", sanitized)
    sanitized = _PHONE_VALUE.sub("[PHONE]", sanitized)
    sanitized = _UNC_PATH.sub("[LOCAL_PATH]", sanitized)
    sanitized = _WINDOWS_LOCAL_PATH.sub("[LOCAL_PATH]", sanitized)
    sanitized = _FORWARD_WINDOWS_PATH.sub("[LOCAL_PATH]", sanitized)
    return _POSIX_HOME_PATH.sub("[LOCAL_PATH]", sanitized)
```

Use these exact recursive public functions, preserving calls that pass only `value`:

```python
def redact(value: object, *, secrets: Sequence[str] = ()) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item, secrets=secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [redact(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        return _redact_string(value, secrets)
    return value


def classify_and_redact(
    value: object,
    *,
    secrets: Sequence[str] = (),
) -> RedactedPayload:
    canonical_json(value)
    sanitized = redact(value, secrets=secrets)
    changed = canonical_json(sanitized) != canonical_json(value)
    return RedactedPayload(
        value=sanitized,
        payload_classification="restricted" if changed else "internal",
        redaction_applied=changed,
    )
```

- [ ] **Step 4: Bind candidate and review identities**

```python
# src/tracelane/acquisition/contracts.py
def compute_candidate_id(
    *,
    query: str,
    title: str,
    source_url: str,
    document_date: str,
    date_precision: str,
    content_sha256: str,
) -> str:
    identity = {
        "query": query,
        "title": title,
        "source_url": source_url,
        "document_date": document_date,
        "date_precision": date_precision,
        "content_sha256": content_sha256,
    }
    return f"candidate_{sha256_json(identity)[:24]}"


def source_locator_sha256(source_url: str) -> str:
    return hashlib.sha256(source_url.encode("utf-8")).hexdigest()
```

Add `document_date` and `date_precision` to `EvidenceCandidate`. In `from_dict`, recompute `record_sha256`, `candidate_id`, `content_sha256`, and `content_blob_sha256`.

Add these immutable fields to `CandidateReview`:
`candidate_record_sha256`, `candidate_content_sha256`, and
`source_locator_sha256`. Its existing `content_sha256` remains the digest of
the review document itself.

```python
@classmethod
def create(
    cls,
    candidate: EvidenceCandidate,
    *,
    decision: Literal["approved", "rejected"],
    reviewer: str,
    reviewed_at: datetime,
    available_at: datetime,
    source_type: Literal["primary", "secondary", "dataset"],
    license: str,
    reason: str,
) -> CandidateReview:
    return cls(
        candidate_id=candidate.candidate_id,
        candidate_record_sha256=candidate.record_sha256,
        candidate_content_sha256=candidate.content_sha256,
        source_locator_sha256=source_locator_sha256(candidate.source_url),
        decision=decision,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        document_date=candidate.document_date,
        date_precision=candidate.date_precision,
        available_at=available_at,
        source_type=source_type,
        license=license,
        reason=reason,
    )
```

`from_dict` validates its own review-envelope `content_sha256`. Promotion
compares the three `candidate_*`/source binding fields with the freshly
reloaded candidate.

- [ ] **Step 5: Make ingest curated-text-only and promotion fail closed**

```python
# src/tracelane/acquisition/service.py
from collections.abc import Callable, Mapping, Sequence

_CANDIDATE_ID = re.compile(r"^candidate_[0-9a-f]{24}$")


def _candidate_id(value: str) -> str:
    if not isinstance(value, str) or not _CANDIDATE_ID.fullmatch(value):
        raise ValueError("candidate_id is invalid")
    return value


def _candidate_uri(session_id: str, candidate_id: str) -> str:
    return (
        f"tracelane://artifacts/acquisition/{session_id}/"
        f"candidates/{_candidate_id(candidate_id)}.json"
    )


def candidate_path(self, candidate_id: str) -> Path:
    return self._root.resolve(_candidate_uri(self._session_id, candidate_id))


def _write_or_load_candidate(
    self,
    candidate: EvidenceCandidate,
) -> EvidenceCandidate:
    path = self.candidate_path(candidate.candidate_id)
    if path.exists():
        existing = EvidenceCandidate.from_dict(_read_json_object(path))
        if existing.to_dict() != candidate.to_dict():
            raise ValueError("candidate identity collision")
        self._blob_store.verify(existing.content_ref)
        return existing
    _write_json(path, candidate.to_dict())
    return candidate
```

Use this ingest boundary:

```python
def ingest(
    self,
    *,
    query: str,
    title: str,
    source_url: str,
    document_date: str,
    date_precision: str,
    curated_text: str,
    secrets: Sequence[str] = (),
) -> EvidenceCandidate:
    query = _non_empty(query, "acquisition query")
    title = _non_empty(title, "candidate title")
    source_url = _validate_source_url(source_url)
    metadata = classify_and_redact(
        {"query": query, "title": title},
        secrets=secrets,
    )
    if not isinstance(metadata.value, Mapping):
        raise ValueError("redacted candidate metadata must remain an object")
    query = str(metadata.value["query"])
    title = str(metadata.value["title"])
    source_check = classify_and_redact(source_url, secrets=secrets)
    if source_check.redaction_applied:
        raise ValueError("source URL contains sensitive data")
    if date_precision not in {"day", "month", "year", "estimated"}:
        raise ValueError("candidate date_precision is invalid")
    if not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", document_date):
        raise ValueError("candidate document_date is invalid")
    redacted = classify_and_redact(
        _non_empty(curated_text, "curated text"),
        secrets=secrets,
    )
    if not isinstance(redacted.value, str):
        raise ValueError("redacted curated text must remain text")
    body = redacted.value.encode("utf-8")
    if len(body) > _MAX_CONTENT_BYTES:
        raise ValueError("candidate body size is invalid")
    content_ref = self._blob_store.put_bytes(body, "text/plain", "evidence_blob")
    candidate_id = compute_candidate_id(
        query=query,
        title=title,
        source_url=source_url,
        document_date=document_date,
        date_precision=date_precision,
        content_sha256=content_ref.sha256,
    )
    candidate = EvidenceCandidate.create(
        candidate_id=candidate_id,
        query=query,
        title=title,
        source_url=source_url,
        document_date=document_date,
        date_precision=date_precision,
        retrieved_at=self._now(),
        content_ref=content_ref,
    )
    return self._write_or_load_candidate(candidate)
```

The candidate schema's fully anchored date pattern rejects compound values such as `1812-01/1812-05`; the preparation script must split those into separate candidates before calling `ingest`.

When opening an existing session, load the manifest with `validate_document("acquisition-session", value)`, recompute `content_sha256`, and compare its `session_id` with the requested ID.

In `promote`, validate the ID before building a URI, resolve through `ArtifactRoot`, load the candidate, verify its blob through `BlobStore.verify`, and compare all four bound review fields before persisting the review.

- [ ] **Step 6: Update the candidate preparation script and run focused tests**

Run:

```powershell
python -m pytest tests/test_security.py tests/v2/test_acquisition.py -v
python -m ruff check src/tracelane/security.py src/tracelane/acquisition tests/test_security.py tests/v2/test_acquisition.py scripts/prepare_hist001_candidates.py
```

Expected: all selected tests pass and Ruff exits 0. Do not regenerate or promote the HIST-001 frozen fixture.

---

### Task 3: Verifiable Historical Provenance and Cross-References

**Files:**
- Modify: `src/tracelane/history/contracts.py`
- Modify: `src/tracelane/history/loader.py`
- Modify: `src/tracelane/schemas/v2/evidence-record.schema.json`
- Modify: `src/tracelane/schemas/v2/evidence-manifest.schema.json`
- Modify: `tests/v2/test_history_contracts.py`
- Modify: `tests/v2/test_history_loader.py`

**Interfaces:**
- Produces: `compute_evidence_provenance_sha256(value: Mapping[str, object]) -> str`
- Produces: `resolve_fixture_ref(root, reference, *, expected_kind, expected_schema_id) -> Path`
- Consumes: canonical `ArtifactRef`

- [ ] **Step 1: Add failing provenance and cross-binding tests**

```python
def test_evidence_record_rejects_arbitrary_provenance_digest() -> None:
    value = evidence_record_value()
    value["provenance_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="provenance"):
        EvidenceRecordV2.from_dict(value)


def test_case_must_reference_exact_loaded_evidence_manifest(tmp_path: Path) -> None:
    build_suite(tmp_path)
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    manifest_path = entry.evidence_manifest_path
    replacement = json.loads(manifest_path.read_text(encoding="utf-8"))
    replacement["source_licenses"]["hist-001-ev-0001"] = "CC-BY-4.0"
    replacement["content_sha256"] = content_digest(replacement)
    manifest_path.write_text(canonical_json(replacement) + "\n", encoding="utf-8")
    loaded_manifest = load_evidence_manifest(manifest_path)
    with pytest.raises(ValueError, match="evidence manifest reference"):
        freeze_history_evidence(case, loaded_manifest)


def test_rejected_future_record_must_be_unavailable_by_cutoff(tmp_path: Path) -> None:
    build_suite(
        tmp_path,
        rejected_available_at="1812-06-25T00:00:00Z",
        rejected_known_by_cutoff="known",
    )
    entry = load_history_suite(tmp_path, "development")[0]
    case = load_history_case(entry.case_ref_path)
    manifest = load_evidence_manifest(entry.evidence_manifest_path)
    with pytest.raises(ValueError, match="unavailable"):
        freeze_history_evidence(case, manifest)
```

Extend the existing `build_suite` helper with the two explicit keyword
arguments above. When `rejected_available_at` is non-null, write one rejected
record using the same record builder, recompute its provenance, add its
`ArtifactRef` to `rejected_future_refs`, and leave it out of `record_refs`.

- [ ] **Step 2: Run the focused tests and confirm all three fail**

Run:

```powershell
python -m pytest tests/v2/test_history_contracts.py tests/v2/test_history_loader.py -v
```

Expected: arbitrary provenance is accepted, manifest substitution is not identified at the case boundary, and the rejected record's cutoff classification is not enforced.

- [ ] **Step 3: Define and enforce the exact provenance projection**

```python
# src/tracelane/history/contracts.py
_PROVENANCE_FIELDS = (
    "evidence_id",
    "document_date",
    "date_precision",
    "available_at",
    "known_by_cutoff",
    "source_type",
    "source_title",
    "source_locator",
    "content_ref",
    "fact_ids",
    "transformation_refs",
    "license",
    "excerpt_kind",
)


def compute_evidence_provenance_sha256(value: Mapping[str, object]) -> str:
    missing = [field for field in _PROVENANCE_FIELDS if field not in value]
    if missing:
        raise ValueError(f"provenance fields are missing: {', '.join(missing)}")
    return sha256_json({field: value[field] for field in _PROVENANCE_FIELDS})
```

Call this function in both `EvidenceRecordV2.from_dict` and `to_dict`, rejecting a mismatch.

Use this exact license enum in Python and JSON Schema:

```python
_LICENSES = frozenset(
    {
        "Public-Domain",
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "LicenseRef-Research-Excerpt",
    }
)
```

- [ ] **Step 4: Verify expected kinds, schemas, content, transformations, and set relations**

Use this exact signature and preconditions, retaining the existing safe path and byte checks after them:

```python
def resolve_fixture_ref(
    root: Path,
    reference: ArtifactRef,
    *,
    expected_kind: str,
    expected_schema_id: str | None,
) -> Path:
    if reference.kind != expected_kind:
        raise ValueError("fixture reference kind mismatch")
    if reference.schema_id != expected_schema_id:
        raise ValueError("fixture reference schema mismatch")
    return _resolve_and_verify_fixture_bytes(root, reference)
```

Rename the current path-resolution body to
`_resolve_and_verify_fixture_bytes(root: Path, reference: ArtifactRef) -> Path`
without changing its link, traversal, size, or SHA-256 checks.

In `freeze_history_evidence`:

```python
manifest_path = resolve_fixture_ref(
    manifest.fixture_root,
    case.evidence_manifest_ref,
    expected_kind="evidence_manifest",
    expected_schema_id="tracelane://schemas/evidence-manifest/v2",
)
if _read_json_object(manifest_path) != manifest.to_dict():
    raise ValueError("case evidence manifest reference does not match loaded manifest")
```

For every admitted and rejected record, require kind `evidence_record` and schema `tracelane://schemas/evidence-record/v2`; verify its content reference and every transformation reference. Require admitted and rejected evidence IDs to be unique and disjoint. Require rejected-future records to have `available_at > cutoff_at` and `known_by_cutoff == "unavailable"`.

Require the set of transformation references declared by the evidence manifest to equal the union used by admitted and rejected records:

```python
declared_transformations = {item.uri: item for item in manifest.transformation_refs}
used_transformations = {
    item.uri: item
    for record in (*records, *rejected_records)
    for item in record.transformation_refs
}
if declared_transformations != used_transformations:
    raise ValueError("evidence transformation references are inconsistent")
```

Include `source_licenses` in `compute_history_bundle_sha256` and require the license map to match every admitted record's exact license.

- [ ] **Step 5: Run history and schema regression tests**

Run:

```powershell
python -m pytest tests/v2/test_history_contracts.py tests/v2/test_history_loader.py tests/v2/test_schema.py -v
```

Expected: all selected tests pass.

---

### Task 4: Source-Bound Idempotent v1 Migration

**Files:**
- Modify: `src/tracelane/v2/migration.py`
- Modify: `tests/v2/test_migration.py`

**Interfaces:**
- Produces: `_validate_existing_import(*, import_dir: Path, payload_dir: Path, expected_import_id: str, expected_source_run_id: str, expected_entries: tuple[dict[str, object], ...]) -> MigrationResult`
- Consumes: `MigrationManifest.from_dict`
- Consumes: `assert_safe_tree`

- [ ] **Step 1: Add a failing unrelated-target test**

```python
def test_existing_import_must_match_requested_source_identity(tmp_path: Path) -> None:
    source = write_v1_run(tmp_path / "source")
    result = import_v1_run(source, tmp_path / "artifacts", clock=fixed_clock)
    manifest_path = result.import_dir / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["source_run_id"] = "another-run"
    value["content_sha256"] = content_digest(value)
    manifest_path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source run"):
        import_v1_run(source, tmp_path / "artifacts", clock=fixed_clock)
```

Add this parameterized identity test alongside the source-run test:

```python
@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("import_id", "0" * 24, "identity"),
        ("entries", (), "entries"),
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
    result = import_v1_run(source, tmp_path / "artifacts", clock=fixed_clock)
    manifest_path = result.import_dir / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value[field] = replacement
    value["content_sha256"] = content_digest(value)
    manifest_path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        import_v1_run(source, tmp_path / "artifacts", clock=fixed_clock)
```

- [ ] **Step 2: Run the focused tests and confirm the identity mutation is accepted**

Run:

```powershell
python -m pytest tests/v2/test_migration.py -v
```

Expected: at least the `source_run_id` mutation test fails.

- [ ] **Step 3: Centralize import identity and validate every existing target field**

```python
# src/tracelane/v2/migration.py
def _import_id(source_run_id: str, entries: tuple[dict[str, object], ...]) -> str:
    return sha256_json(
        {
            "source_format": "tracelane-v1",
            "source_run_id": source_run_id,
            "entries": entries,
        }
    )[:24]


def _validate_existing_import(
    *,
    import_dir: Path,
    payload_dir: Path,
    expected_import_id: str,
    expected_source_run_id: str,
    expected_entries: tuple[dict[str, object], ...],
) -> MigrationResult:
    assert_safe_tree(import_dir)
    manifest = _read_manifest(import_dir / "manifest.json")
    if manifest.import_id != expected_import_id:
        raise ValueError("existing v1 import identity does not match source")
    if manifest.source_run_id != expected_source_run_id:
        raise ValueError("existing v1 import source run does not match source")
    if manifest.entries != expected_entries:
        raise ValueError("existing v1 import entries do not match source")
    if manifest.payload_root_sha256 != sha256_json(expected_entries):
        raise ValueError("existing v1 import payload root does not match source")
    if _tree_entries(payload_dir) != expected_entries:
        raise ValueError("existing v1 import payload does not match source")
    return MigrationResult(import_dir, payload_dir, manifest)
```

Resolve and validate the artifact root before target construction. Use `_import_id` for both new and existing imports. Treat the manifest as the final completion marker; a directory without a valid manifest remains a partial import and is verified file-by-file before completion.

- [ ] **Step 4: Run migration and v1 inspection regressions**

Run:

```powershell
python -m pytest tests/v2/test_migration.py tests/test_experiments.py -v
```

Expected: all selected tests pass.

---

### Task 5: Persisted Run Fingerprint and Exact Checksum Closure

**Files:**
- Modify: `src/tracelane/v2/manifests.py`
- Modify: `src/tracelane/schemas/v2/run-manifest.schema.json`
- Modify: `src/tracelane/schemas/v2/checksums.schema.json`
- Modify: `tests/v2/test_manifests.py`

**Interfaces:**
- Produces: `ExecutionFingerprint.from_dict(value: Mapping[str, object]) -> ExecutionFingerprint`, `ExecutionFingerprint.to_dict() -> dict[str, object]`, and `ExecutionFingerprint.run_id: str`
- Produces: `RunManifest.execution_fingerprint: ExecutionFingerprint`
- Produces: `write_checksums(run_dir: Path) -> ArtifactRef`
- Produces: `validate_run(run_dir: Path) -> None`

- [ ] **Step 1: Add failing fingerprint and checksum-closure tests**

```python
def test_manifest_persists_full_execution_fingerprint(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    value = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert value["execution_fingerprint"] == fingerprint().to_dict()
    assert value["run_id"] == fingerprint().run_id
    assert "code_revision" not in value


def test_validate_run_rejects_unlisted_extra_file(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    (run_dir / "output" / "extra.json").parent.mkdir(parents=True)
    (run_dir / "output" / "extra.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum coverage"):
        validate_run(run_dir)


def test_validate_run_rejects_component_digest_substitution(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    manifest_path = run_dir / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["execution_fingerprint"]["case_sha256"] = "f" * 64
    value["run_id"] = ExecutionFingerprint.from_dict(value["execution_fingerprint"]).run_id
    value["content_sha256"] = content_digest(value)
    manifest_path.write_text(canonical_json(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="case.*fingerprint"):
        validate_run(run_dir)


def test_failed_run_requires_trace_and_failure_record(tmp_path: Path) -> None:
    run_dir = write_minimal_run(tmp_path)
    value = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    value["lifecycle_status"] = "failed"
    value["trace_ref"] = None
    value["failure_ref"] = None
    value["content_sha256"] = content_digest(value)
    with pytest.raises(ValueError, match="failed run"):
        RunManifest.from_dict(value)
```

- [ ] **Step 2: Run focused tests and confirm the new invariants fail**

Run:

```powershell
python -m pytest tests/v2/test_manifests.py -v
```

Expected: the fingerprint is absent and extra files are accepted.

- [ ] **Step 3: Serialize the complete fingerprint exactly once**

```python
# src/tracelane/v2/manifests.py
@dataclass(frozen=True)
class ExecutionFingerprint:
    case_sha256: str
    evidence_manifest_sha256: str
    harness_config_sha256: str
    runtime_config_sha256: str
    grader_set_sha256: str
    repeat: int
    code_revision: str

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ExecutionFingerprint:
        return cls(
            case_sha256=str(value["case_sha256"]),
            evidence_manifest_sha256=str(value["evidence_manifest_sha256"]),
            harness_config_sha256=str(value["harness_config_sha256"]),
            runtime_config_sha256=str(value["runtime_config_sha256"]),
            grader_set_sha256=str(value["grader_set_sha256"]),
            repeat=int(value["repeat"]),
            code_revision=str(value["code_revision"]),
        )

    def to_dict(self) -> dict[str, object]:
        return json.loads(canonical_json(asdict(self)))

    @property
    def run_id(self) -> str:
        return sha256_json(self.to_dict())
```

Define `RunManifest.execution_fingerprint: ExecutionFingerprint` and
`RunManifest.failure_ref: ArtifactRef | None`. Remove top-level
`code_revision`. Serialize both fields in `_raw_dict`; parse them in
`from_dict`; require:

```python
if self.run_id != self.execution_fingerprint.run_id:
    raise ValueError("run identity does not match execution fingerprint")
```

- [ ] **Step 4: Make checksum construction and validation cover the exact directory**

```python
def _authoritative_run_files(run_dir: Path) -> tuple[Path, ...]:
    excluded = {
        (run_dir / "manifest.json").resolve(strict=False),
        (run_dir / "checksums.json").resolve(strict=False),
    }
    return tuple(
        path.resolve(strict=True)
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and path.resolve(strict=True) not in excluded
    )


def write_checksums(run_dir: Path) -> ArtifactRef:
    paths = _authoritative_run_files(run_dir)
    return _write_checksum_entries(run_dir, paths)
```

In `validate_run`, compare the normalized set of checksum paths with `_authoritative_run_files(run_dir)` before validating bytes. Verify every manifest reference that resolves inside the run is present in the checksum set.

Use a slot table to enforce kinds and schemas:

```python
_RUN_INPUT_SLOTS = {
    "case_ref": ("case", "tracelane://schemas/case/v2", "case_sha256"),
    "evidence_manifest_ref": (
        "evidence_manifest",
        "tracelane://schemas/evidence-manifest/v2",
        "evidence_manifest_sha256",
    ),
    "harness_config_ref": (
        "harness_config",
        "tracelane://schemas/object-envelope/v2",
        "harness_config_sha256",
    ),
    "runtime_config_ref": (
        "runtime_config",
        "tracelane://schemas/object-envelope/v2",
        "runtime_config_sha256",
    ),
    "grader_set_ref": (
        "grader_set",
        "tracelane://schemas/object-envelope/v2",
        "grader_set_sha256",
    ),
}
```

For each slot, verify URI, kind, schema, size, file digest, checksum membership, and equality with the fingerprint digest.

- [ ] **Step 5: Enforce lifecycle invariants**

```python
if self.lifecycle_status in {"created", "running"} and self.completed_at is not None:
    raise ValueError("non-terminal run cannot have completed_at")
if self.lifecycle_status in {"completed", "failed"} and self.completed_at is None:
    raise ValueError("terminal run must have completed_at")
if self.completed_at is not None and self.completed_at < self.started_at:
    raise ValueError("run completion cannot precede start")
if self.lifecycle_status == "completed":
    if self.trace_ref is None or self.grade_report_ref is None:
        raise ValueError("completed run must reference trace and grade report")
    if self.failure_ref is not None:
        raise ValueError("completed run cannot reference a failure record")
if self.lifecycle_status == "failed":
    if self.trace_ref is None or self.failure_ref is None:
        raise ValueError("failed run must reference trace and failure record")
```

- [ ] **Step 6: Run manifest, storage, and v1 regression tests**

Run:

```powershell
python -m pytest tests/v2/test_manifests.py tests/v2/test_storage.py tests/test_artifacts.py -v
```

Expected: all selected tests pass.

---

### Task 6: Semantic Trace Hash Chain and Stale-Writer Protection

**Files:**
- Create: `src/tracelane/v2/locking.py`
- Modify: `src/tracelane/v2/tracing.py`
- Modify: `src/tracelane/schemas/v2/trace-event.schema.json`
- Modify: `tests/v2/test_tracing.py`

**Interfaces:**
- Produces: `event_content_sha256(value: Mapping[str, object]) -> str`
- Produces: `read_trace(path: str | Path, *, expected_run_id: str | None = None) -> tuple[TraceEventV2, ...]`
- Produces: `exclusive_file_lock(path: Path) -> Iterator[None]`
- Consumes: `classify_and_redact`

- [ ] **Step 1: Add failing mutation, truncation, payload, and stale-writer tests**

```python
def trace_path(root: Path) -> Path:
    return root / "runs" / RUN_ID / "trace" / "events.jsonl"


def write_three_event_trace(root: Path) -> Path:
    recorder = recorder_v2(root)
    started = recorder.emit("run.started", {"status": "running"})
    stage = recorder.emit(
        "stage.started",
        {"stage_id": "research"},
        causation_id=started.event_id,
        parent_span_id=started.span_id,
    )
    recorder.emit(
        "stage.completed",
        {"stage_id": "research"},
        causation_id=stage.event_id,
        parent_span_id=stage.span_id,
    )
    return trace_path(root)


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(canonical_json(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def mutate_rows(
    rows: list[dict[str, object]],
    mutation: str,
) -> list[dict[str, object]]:
    changed = deepcopy(rows)
    if mutation == "edit":
        changed[1]["payload"]["stage_id"] = "changed"
    elif mutation == "delete":
        del changed[1]
    elif mutation == "insert":
        changed.insert(1, deepcopy(changed[0]))
    elif mutation == "reorder":
        changed[1], changed[2] = changed[2], changed[1]
    else:
        raise AssertionError(f"unknown mutation: {mutation}")
    return changed


@pytest.mark.parametrize("mutation", ["edit", "delete", "insert", "reorder"])
def test_read_trace_rejects_broken_event_chain(tmp_path: Path, mutation: str) -> None:
    path = write_three_event_trace(tmp_path)
    rows = read_jsonl(path)
    mutated = mutate_rows(rows, mutation)
    write_jsonl(path, mutated)
    with pytest.raises(ValueError, match="trace"):
        read_trace(path, expected_run_id=RUN_ID)


def test_trace_event_requires_tool_payload_contract(tmp_path: Path) -> None:
    recorder = recorder_v2(tmp_path)
    with pytest.raises(ValueError, match="call_id"):
        recorder.emit("tool.called", {"tool_name": "read_evidence", "arguments": {}})


def test_stale_recorder_cannot_reuse_sequence(tmp_path: Path) -> None:
    store = RunStore.create(tmp_path, RUN_ID)
    first = TraceRecorderV2(store, clock=IncrementingClock())
    stale = TraceRecorderV2(store, clock=IncrementingClock())
    first.emit("run.started", {"status": "running"})
    with pytest.raises(ValueError, match="stale trace recorder"):
        stale.emit("run.started", {"status": "running"})
```

Add a completed-run integration case in `test_manifests.py` that truncates the last valid JSONL line and expects `validate_run` to fail through the trace `ArtifactRef` or checksum.

Update every existing trace test payload to the new minimum contracts:

```python
TOOL_CALLED = {
    "call_id": "call_001",
    "tool_name": "read_evidence",
    "arguments": {"query": "Napoleon"},
}
TOOL_OBSERVED = {
    "call_id": "call_001",
    "tool_name": "read_evidence",
    "output": {"result": "ok"},
    "is_error": False,
    "error_code": None,
}
RUN_STARTED = {"status": "running"}
STAGE_STARTED = {"stage_id": "research"}
```

- [ ] **Step 2: Run focused trace tests and confirm they fail**

Run:

```powershell
python -m pytest tests/v2/test_tracing.py -v
```

Expected: edited payloads and stale recorders are not rejected by the current implementation.

- [ ] **Step 3: Add full event digest and deterministic event identity**

```python
# src/tracelane/v2/tracing.py
def event_content_sha256(value: Mapping[str, object]) -> str:
    projection = {
        str(key): item for key, item in value.items() if key not in {"event_id", "content_sha256"}
    }
    return sha256_json(projection)


def _event_id(content_sha256: str) -> str:
    return f"evt_{content_sha256}"
```

Add `previous_event_sha256: str | None` and `content_sha256: str` to `TraceEventV2` and the schema. `from_dict` recomputes the digest and event ID. The schema patterns require a full 64-character digest for both.

- [ ] **Step 4: Make one semantic validator power construction and public reads**

```python
def _validate_trace_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_run_id: str | None,
) -> tuple[TraceEventV2, ...]:
    events: list[TraceEventV2] = []
    prior_event_ids: set[str] = set()
    prior_span_ids: set[str] = set()
    previous_sha256: str | None = None
    inferred_run_id = expected_run_id
    for sequence, row in enumerate(rows, start=1):
        event = TraceEventV2.from_dict(row)
        inferred_run_id = inferred_run_id or event.run_id
        if event.sequence != sequence:
            raise ValueError("trace sequence is not contiguous")
        if event.run_id != inferred_run_id:
            raise ValueError("trace run identity is inconsistent")
        if event.previous_event_sha256 != previous_sha256:
            raise ValueError("trace event hash chain is invalid")
        if event.causation_id is not None and event.causation_id not in prior_event_ids:
            raise ValueError("trace causation reference is invalid")
        if event.parent_span_id is not None and event.parent_span_id not in prior_span_ids:
            raise ValueError("trace parent span reference is invalid")
        previous_sha256 = event.content_sha256
        prior_event_ids.add(event.event_id)
        prior_span_ids.add(event.span_id)
        events.append(event)
    return tuple(events)
```

`read_trace` parses JSONL and delegates to this function. `TraceRecorderV2` uses the same validator when it opens an existing trace.

- [ ] **Step 5: Add exact event payload conditionals to the wire schema**

Add JSON Schema `if`/`then` rules for:

```json
{
  "model.called": ["turn", "runtime_id"],
  "model.observed": [
    "turn",
    "tool_call_count",
    "has_output",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "latency_ms"
  ],
  "tool.called": ["call_id", "tool_name", "arguments"],
  "tool.observed": ["call_id", "tool_name", "output", "is_error", "error_code"],
  "run.started": ["status"],
  "run.completed": ["status"],
  "stage.started": ["stage_id"],
  "stage.completed": ["stage_id"],
  "stage.failed": ["stage_id", "error_code"]
}
```

`run.started.payload.status` is the constant `running`; `run.completed.payload.status` is the constant `completed`. Counts, turns, and token values are non-negative integers; `latency_ms` is a non-negative number; IDs are non-empty strings; `is_error` and `has_output` are booleans; `arguments` is an object.

- [ ] **Step 6: Protect append with a non-blocking cross-platform lock and stale-tail check**

```python
# src/tracelane/v2/locking.py
@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("trace writer lock is unavailable") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
```

Inside `TraceRecorderV2.emit`, take the lock, rescan through `_validate_trace_rows`, and compare the returned tail sequence and digest with the recorder's remembered state. Raise `ValueError("stale trace recorder")` on mismatch. Build the next event using the verified prior digest, validate it, append it, flush, and `fsync`.

- [ ] **Step 7: Run trace, manifest-integration, and security tests**

Run:

```powershell
python -m pytest tests/v2/test_tracing.py tests/v2/test_manifests.py tests/test_security.py -v
```

Expected: all selected tests pass.

---

### Task 7: Adversarial Matrix, Documentation Reconciliation, and Release Gate

**Files:**
- Modify: `tests/v2/test_schema.py`
- Modify: `tests/v2/test_acquisition.py`
- Modify: `tests/v2/test_history_loader.py`
- Modify: `tests/v2/test_migration.py`
- Modify: `tests/v2/test_manifests.py`
- Modify: `tests/v2/test_tracing.py`
- Modify: `docs/superpowers/plans/2026-07-24-tracelane-v0.2.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes every hardened public API from Tasks 1–6.
- Produces one reproducible verification record in terminal output; it does not create a committed generated report.

- [ ] **Step 1: Complete the negative-test matrix**

Use parametrized tests with exact expected error classes for:

```python
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

TRACE_CORRUPTIONS = (
    "event_edit",
    "event_delete",
    "event_insert",
    "event_reorder",
    "suffix_truncation",
    "broken_causation",
    "broken_parent_span",
    "invalid_payload",
    "stale_writer",
)

ACQUISITION_CORRUPTIONS = (
    "candidate_traversal",
    "candidate_unc_path",
    "record_substitution",
    "blob_substitution",
    "stale_approval",
    "session_identity_substitution",
)
```

Each table item must cause the public loader or validator to raise `ValueError` or `SchemaValidationError`; no test may call a private parser as its final assertion.

- [ ] **Step 2: Remove the obsolete Brave design from the parent implementation plan**

Replace the parent plan's acquisition description with:

```markdown
Manual Codex-assisted acquisition is the v0.2 source-discovery boundary.
Codex or a human supplies a source URL and curated note; TraceLane binds,
reviews, and freezes those bytes. The scored agent has no network tool.
Automated search providers and raw HTTP fetching are outside v0.2.
```

Remove `acquisition/brave.py`, `acquisition/http.py`, Brave API configuration, and Brave-specific test steps from the file map and tasks. Do not add a replacement network provider.

- [ ] **Step 3: Document the trust and release boundary**

Add this concise statement to README's artifact-integrity section:

```markdown
TraceLane verifies internal consistency: hashes, references, trace order,
approval bindings, and complete run contents. It detects corruption and
partial or stale substitutions. It does not claim authenticity against an
attacker who can rewrite every artifact and Git history; a published Git
commit or release digest is the external anchor.
```

Update `CHANGELOG.md` under `Unreleased` with the user-facing effects:
persisted execution fingerprints, exact checksum closure, bound acquisition
reviews, verified historical provenance, and trace hash chaining. Do not
mention Brave or PKI as features.

- [ ] **Step 4: Run schema synchronization and formatting**

Run:

```powershell
python scripts/sync_v2_schema_defs.py --check
python -m ruff format .
python -m ruff check .
```

Expected: schema check and Ruff both exit 0.

- [ ] **Step 5: Run all tests except the intentionally gated HIST-001 fixture**

Run:

```powershell
python -m pytest --ignore=tests/v2/test_hist001_fixture.py
```

Expected: all collected tests pass.

- [ ] **Step 6: Prove the fixture gate is the only remaining expected failure**

Run:

```powershell
python -m pytest tests/v2/test_hist001_fixture.py -v
```

Expected before candidate approval: FAIL only because `fixtures/v0.2` has not been promoted. Any schema, loader, provenance, or integrity failure is a regression and must be fixed before review.

- [ ] **Step 7: Scan tracked and staged content for secret patterns**

Run:

```powershell
git grep -n -E "sk-[A-Za-z0-9_-]{16,}|Bearer[[:space:]]+[A-Za-z0-9._~+/-]{16,}" -- . ":(exclude)docs/superpowers/specs/2026-07-24-integrity-hardening-design.md"
git status --short
git diff --check
```

Expected: no live secret match; `.local/runtime.json` is absent from status; `git diff --check` exits 0.

- [ ] **Step 8: Repeat the three-way expert review**

Dispatch three read-only reviewers over the complete uncommitted diff:

- architecture: data contracts, dependency boundaries, future agent-loop compatibility;
- security: path handling, redaction, trust boundary, substitution and tamper resistance;
- testing: negative matrix, false positives, missing regressions, determinism.

Every reviewer must return findings with severity, exact file/line, reproduction or reasoning, and a merge recommendation. Fix actionable findings with another red → green test cycle, then rerun the affected reviewer.

- [ ] **Step 9: Run the final gate after review fixes**

Run:

```powershell
python scripts/sync_v2_schema_defs.py --check
python -m ruff format --check .
python -m ruff check .
python -m pytest --ignore=tests/v2/test_hist001_fixture.py
git diff --check
git status --short
```

Expected: every command exits 0; the only intentionally incomplete product item remains the unapproved HIST-001 fixture.

- [ ] **Step 10: Stop for user approval before the one main-branch commit**

Report:

- exact passing test count;
- the expected HIST-001 gate status;
- all cross-review findings and dispositions;
- files that will be committed;
- confirmation that `.local/` and `artifacts/` are not included.

Do not commit until the user explicitly approves the final verified diff. After approval, use one title-style Conventional Commit.

---

### Task 8: Final Review Closure and Byte-Preserving Evidence Archive

**Files:**
- Modify: `src/tracelane/v2/manifests.py`
- Modify: `src/tracelane/v2/tracing.py`
- Modify: `src/tracelane/acquisition/service.py`
- Modify: `src/tracelane/history/loader.py`
- Modify: `src/tracelane/history/__init__.py`
- Modify: `src/tracelane/v2/locking.py`
- Modify: `tests/v2/test_manifests.py`
- Modify: `tests/v2/test_tracing.py`
- Modify: `tests/v2/test_acquisition.py`
- Modify: `tests/v2/test_history_loader.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: `atomic_create_bytes(path, data, *, root, label)`,
  `secure_read_bytes(path, *, root, label)`, `ArtifactRoot.resolve(uri)`,
  `EvidenceRecordV2.candidate_ref`, and `EvidenceRecordV2.review_ref`.
- Produces:
  `archive_promoted_evidence(source_root: str | Path,
  target_root: str | Path, record_ref: ArtifactRef) -> ArtifactRef`.
- Produces private helpers
  `authenticate_promoted_closure(root: ArtifactRoot,
  record_ref: ArtifactRef) -> Sequence[tuple[ArtifactRef, bytes]]` and
  `publish_identical_artifact(root: ArtifactRoot, reference: ArtifactRef,
  data: bytes) -> None`.
- Preserves every archived `ArtifactRef`, URI, digest, size, and exact byte
  payload. No archive operation promotes or approves HIST-001.

- [ ] **Step 1: Add failing terminal-lifecycle and hardened-publication tests**

Add public tests named
`test_failed_run_rejects_run_completed_event`,
`test_completed_run_rejects_duplicate_run_started`,
`test_completed_run_rejects_duplicate_run_completed`,
`test_write_checksums_uses_hardened_create_new`, and
`test_write_run_manifest_uses_hardened_create_new`. Each semantic test ends at:

```python
with pytest.raises(ValueError, match="run trace"):
    validate_run(run_dir)
```

Each publication test injects the identity change through
`storage.atomic_create_bytes` and asserts that neither a valid reference nor a
successfully finalized manifest is returned.

The lifecycle tests must rebuild the trace reference and checksums so the only
failure is semantic. The publication tests inject an identity/link change into
the hardened storage primitive and assert that no successful terminal artifact
is reported.

Run:

```powershell
python -m pytest tests/v2/test_manifests.py -k "duplicate_run or failed_run_rejects or hardened_create" -v
```

Expected before implementation: lifecycle-conflicting traces are accepted and
the hardened-publication probe is never reached.

- [ ] **Step 2: Enforce exact lifecycle counts and use hardened create-new**

After parsing the authenticated trace bytes, enforce:

```python
started = [event for event in events if event.event_type == "run.started"]
completed = [event for event in events if event.event_type == "run.completed"]
if len(started) != 1 or events[0] is not started[0]:
    raise ValueError("terminal run trace must contain exactly one initial run.started")
if manifest.lifecycle_status == "completed":
    if len(completed) != 1 or events[-1] is not completed[0]:
        raise ValueError("completed run trace must contain exactly one final run.completed")
elif completed:
    raise ValueError("failed run trace must not contain run.completed")
```

Replace `_write_new_bytes` internals with `atomic_create_bytes`, passing the
run artifact root through both `write_checksums` and `write_run_manifest`.
Map `FileExistsError`/create conflicts to the existing finalize-once
`ValueError` messages; do not unlink an unverified pathname after failure.

Run:

```powershell
python -m pytest tests/v2/test_manifests.py -v
```

Expected: all manifest tests pass.

- [ ] **Step 3: Add failing recovery-preflight and ingest-interruption tests**

Add public tests named
`test_recovery_does_not_repair_invalid_existing_inventory`,
`test_recovery_keeps_journal_until_merged_session_validates`,
`test_ingest_recovers_after_candidate_publish_before_inventory`, and
`test_two_services_do_not_lose_ingest_inventory`. The invalid-base cases assert:

```python
before = tree_snapshot(tmp_path)
with pytest.raises(ValueError):
    make_service(tmp_path)
assert transaction_path.exists()
assert tree_snapshot(tmp_path) == before
```

The interrupted-ingest case then reopens with `make_service(tmp_path)` and
asserts that the exact candidate reference occurs once in `candidate_refs`.

The first test starts from a manifest with a valid envelope but a broken
existing candidate/review/record lineage plus a valid pending promotion
journal. Reopening must reject without publishing pending files or deleting the
journal. The ingest interruption test simulates the boundary after candidate
publication and before manifest publication, then reopens the service and
requires a valid, inventoried candidate.

Run:

```powershell
python -m pytest tests/v2/test_acquisition.py -k "recovery or ingest" -v
```

Expected before implementation: recovery mutates the invalid session and an
orphan candidate makes the session permanently fail closed.

- [ ] **Step 4: Make recovery preflight-complete and journal ingest**

Use the existing per-session blocking lock for constructor recovery, ingest,
and promotion. Before materializing a promotion:

1. parse and authenticate the journal;
2. validate the complete base manifest inventory and cross-lineage while
   allowing only the exact journal-declared pending review/record paths as
   extras;
3. materialize or verify the pending documents;
4. build the merged manifest in memory;
5. validate the complete merged inventory and cross-lineage;
6. publish the merged manifest;
7. delete the journal only after the published manifest is reread and
   validated.

Generalize the transaction envelope with an explicit operation field:

```python
{
    "operation": "ingest" | "promote",
    "session_id": session_id,
    "base_manifest_sha256": manifest["content_sha256"],
    "candidate_ref": candidate_ref.to_dict(),
    "candidate": candidate.to_dict(),  # ingest only
    "review_ref": review_ref.to_dict(),  # promote only
    "record_ref": record_ref.to_dict(),  # promote only
    "review": review.to_dict(),  # promote only
    "record": record.to_dict(),  # promote only
    "content_sha256": content_digest(transaction_without_content_sha256),
}
```

Reject a journal whose base digest does not match the authenticated base
manifest unless every intended reference is already present identically in the
current manifest, which is the idempotent completed-transaction case.

Run:

```powershell
python -m pytest tests/v2/test_acquisition.py -v
```

Expected: all acquisition tests pass, including every interruption boundary.

- [ ] **Step 5: Add a failing real acquisition-to-fixture archive test**

Create a source artifact root in `tmp_path`, then use the public acquisition API
to ingest, approve, and promote one evidence record. Call the proposed archive
API into a different empty target root and assert:

```python
archived_ref == promoted_ref
target_root.resolve(promoted_ref.uri).read_bytes() == source_root.resolve(
    promoted_ref.uri
).read_bytes()
```

Build a minimal case/evidence manifest/suite whose record ref is the unchanged
promoted ref, and require public historical loading to validate it. Also test:

- candidate, review, content, and transformation bytes are copied;
- a second identical archive call is idempotent;
- a conflicting existing target is rejected without overwrite;
- missing or substituted source closure is rejected before target mutation;
- no `fixtures/v0.2` path is created.

Run:

```powershell
python -m pytest tests/v2/test_history_loader.py -k "archive_promoted" -v
```

Expected before implementation: the API is absent or the loader rejects the
preserved acquisition URI as a fixture path mismatch.

- [ ] **Step 6: Implement the byte-preserving archive closure**

Implement:

```python
def archive_promoted_evidence(
    source_root: str | Path,
    target_root: str | Path,
    record_ref: ArtifactRef,
) -> ArtifactRef:
    source = ArtifactRoot(Path(source_root))
    target = ArtifactRoot(Path(target_root))
    source_documents = authenticate_promoted_closure(source, record_ref)
    for reference, data in source_documents:
        publish_identical_artifact(target, reference, data)
    authenticate_promoted_closure(target, record_ref)
    return record_ref
```

The function must:

1. authenticate and parse the promoted evidence record from `record_ref`;
2. authenticate candidate and review from the record's exact refs;
3. validate candidate-to-review-to-record lineage;
4. build the exact closure containing record, candidate, review, content, and
   ordered transformation refs;
5. read and authenticate the entire source closure before any target write;
6. recreate every unchanged URI-relative path below the target
   `ArtifactRoot`;
7. use `atomic_create_bytes` for absent targets and require exact bytes for
   existing targets;
8. validate the archived closure from the target root and return the unchanged
   `record_ref`.

Remove the fixture-native candidate/review path inference from history loading.
Exact `ArtifactRef` authentication plus lineage validation is authoritative;
the live acquisition-session loader continues to enforce its deterministic
session paths.

Export the function from `tracelane.history`.

Run:

```powershell
python -m pytest tests/v2/test_history_loader.py tests/v2/test_acquisition.py -v
```

Expected: all acquisition/archive/history tests pass.

- [ ] **Step 7: Enforce whole-event configured-secret fail-closed**

Add a public test that configures each structural field value as a secret,
including `run_id`, a valid prior `causation_id`, and a valid prior
`parent_span_id`. The recorder must reject before appending when the final
serialized event would contain a configured secret.

After constructing and semantically validating the prospective event but
before opening the trace for append, classify the complete `event.to_dict()`
with configured secrets. If it would change, raise:

```python
ValueError("trace event contains restricted metadata")
```

Do not redact identity fields because that would break the event hash chain.
Payload and free-text mapping keys keep their existing preconstruction
redaction behavior.

Run:

```powershell
python -m pytest tests/v2/test_tracing.py -k "configured_secret or metadata" -v
```

Expected: all selected tests pass and rejected events leave the trace bytes
unchanged.

- [ ] **Step 8: Neutralize shared-lock diagnostics and reconcile docs**

Change shared lock diagnostics from trace-specific wording to neutral
artifact-lock wording without changing exception types. Update README and CHANGELOG to
describe:

- manifest-last recoverable acquisition transactions;
- byte-preserving evidence archive closure;
- structural trace identities fail closed when they collide with configured
  secrets;
- migration uses per-file atomic publication plus an authenticated completion
  marker.

Do not claim that a public HIST-001 fixture exists.

- [ ] **Step 9: Run the complete release gates and repeat three-way review**

Run:

```powershell
python scripts/sync_v2_schema_defs.py --check
python -m ruff format --check .
python -m ruff check .
python -m pytest --ignore=tests/v2/test_hist001_fixture.py
python -m pytest tests/v2/test_hist001_fixture.py -v
git diff --check
git status --short
git diff --cached --name-only
```

Expected:

- schema/Ruff/non-HIST/diff checks pass;
- isolated HIST fails only because `fixtures/v0.2` is absent;
- no secret is present in explicit safe roots;
- branch is `main`;
- staged files and new commits are zero;
- `fixtures/v0.2` and `artifacts/` are absent from status.

Repeat architecture, security, and testing read-only reviews over the entire
uncommitted working tree. Fix every Critical or Important finding with another
recorded red-to-green cycle. Stop for explicit user approval before staging or
the single title-style Conventional Commit.
