# Project Evidence Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repository-tracked, project-indexed Evidence Registry and
import the nine HIST-001 candidates as verified pending records without
creating or approving `fixtures/v0.2`.

**Architecture:** A new `tracelane.evidence_registry` package owns typed
contracts, a hardened `tracelane://evidence/` resolver, immutable content
blobs, append-only reviews, deterministic indexes, queries, and a transactional
acquisition importer. Checked-in JSON under `evidence/` is the research source
of truth; generated indexes are rebuilt from candidate and review records, and
fixtures remain a separately approved frozen derivative.

**Tech Stack:** Python 3.11+, frozen dataclasses, JSON Schema Draft 2020-12,
existing TraceLane canonical JSON and SHA-256 helpers, existing hardened v2
filesystem primitives, argparse, pytest, and Ruff. No new dependency.

## Global Constraints

- Work directly on the current local `main`; do not create a branch or
  worktree.
- Use strict TDD for every production behavior: accepted RED, minimal GREEN,
  focused regression, then task review.
- Do not fetch evidence from the network or call a language model.
- Do not create, approve, or package `fixtures/v0.2`.
- Do not read, print, or modify `.local/runtime.json`.
- Do not store the external source package's absolute path in tracked data,
  documentation, errors, indexes, or reports.
- Retain every candidate; initial effective status is `pending`.
- Retain third-party full text only when a positive retention policy permits
  it. Initial HIST-001 content is repository-authored paraphrase with
  `paraphrase_only`.
- The December 1812 29th Bulletin has role `future-control` and is excluded
  from clean evidence queries.
- Persisted JSON is canonical UTF-8, sorted, compact, newline-terminated, and
  byte-deterministic.
- All writes are create-new or identity-checked. Different existing bytes are
  rejected.
- Keep commits local until the user asks to push.

---

## File Structure

### New production package

- `src/tracelane/evidence_registry/__init__.py`
  - Public registry API only.
- `src/tracelane/evidence_registry/contracts.py`
  - Project, candidate, transformation, and import-metadata contracts.
- `src/tracelane/evidence_registry/storage.py`
  - Safe evidence-root URI resolution, canonical JSON reads, and content blob
    storage.
- `src/tracelane/evidence_registry/reviews.py`
  - Immutable review records, chain validation, and effective status.
- `src/tracelane/evidence_registry/index.py`
  - Deterministic project/global index construction, verification, and queries.
- `src/tracelane/evidence_registry/importer.py`
  - Authenticated acquisition snapshot import and transactional publication.

### New schemas

- `src/tracelane/schemas/v2/evidence-project.schema.json`
- `src/tracelane/schemas/v2/project-evidence-candidate.schema.json`
- `src/tracelane/schemas/v2/evidence-transformation.schema.json`
- `src/tracelane/schemas/v2/evidence-review.schema.json`
- `src/tracelane/schemas/v2/evidence-project-index.schema.json`
- `src/tracelane/schemas/v2/evidence-registry.schema.json`
- `src/tracelane/schemas/v2/evidence-import-metadata.schema.json`

### New and modified tools

- `scripts/import_hist001_evidence.py`
  - Deterministic initial project importer and verifier.
- `scripts/prepare_hist001_candidates.py`
  - Add authenticated machine-readable import metadata alongside the human
    review Markdown.
- `src/tracelane/cli.py`
  - Add `evidence list`, `find`, `verify`, and `rebuild-index`.
- `scripts/sync_v2_schema_defs.py`
  - Continue synchronizing embedded ArtifactRef definitions for new schemas.

### Checked-in data and documentation

- `evidence/README.md`
- `evidence/registry.json`
- `evidence/projects/hist-001/README.md`
- `evidence/projects/hist-001/project.json`
- `evidence/projects/hist-001/index.json`
- `evidence/projects/hist-001/candidates/*.json`
- `evidence/blobs/sha256/*/*.blob`
- `README.md`
- `CHANGELOG.md`

### New tests

- `tests/v2/test_evidence_registry_contracts.py`
- `tests/v2/test_evidence_registry_storage.py`
- `tests/v2/test_evidence_registry_reviews.py`
- `tests/v2/test_evidence_registry_index.py`
- `tests/v2/test_evidence_registry_importer.py`
- `tests/v2/test_evidence_registry_cli.py`
- `tests/v2/test_hist001_evidence_registry.py`

---

### Task 1: Core Evidence Registry Contracts and Schemas

**Files:**

- Create: `src/tracelane/evidence_registry/__init__.py`
- Create: `src/tracelane/evidence_registry/contracts.py`
- Create: `src/tracelane/schemas/v2/evidence-project.schema.json`
- Create: `src/tracelane/schemas/v2/project-evidence-candidate.schema.json`
- Create: `src/tracelane/schemas/v2/evidence-transformation.schema.json`
- Create: `src/tracelane/schemas/v2/evidence-import-metadata.schema.json`
- Create: `tests/v2/test_evidence_registry_contracts.py`
- Modify: `tests/v2/test_schema.py`

**Interfaces:**

- Consumes:
  - `ArtifactRef`, `content_digest`, and `make_object_id` from
    `tracelane.v2.contracts`.
  - `canonical_json`, `parse_utc`, and `sha256_json` from
    `tracelane.contracts`.
  - `validate_document` and `validate_document_date` from
    `tracelane.v2.schema`.
- Produces:
  - `EvidenceProject.create(...) -> EvidenceProject`
  - `EvidenceProject.from_dict(value) -> EvidenceProject`
  - `ProjectEvidenceCandidate.create(...) -> ProjectEvidenceCandidate`
  - `ProjectEvidenceCandidate.from_dict(value) -> ProjectEvidenceCandidate`
  - `EvidenceTransformation.create(...) -> EvidenceTransformation`
  - `EvidenceTransformation.from_dict(value) -> EvidenceTransformation`
  - `EvidenceImportRow`
  - `EvidenceImportMetadata.create(...) -> EvidenceImportMetadata`
  - `EvidenceImportMetadata.from_dict(value) -> EvidenceImportMetadata`
  - `candidate_record_digest(value) -> str`

`EvidenceImportRow` contains candidate ID, candidate record/content digests,
stable source-specification ID, source type, license basis, content authorship,
retention policy, sorted domains, sorted fact IDs, and role.
`EvidenceImportMetadata` contains schema identity, session ID, acquisition
manifest content digest, sorted rows, and its own content digest.

The exact Task 1 wire fields are binding:

```text
EvidenceProject
  schema_id = tracelane://schemas/evidence-project/v1
  schema_version = 1.0.0
  record_sha256: 64 lowercase hex
  project_id: lowercase [a-z][a-z0-9-]{2,63}
  title: non-empty string
  research_question: non-empty string
  historical_cutoff_at: canonical UTC datetime
  intervention: non-empty string
  required_domains: non-empty sorted unique tuple[str, ...]
  future_control_policy = exclude_from_clean
  admitted_source_types: non-empty sorted unique tuple of primary|secondary|dataset
  status: active|paused|completed|archived

ProjectEvidenceCandidate
  schema_id = tracelane://schemas/project-evidence-candidate/v1
  schema_version = 1.0.0
  record_sha256: 64 lowercase hex
  project_id: lowercase project ID
  candidate_id: candidate_<24 lowercase hex>
  source_spec_id: lowercase [a-z][a-z0-9_]{2,63}
  query: non-empty string
  title: non-empty string
  source_url: canonical source URL
  document_date: validated against date_precision
  date_precision: day|month|year|estimated
  retrieved_at: canonical UTC datetime
  curator: non-empty string
  source_type: primary|secondary|dataset
  role: evidence|future-control
  domains: non-empty sorted unique tuple[str, ...]
  fact_ids: non-empty sorted unique tuple[str, ...]
  content_ref: ArtifactRef(kind=evidence_blob, schema_id absent,
               URI under tracelane://evidence/blobs/sha256/)
  transformation_refs: ordered unique tuple of
               ArtifactRef(kind=evidence_transformation, schema_id absent)
  content_sha256: equal to content_ref.sha256
  content_authorship: repository_authored|third_party
  retention_policy:
               paraphrase_only|public_domain_full_text|licensed_full_text
  license_basis: non-empty string
  acquisition_session_id: acquisition session ID
  source_candidate_uri: original safe tracelane://artifacts/... candidate URI
  source_candidate_id: equal to candidate_id
  source_candidate_record_sha256: 64 lowercase hex
  source_candidate_content_sha256: equal to content_sha256
  trust_level = untrusted_external

EvidenceTransformation
  schema_id = tracelane://schemas/evidence-transformation/v1
  schema_version = 1.0.0
  record_sha256: 64 lowercase hex
  transformation_id: transformation_<24 lowercase hex>
  project_id: lowercase project ID
  candidate_id: candidate_<24 lowercase hex>
  transformation_type:
               manual_excerpt|repository_paraphrase|translation|ocr|normalization
  input_ref: ArtifactRef(kind=evidence_blob, schema_id absent)
  output_ref: ArtifactRef(kind=evidence_blob, schema_id absent)
  actor: non-empty string
  method: non-empty string
  parameters: canonical JSON object whose recursively nested values are
              null, bool, int, finite float, str, list, or object
  created_at: canonical UTC datetime
  license_implications: non-empty string

EvidenceImportRow
  source_spec_id
  candidate_id
  candidate_record_sha256
  candidate_content_sha256
  source_type
  license_basis
  content_authorship
  retention_policy
  domains
  fact_ids
  role

EvidenceImportMetadata
  schema_id = tracelane://schemas/evidence-import-metadata/v1
  schema_version = 1.0.0
  content_sha256: digest of all other fields
  project_id
  session_id
  manifest_sha256: acquisition manifest content_sha256
  candidates: sorted unique tuple[EvidenceImportRow, ...] by candidate_id
```

Cross-field rules:

- candidate ID is recomputed with the existing
  `acquisition.compute_candidate_id` formula;
- `repository_authored` pairs only with `paraphrase_only`;
- `third_party` pairs only with `public_domain_full_text` or
  `licensed_full_text`;
- candidate source/content lineage IDs and digests must agree;
- input and output transformation refs must be different content identities;
- no Task 1 contract compares a candidate date with a project cutoff because
  the project is not an argument to candidate parsing; Task 4 project
  verification enforces that relationship;
- all `to_dict()` methods revalidate stale dataclass instances; and
- no current/effective status field is serialized on a candidate.

- [ ] **Step 1: Write failing positive and negative contract tests**

Create tests that exercise public constructors, round trips, stale
`record_sha256`, invalid project IDs, invalid cutoff timestamps, mismatched
candidate lineage, duplicate facts/domains, invalid retention combinations,
future-control roles, and invalid typed transformation refs.

```python
def test_project_candidate_round_trip(candidate_input):
    candidate = ProjectEvidenceCandidate.create(**candidate_input)
    assert ProjectEvidenceCandidate.from_dict(candidate.to_dict()) == candidate
    assert "status" not in candidate.to_dict()


def test_third_party_content_requires_positive_retention(candidate_input):
    candidate_input["content_authorship"] = "third_party"
    candidate_input["retention_policy"] = "paraphrase_only"
    with pytest.raises(ValueError, match="retention policy"):
        ProjectEvidenceCandidate.create(**candidate_input)


def test_candidate_role_is_schema_bound(candidate_input):
    candidate_input["role"] = "control"
    with pytest.raises(ValueError, match="role"):
        ProjectEvidenceCandidate.create(**candidate_input)
```

- [ ] **Step 2: Run the contract tests and accept RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_contracts.py -v
```

Expected: collection fails because `tracelane.evidence_registry` does not
exist.

- [ ] **Step 3: Add exact schemas**

Use Draft 2020-12 schemas with `additionalProperties: false`, stable `$id`
values, and required digest fields.

The project schema requires:

```json
{
  "schema_id": "tracelane://schemas/evidence-project/v1",
  "schema_version": "1.0.0",
  "record_sha256": "<64 lowercase hex>",
  "project_id": "hist-001",
  "title": "<non-empty>",
  "research_question": "<non-empty>",
  "historical_cutoff_at": "1812-06-23T23:59:59Z",
  "intervention": "<non-empty>",
  "required_domains": ["diplomacy"],
  "future_control_policy": "exclude_from_clean",
  "admitted_source_types": ["primary"],
  "status": "active"
}
```

The candidate schema requires identity, source, content, interpretation,
rights, and lineage fields from the approved design. It allows:

```text
role: evidence | future-control
source_type: primary | secondary | dataset
content_authorship: repository_authored | third_party
retention_policy:
  paraphrase_only | public_domain_full_text | licensed_full_text
```

`source_spec_id` is a required lowercase identifier such as
`hist001_tilsit_treaty`. Multiple dated candidates from one source
specification share this field while retaining distinct candidate IDs.

The transformation schema requires typed input/output ArtifactRefs and:

```text
transformation_type:
  manual_excerpt | repository_paraphrase | translation | ocr | normalization
```

The import-metadata schema contains `session_id`, `manifest_sha256`,
`content_sha256`, and sorted candidate metadata rows keyed by candidate ID.

- [ ] **Step 4: Implement frozen contracts**

Use a single digest helper that excludes only `record_sha256`:

```python
def candidate_record_digest(value: Mapping[str, object]) -> str:
    payload = {
        str(key): item
        for key, item in value.items()
        if key != "record_sha256"
    }
    return sha256_json(payload)
```

Each `create()` builds a raw mapping with an empty digest, calculates the
digest, then calls `from_dict()`. Each `from_dict()` validates schema first,
constructs typed nested ArtifactRefs, and rechecks every semantic invariant.
Each `to_dict()` reconstructs the mapping, revalidates semantics and schema,
and rejects stale instances produced by `dataclasses.replace`.

Use sorted unique tuples for facts, domains, source types, and metadata rows.
Reject an input whose original ordering or duplicates would make the wire
identity ambiguous rather than silently reordering caller input.

- [ ] **Step 5: Add schema/Python parity tables**

For every semantic rule changed by Python, include a corresponding JSON Schema
case where expressible. Cover all enums, digest patterns, required fields,
additional properties, duplicate arrays, and ArtifactRef definitions.

Run:

```powershell
.\.venv\Scripts\python.exe scripts\sync_v2_schema_defs.py
.\.venv\Scripts\python.exe scripts\sync_v2_schema_defs.py --check
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_contracts.py tests\v2\test_schema.py -v
```

Expected: PASS.

- [ ] **Step 6: Review and commit Task 1**

Run Ruff on the new package and tests, inspect the diff, and commit:

```powershell
git add src/tracelane/evidence_registry src/tracelane/schemas/v2 tests/v2/test_evidence_registry_contracts.py tests/v2/test_schema.py
git commit -m "feat: add evidence registry contracts"
```

---

### Task 2: Hardened Evidence Root and Blob Storage

**Files:**

- Create: `src/tracelane/evidence_registry/storage.py`
- Create: `tests/v2/test_evidence_registry_storage.py`
- Modify: `src/tracelane/evidence_registry/__init__.py`

**Interfaces:**

- Consumes:
  - `ArtifactRef` from Task 1 dependencies.
  - `atomic_create_bytes`, `atomic_write_bytes`, and `secure_read_bytes` from
    `tracelane.v2.storage`.
  - `assert_safe_tree` from `tracelane.security`.
- Produces:
  - `EvidenceRoot.open(path: str | Path) -> EvidenceRoot`
  - `EvidenceRoot.create(path: str | Path) -> EvidenceRoot`
  - `EvidenceRoot.resolve(uri: str, *, must_exist: bool = False) -> Path`
  - `EvidenceBlobStore.put_bytes(data, media_type, kind) -> ArtifactRef`
  - `EvidenceBlobStore.verify(reference) -> Path`
  - `read_json_object(root, reference, *, expected_kind, expected_schema_id)`
  - `write_json_create_or_match(root, uri, kind, schema_id, value) -> ArtifactRef`

- [ ] **Step 1: Write failing resolver and corruption tests**

Cover:

- valid project, project-index, candidate, review, transformation, and blob
  URIs;
- logical blob URI mapping from
  `tracelane://evidence/blobs/sha256/<sha256>` to the physical
  `evidence/blobs/sha256/<first-two-hex>/<sha256>.blob` path;
- wrong URI root;
- slash, backslash, percent-encoding, dot segment, absolute path, and escape;
- symlink, Windows junction, and reparse point;
- missing root without mutation;
- missing, replaced, truncated, and hardlinked blob;
- same-byte idempotence;
- different existing bytes;
- stale open descriptor and parent replacement; and
- errors that omit absolute local paths.

```python
def test_open_missing_root_does_not_create_it(tmp_path):
    root = tmp_path / "evidence"
    with pytest.raises(ValueError, match="evidence root is unavailable"):
        EvidenceRoot.open(root)
    assert not root.exists()
```

- [ ] **Step 2: Run storage tests and accept RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_storage.py -v
```

Expected: import failure for the missing storage module.

- [ ] **Step 3: Implement the evidence URI resolver**

Use the exact prefix:

```python
_EVIDENCE_PREFIX = "tracelane://evidence/"
```

`open()` must not create a path. `create()` may create only the exact supplied
root after confirming no supplied ancestor component is a link or reparse
point. `resolve()` must validate every descendant with `lstat`, enforce
containment, and avoid returning local paths in public errors.

- [ ] **Step 4: Implement create-new blob and JSON publication**

Blob references use the stable logical URI:

```python
digest = hashlib.sha256(data).hexdigest()
uri = f"tracelane://evidence/blobs/sha256/{digest}"
```

`EvidenceRoot.resolve()` maps that logical URI to
`blobs/sha256/{digest[:2]}/{digest}.blob` beneath the evidence root. The
physical sharding and `.blob` suffix are never serialized into `ArtifactRef`
URIs.

For an existing target, authenticate bytes and accept only exact identity.
Never overwrite. JSON helpers serialize with:

```python
canonical_json(value).encode("utf-8") + b"\n"
```

`canonical_json()` does not include a newline, so the helper appends exactly
one.

- [ ] **Step 5: Run focused storage tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_storage.py tests\v2\test_storage.py -v
.\.venv\Scripts\python.exe -m ruff check src\tracelane\evidence_registry\storage.py tests\v2\test_evidence_registry_storage.py
```

Expected: PASS with no v2 storage regression.

- [ ] **Step 6: Review and commit Task 2**

```powershell
git add src/tracelane/evidence_registry/storage.py src/tracelane/evidence_registry/__init__.py tests/v2/test_evidence_registry_storage.py
git commit -m "feat: add evidence registry storage"
```

---

### Task 3: Append-only Reviews and Effective Status

**Files:**

- Create: `src/tracelane/evidence_registry/reviews.py`
- Create: `src/tracelane/schemas/v2/evidence-review.schema.json`
- Create: `tests/v2/test_evidence_registry_reviews.py`
- Modify: `src/tracelane/evidence_registry/__init__.py`

**Interfaces:**

- Consumes:
  - `ProjectEvidenceCandidate` from Task 1.
  - `write_json_create_or_match` and `read_json_object` from Task 2.
- Produces:
  - `EvidenceReview.create(...) -> EvidenceReview`
  - `EvidenceReview.from_dict(value) -> EvidenceReview`
  - `validate_review_chain(candidate, reviews) -> ReviewChain`
  - `effective_status(candidate, reviews) -> Literal["pending", "approved", "rejected", "superseded"]`
  - `current_review(candidate, reviews) -> EvidenceReview | None`
  - `append_review(root, review) -> ArtifactRef`

`ReviewChain` is:

```python
@dataclass(frozen=True)
class ReviewChain:
    ordered: tuple[EvidenceReview, ...]
    head: EvidenceReview | None
    effective_status: Literal["pending", "approved", "rejected", "superseded"]
```

The exact `EvidenceReview` wire contract is:

```text
schema_id = tracelane://schemas/evidence-review/v1
schema_version = 1.0.0
record_sha256: lowercase SHA-256 over the record without record_sha256
review_id: review_<24 lowercase hex>, derived from the business identity
project_id
candidate_id
candidate_record_sha256
decision: approved | rejected | superseded
reason
reviewer
reviewed_at: canonical UTC date-time
approved_fact_ids: sorted unique strings
approved_domains: sorted unique strings
license_basis: exact reviewed candidate value
retention_policy: exact reviewed candidate value
supersedes_review_id: review_<24 lowercase hex>, omitted for the first review
```

The business identity used for `review_id` contains every field from
`project_id` through `supersedes_review_id`, omitting the optional predecessor
when absent. It excludes schema metadata, `review_id`, and `record_sha256`.
The complete record digest includes the derived `review_id`.

For a review bound to the current candidate digest, `license_basis` and
`retention_policy` must equal the candidate. An approved review has non-empty
approved fact/domain sets that are subsets of the candidate. Rejected and
superseded decisions have empty approved sets. Historical reviews bound to an
older valid candidate digest remain structurally loadable; only a chain head
bound to the current candidate digest produces a non-pending effective status.

- [ ] **Step 1: Write failing lifecycle tests**

Cover no-review pending, first approval/rejection, a valid superseding review,
missing predecessor, non-head predecessor, fork, stale candidate digest,
cross-project/cross-candidate review, reduced facts/domains, invalid retention
decision, and replacement of an existing review file.

```python
def test_candidate_change_invalidates_review(candidate, approved_review):
    changed = dataclasses.replace(candidate, license_basis="changed")
    with pytest.raises(ValueError, match="candidate record hash"):
        effective_status(changed, [approved_review])
```

Also test a valid revised candidate with a recomputed digest: the historical
review remains loadable but the effective status is `pending`.

- [ ] **Step 2: Run review tests and accept RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_reviews.py -v
```

Expected: missing review module.

- [ ] **Step 3: Implement review contract and deterministic chain**

Review IDs are content-derived:

```python
review_id = make_object_id("review", identity_without_record_digest)
```

The review schema permits decisions `approved`, `rejected`, and `superseded`.
Approval requires non-empty approved fact IDs and domains that are subsets of
the candidate. Rejection and superseded reviews store empty approved sets.
`supersedes_review_id` is absent on the first review and required thereafter.

Chain validation starts from the unique review without a predecessor, walks
successors, and rejects zero roots, multiple roots, forks, cycles, gaps, and a
head bound to another current candidate digest.

- [ ] **Step 4: Implement append-only publication**

The URI is:

```python
f"tracelane://evidence/projects/{project_id}/reviews/{review_id}.json"
```

Publication is create-new or exact-match only. There is no update or delete
API. Re-review creates another record naming the current head.

- [ ] **Step 5: Run review/schema parity tests**

```powershell
.\.venv\Scripts\python.exe scripts\sync_v2_schema_defs.py
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_reviews.py tests\v2\test_evidence_registry_contracts.py tests\v2\test_schema.py -v
```

Expected: PASS.

- [ ] **Step 6: Review and commit Task 3**

```powershell
git add src/tracelane/evidence_registry src/tracelane/schemas/v2/evidence-review.schema.json tests/v2/test_evidence_registry_reviews.py
git commit -m "feat: add evidence review lifecycle"
```

---

### Task 4: Deterministic Indexes, Verification, and Queries

**Files:**

- Create: `src/tracelane/evidence_registry/index.py`
- Create: `src/tracelane/schemas/v2/evidence-project-index.schema.json`
- Create: `src/tracelane/schemas/v2/evidence-registry.schema.json`
- Create: `tests/v2/test_evidence_registry_index.py`
- Modify: `src/tracelane/evidence_registry/__init__.py`

**Interfaces:**

- Consumes Task 1 contracts, Task 2 storage, and Task 3 review-chain functions.
- Produces:
  - `EvidenceIndexEntry`
  - `EvidenceProjectIndex`
  - `EvidenceRegistryEntry`
  - `EvidenceRegistry`
  - `build_project_index(root, project_id) -> EvidenceProjectIndex`
  - `rebuild_project_index(root, project_id) -> ArtifactRef`
  - `build_registry(root) -> EvidenceRegistry`
  - `rebuild_registry(root) -> ArtifactRef`
  - `verify_evidence_registry(root, project_id=None) -> VerificationReport`
  - `find_evidence(root, query: EvidenceQuery) -> tuple[EvidenceIndexEntry, ...]`

The cross-task value types are:

```python
@dataclass(frozen=True)
class EvidenceQuery:
    project_id: str
    statuses: tuple[str, ...] = ()
    fact_id: str | None = None
    domain: str | None = None
    role: str | None = None
    source_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    clean_only: bool = False


@dataclass(frozen=True)
class VerificationReport:
    project_count: int
    candidate_count: int
    review_count: int
    future_control_count: int
    status_counts: Mapping[str, int]
    registry_sha256: str
    project_index_sha256: str | None
```

`EvidenceIndexEntry` stores the searchable fields named in the approved design.
`EvidenceProjectIndex` stores project ID, sorted entries, status counts, and
record digest. `EvidenceRegistryEntry` stores project refs and digests.
`EvidenceRegistry` stores sorted project entries and its record digest.

The exact index/registry wire shapes are:

```text
EvidenceIndexEntry
  candidate_id
  candidate_ref: ArtifactRef(kind=evidence_candidate,
                  schema_id=tracelane://schemas/project-evidence-candidate/v1)
  effective_status: pending | approved | rejected | superseded
  current_review_ref: optional ArtifactRef(
                        kind=evidence_review,
                        schema_id=tracelane://schemas/evidence-review/v1)
  document_date
  date_precision
  source_type
  role
  domains: sorted unique strings
  fact_ids: sorted unique strings
  content_sha256
  license_class: the candidate retention_policy
  transformation_ids: sorted unique transformation IDs

EvidenceProjectIndex
  schema_id = tracelane://schemas/evidence-project-index/v1
  schema_version = 1.0.0
  record_sha256
  project_id
  entries: sorted unique by candidate_id
  status_counts: exact keys pending, approved, rejected, superseded

EvidenceRegistryEntry
  project_id
  title
  status
  project_ref: ArtifactRef(kind=evidence_project,
               schema_id=tracelane://schemas/evidence-project/v1)
  index_ref: ArtifactRef(kind=evidence_project_index,
             schema_id=tracelane://schemas/evidence-project-index/v1)

EvidenceRegistry
  schema_id = tracelane://schemas/evidence-registry/v1
  schema_version = 1.0.0
  record_sha256
  projects: sorted unique by project_id
```

`record_sha256` always covers the complete wire object except itself.
`VerificationReport.registry_sha256` and `project_index_sha256` are the
SHA-256 values of the canonical persisted JSON bytes from their
`ArtifactRef`s, not the embedded record digests. `project_index_sha256` is
present only for a project-scoped verification.

- [ ] **Step 1: Write failing deterministic-index tests**

Build a project with approved, rejected, superseded, pending, and
future-control candidates. Assert byte-identical rebuild, canonical sort order,
derived current review, no build timestamp, complete inventory, and filters for
status, fact, domain, role, source type, date, and date range.

```python
def test_clean_query_excludes_future_control(registry_root):
    values = find_evidence(
        registry_root,
        EvidenceQuery(project_id="hist-001", clean_only=True),
    )
    assert all(item.role != "future-control" for item in values)
```

Add mutation tests for an unindexed candidate, a ghost index entry, hand-edited
status, stale current review ref, duplicated candidate ID, and an extra JSON
file in a managed directory.

- [ ] **Step 2: Run index tests and accept RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_index.py -v
```

Expected: missing index module.

- [ ] **Step 3: Implement index and registry contracts**

Project index entries contain only derived searchable fields. The index digest
covers all fields except its own `record_sha256`. Entries are sorted by
candidate ID; every tuple field is sorted and unique.

Global registry entries contain project ID, title, status, project/index refs,
digests, and sizes, sorted by project ID.

- [ ] **Step 4: Implement inventory-first verification**

Verification order:

1. open and authenticate evidence root;
2. enumerate managed directories with link/reparse checks;
3. load project;
4. load every candidate and transformation;
5. authenticate every content reference;
6. load every review and validate chains;
7. derive expected index;
8. compare expected and persisted index bytes;
9. derive expected global registry; and
10. compare persisted global registry bytes.

Do not trust persisted index fields to locate source records.

The managed project inventory allows only `README.md`, `project.json`,
`index.json`, and the `candidates`, `reviews`, and `transformations`
directories. Within those three managed directories, every regular JSON file
must be consumed exactly once by the source-derived index; extra files,
unsupported extensions, links/reparse points, and unreferenced
review/transformation records are errors. Unreferenced global content blobs
are permitted because an interrupted import may publish harmless
content-addressed blobs before project publication.

- [ ] **Step 5: Implement query filters**

`EvidenceQuery` has optional project ID, statuses, fact ID, domain, role,
source type, `date_from`, `date_to`, and `clean_only`. Date comparison respects
precision by converting year/month/day to conservative closed intervals.
`clean_only=True` always excludes future-control regardless of other filters.
Project verification compares every candidate date and precision with the
project cutoff and rejects post-cutoff candidates whose role is not
`future-control`.

Candidate dates and query bounds are closed intervals. Year/month/day values
expand to their complete calendar interval; `estimated` uses the granularity
present in `document_date`. A candidate matches a range when its possible
interval intersects the inclusive query interval. For cutoff enforcement, a
non-future-control candidate is admissible only when the end of its possible
interval is no later than the project cutoff.

- [ ] **Step 6: Run index, corruption, and schema checks**

```powershell
.\.venv\Scripts\python.exe scripts\sync_v2_schema_defs.py
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_index.py tests\v2\test_evidence_registry_reviews.py tests\v2\test_evidence_registry_storage.py tests\v2\test_schema.py -v
```

Expected: PASS.

- [ ] **Step 7: Review and commit Task 4**

```powershell
git add src/tracelane/evidence_registry src/tracelane/schemas/v2 tests/v2/test_evidence_registry_index.py
git commit -m "feat: add deterministic evidence indexes"
```

---

### Task 5: Authenticated Acquisition Snapshot and Project Importer

**Files:**

- Modify: `src/tracelane/acquisition/contracts.py`
- Modify: `src/tracelane/acquisition/service.py`
- Modify: `src/tracelane/acquisition/__init__.py`
- Create: `src/tracelane/evidence_registry/importer.py`
- Create: `tests/v2/test_evidence_registry_importer.py`
- Modify: `tests/v2/test_acquisition.py`
- Modify: `src/tracelane/evidence_registry/__init__.py`

**Interfaces:**

- Produces in acquisition:
  - `AcquisitionCandidateClosure`
  - `ManualAcquisitionService.snapshot_candidates() -> tuple[AcquisitionCandidateClosure, ...]`
- Produces in importer:
  - `EvidenceImportReport`
  - `import_acquisition_project(source_root, target_root, project, metadata) -> EvidenceImportReport`

`AcquisitionCandidateClosure` contains:

```python
@dataclass(frozen=True)
class AcquisitionCandidateClosure:
    candidate_ref: ArtifactRef
    candidate: EvidenceCandidate
    candidate_bytes: bytes
    content_bytes: bytes
    transformations: tuple[tuple[ArtifactRef, bytes], ...]
```

`EvidenceImportReport` is:

```python
@dataclass(frozen=True)
class EvidenceImportReport:
    project_id: str
    candidate_count: int
    pending_count: int
    future_control_count: int
    source_manifest_sha256: str
    project_index_sha256: str
    registry_sha256: str
    source_candidate_ids: tuple[str, ...]
```

- [ ] **Step 1: Write failing public snapshot tests**

Assert a snapshot is returned only after the acquisition manifest, candidate
record, content blob, transformations, inventory, and lineage are
authenticated under the session lock. Mutate each closure member and assert
fail-closed with no target mutation.

- [ ] **Step 2: Run snapshot tests and accept RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_acquisition.py -k snapshot_candidates -v
```

Expected: `ManualAcquisitionService` has no `snapshot_candidates`.

- [ ] **Step 3: Implement the read-only acquisition snapshot**

Within the existing session lock:

1. reload and recover session state;
2. validate the full manifest and inventory;
3. secure-read each candidate record;
4. verify and secure-read content and transformation bytes;
5. revalidate the manifest and inventory; and
6. return immutable closure objects.

The method performs no write when the session is already clean.

- [ ] **Step 4: Write failing importer transaction tests**

Cover:

- nine valid candidate closures;
- metadata missing/extra candidate;
- source candidate/digest mismatch;
- retention policy violation;
- source mutation before and after snapshot;
- target project absent;
- identical rerun;
- different existing project;
- global blob deduplication;
- interruption before project publication;
- interruption after complete project publication but before global registry;
- concurrent importers; and
- source absolute path absent from every target byte and error.

- [ ] **Step 5: Implement staged project publication**

Use one evidence-root import lock. Build the complete project under a unique
verified sibling staging directory. Publish permitted global blobs through
create-new/exact-match. Validate the staged project and index using a staging
root with the same URI semantics. Rename the complete project directory into
`projects/{project_id}` only when the target is absent. Then publish or repair
the global registry last.

An existing complete identical project is authenticated and returned. A
different project is rejected. A crash before directory publication leaves
only a removable staging directory and possibly harmless content-addressed
blobs. A crash after directory publication is recovered by authenticating the
complete project and rebuilding the global registry.

- [ ] **Step 6: Run importer concurrency and recovery tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_importer.py tests\v2\test_acquisition.py -k "evidence_registry or snapshot_candidates" -v
```

Expected: PASS.

- [ ] **Step 7: Review and commit Task 5**

```powershell
git add src/tracelane/acquisition src/tracelane/evidence_registry tests/v2/test_acquisition.py tests/v2/test_evidence_registry_importer.py
git commit -m "feat: import acquisition evidence projects"
```

---

### Task 6: Machine-readable Preparation Metadata and Evidence CLI

**Files:**

- Modify: `scripts/prepare_hist001_candidates.py`
- Create: `scripts/import_hist001_evidence.py`
- Modify: `src/tracelane/cli.py`
- Create: `tests/v2/test_evidence_registry_cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**

- Consumes `EvidenceImportMetadata`, importer, query, rebuild, and verification
  APIs from Tasks 1–5.
- Produces:
  - external `candidate-metadata.json`;
  - `prepare(artifact_root) -> PreparationResult`;
  - CLI commands `evidence list`, `evidence find`, `evidence verify`, and
    `evidence rebuild-index`; and
  - `scripts/import_hist001_evidence.py --source PATH --target evidence`.

- [ ] **Step 1: Write failing metadata-output tests**

Generate an acquisition package in `tmp_path`. Require
`candidate-metadata.json` to contain exact candidate IDs, record/content
digests, stable source-specification ID, source type, license basis, content
authorship, retention policy, domains, fact IDs, and role.

Expected initial values:

```text
content_authorship = repository_authored
retention_policy = paraphrase_only
role = future-control only for the 29th Bulletin
```

The metadata file has its own `content_sha256`, contains nine rows, and stores
no absolute path.

- [ ] **Step 2: Run preparation test and accept RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_cli.py -k preparation -v
```

Expected: metadata file is missing.

- [ ] **Step 3: Implement canonical preparation metadata**

Return a frozen result:

```python
@dataclass(frozen=True)
class PreparationResult:
    review_path: Path
    metadata_path: Path
```

Keep the human Markdown `PENDING USER REVIEW`. Write metadata through canonical
JSON and validate it with `EvidenceImportMetadata.from_dict`.

- [ ] **Step 4: Write failing CLI tests**

Use `main([...])` and `capsys` to test:

```text
tracelane evidence list --root evidence --project hist-001 --status pending
tracelane evidence find --root evidence --project hist-001 --fact logistics.prewar_supply_plan
tracelane evidence verify --root evidence --project hist-001
tracelane evidence rebuild-index --root evidence --project hist-001
```

Test JSON output, stable text output, clean query exclusion, invalid date,
missing project, corrupt registry, and errors without absolute paths.

- [ ] **Step 5: Implement nested argparse commands**

Add an `evidence` parser with required nested command. Default `--root` is
`Path("evidence")`, but every test passes an explicit root. Query commands are
read-only. Rebuild updates the project index and global registry only after
source records verify.

- [ ] **Step 6: Implement the import script**

The script:

1. reads and validates external metadata;
2. constructs the locked HIST-001 `EvidenceProject`;
3. invokes `import_acquisition_project`;
4. verifies the final project and global registry; and
5. prints counts and digests, never the source absolute path.

- [ ] **Step 7: Run CLI and existing CLI regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_cli.py tests\test_cli.py -v
.\.venv\Scripts\python.exe -m ruff check scripts\prepare_hist001_candidates.py scripts\import_hist001_evidence.py src\tracelane\cli.py
```

Expected: PASS.

- [ ] **Step 8: Review and commit Task 6**

```powershell
git add scripts/prepare_hist001_candidates.py scripts/import_hist001_evidence.py src/tracelane/cli.py tests/v2/test_evidence_registry_cli.py tests/test_cli.py
git commit -m "feat: add evidence registry commands"
```

---

### Task 7: Import and Check In the HIST-001 Evidence Registry

**Files:**

- Create: `evidence/README.md`
- Create: `evidence/registry.json`
- Create: `evidence/projects/hist-001/README.md`
- Create: `evidence/projects/hist-001/project.json`
- Create: `evidence/projects/hist-001/index.json`
- Create: `evidence/projects/hist-001/candidates/*.json`
- Create: `evidence/blobs/sha256/*/*.blob`
- Create: `tests/v2/test_hist001_evidence_registry.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes the external generated candidate package and Tasks 1–6.
- Produces the tracked initial Evidence Registry with nine pending candidates.

- [ ] **Step 1: Write the failing checked-in data acceptance test**

```python
def test_hist001_registry_has_nine_pending_candidates():
    report = verify_evidence_registry(REPO_ROOT / "evidence", "hist-001")
    assert report.candidate_count == 9
    assert report.status_counts == {"pending": 9}
    assert report.future_control_count == 1
    assert report.review_count == 0


def test_hist001_clean_query_excludes_future_control():
    clean = find_evidence(
        REPO_ROOT / "evidence",
        EvidenceQuery(project_id="hist-001", clean_only=True),
    )
    assert len(clean) == 8
    assert all(item.role == "evidence" for item in clean)
```

Also assert seven distinct source specifications, required domains, exact
cutoff, intervention, no local absolute paths, no review files, no
`fixtures/v0.2`, and byte-identical index rebuild in a temporary copy.

- [ ] **Step 2: Run the acceptance test and accept RED**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_hist001_evidence_registry.py -v
```

Expected: `evidence/registry.json` is unavailable.

- [ ] **Step 3: Regenerate the external package with metadata**

Use the existing operator source path as a command argument:

```powershell
if (-not $env:TRACELANE_HIST001_SOURCE) {
  throw "TRACELANE_HIST001_SOURCE must name the external candidate root"
}
.\.venv\Scripts\python.exe scripts\prepare_hist001_candidates.py --artifact-root $env:TRACELANE_HIST001_SOURCE
```

Do not write the concrete absolute path into tracked files or command examples
committed to the repository.

- [ ] **Step 4: Import into the repository root**

```powershell
.\.venv\Scripts\python.exe scripts\import_hist001_evidence.py --source $env:TRACELANE_HIST001_SOURCE --target evidence
```

Expected output names only project ID, candidate count, pending count, and
registry/project digests.

- [ ] **Step 5: Add human documentation**

`evidence/README.md` explains evidence vs fixtures vs artifacts, statuses,
review retention, licensing, commands, and approval gate.

`evidence/projects/hist-001/README.md` lists the question, cutoff,
intervention, required domains, nine candidate titles, future-control role,
and current `9 pending / 0 approved` status.

Update the root README roadmap and usage without claiming a completed History
Fork or public fixture.

- [ ] **Step 6: Run checked-in data verification**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_hist001_evidence_registry.py tests\v2\test_evidence_registry_index.py -v
.\.venv\Scripts\tracelane.exe evidence verify --root evidence --project hist-001
.\.venv\Scripts\tracelane.exe evidence list --root evidence --project hist-001 --status pending
```

Expected: nine pending, one future control, no review, and PASS.

- [ ] **Step 7: Compare external and tracked identities**

Run the import script in verify mode and assert the nine source candidate IDs
and content digests match. Confirm the external package still exists. Do not
delete it in this task.

- [ ] **Step 8: Review and commit Task 7**

Scan only tracked source/data roots for credentials and local absolute paths.
Then commit:

```powershell
git add evidence README.md CHANGELOG.md tests/v2/test_hist001_evidence_registry.py
git commit -m "data: add hist-001 evidence candidates"
```

---

### Task 8: Integrity Matrix, Packaging Boundary, and Final Verification

**Files:**

- Modify: `tests/v2/test_evidence_registry_contracts.py`
- Modify: `tests/v2/test_evidence_registry_storage.py`
- Modify: `tests/v2/test_evidence_registry_reviews.py`
- Modify: `tests/v2/test_evidence_registry_index.py`
- Modify: `tests/v2/test_evidence_registry_importer.py`
- Modify: `tests/v2/test_hist001_evidence_registry.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**

- Consumes all prior tasks.
- Produces a reviewed Evidence Registry release candidate while leaving
  fixture approval gated.

- [ ] **Step 1: Add table-driven corruption matrices**

Through public APIs, mutate one field or file at a time:

- registry/project/index digest;
- candidate source, date, role, facts, domain, retention, lineage, and digest;
- content blob bytes, size, and path;
- transformation kind and input/output digest;
- review candidate digest, chain head, predecessor, fact scope, and decision;
- candidate inventory and review inventory;
- future-control role and cutoff;
- source package during import; and
- target namespace during publication.

Each case asserts exact exception class/category and unchanged accepted files.

- [ ] **Step 2: Add mutation-sensitivity tests for critical ordering**

Prove tests fail if:

- persisted index is trusted before source inventory;
- future-control filtering is removed;
- stale review digest checks are removed;
- different existing blobs are accepted;
- source revalidation after acquisition snapshot is removed;
- project publication precedes staged validation; or
- global registry publication precedes project validation.

Restore production code after every temporary mutation and verify a clean diff.

- [ ] **Step 3: Run all focused Evidence Registry tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_evidence_registry_contracts.py tests\v2\test_evidence_registry_storage.py tests\v2\test_evidence_registry_reviews.py tests\v2\test_evidence_registry_index.py tests\v2\test_evidence_registry_importer.py tests\v2\test_evidence_registry_cli.py tests\v2\test_hist001_evidence_registry.py -v
```

Expected: PASS.

- [ ] **Step 4: Run schema, format, lint, and full non-HIST gates**

```powershell
.\.venv\Scripts\python.exe scripts\sync_v2_schema_defs.py --check
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest --ignore=tests/v2/test_hist001_fixture.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Prove the fixture gate remains intentional**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\v2\test_hist001_fixture.py -v
```

Expected: the only failure is the missing, unapproved `fixtures/v0.2`. Any
schema, evidence-registry, loader, or provenance failure is a regression.

- [ ] **Step 6: Run final repository safety checks**

Verify:

- no staged file before final review;
- no `.local/runtime.json` status entry;
- no `artifacts/` status entry;
- no configured-secret or credential-shaped value in `src`, `tests`,
  `scripts`, `docs`, `evidence`, `README.md`, or `CHANGELOG.md`;
- no persisted external absolute path;
- `git diff --check` passes; and
- the external package remains untouched.

- [ ] **Step 7: Run three-way read-only review**

Request independent architecture, local data-integrity/security, and
testing/mutation-sensitivity reviews of one frozen complete worktree package.
Fix all Critical and Important findings. Record Minor findings or fix them when
they affect determinism, data integrity, or developer trust.

- [ ] **Step 8: Final commit and handoff**

If Task 8 changes tests or docs, commit them:

```powershell
git add tests README.md CHANGELOG.md
git commit -m "test: harden evidence registry verification"
```

Report:

- exact test counts;
- nine pending candidates;
- one future control;
- zero approvals and reviews;
- fixture gate status;
- review verdicts;
- commit list; and
- whether the external package is still present.

Do not push unless the user explicitly asks.
