# TraceLane v0.2 Internal Integrity Hardening

## 1. Goal

This change makes TraceLane's research artifacts internally consistent, reproducible, and fail-closed before the first public v0.2 benchmark fixture is frozen.

The system must be able to answer:

- Which exact case, evidence set, harness, model runtime, grader set, repeat, and code revision produced a run?
- Were any referenced artifacts changed, omitted, duplicated, truncated, or substituted?
- Is a trace structurally and semantically valid, and is every event still in the original order?
- Did a human approve the exact acquisition candidate that was promoted?
- Does a migrated v1 run still represent the source run it claims to represent?

This is an internal consistency design, not an adversarial authenticity system.

## 2. Scope and explicit exclusions

### Included

- Run identity, manifest, artifact references, and checksum coverage.
- Trace event identity, hash chaining, semantic validation, and append safety.
- Manual/Codex-assisted acquisition candidate integrity and approval binding.
- Historical case, evidence, provenance, licensing, and cross-reference validation.
- v1-to-v2 migration validation.
- JSON Schema and Python validation consistency.
- Secret and personal-data redaction at persistence boundaries.
- Tests for corruption, mismatch, truncation, substitution, and stale writers.

### Excluded

- No Brave Search API or other automated search provider.
- No PKI, certificates, external signatures, or key management.
- No automatic harness optimization or reward-model training in this change.
- No public HIST-001 fixture promotion until the candidate set is explicitly approved.

## 3. Trust model

TraceLane will detect accidental corruption, partial edits, stale or mismatched artifacts, and local tampering that does not rewrite every dependent digest consistently.

TraceLane will not claim to resist an attacker who can rewrite all artifacts, all hashes, the repository, and Git history. For published experiments, the Git commit and release digest are the external publication anchor. That is a practical boundary for v0.2 and does not require PKI.

All integrity failures are fatal for loading, promotion, migration, or benchmark scoring. Trace observability is part of the benchmark evidence, so trace integrity failures also fail closed.

## 4. Canonical serialization and digest rules

- JSON digests use UTF-8 canonical JSON: sorted keys, compact separators, no insignificant whitespace.
- File digests use the exact stored bytes.
- SHA-256 values are lowercase, 64-character hexadecimal strings.
- Every digest formula is defined by an explicit field projection. A stored digest field is never included in its own digest.
- Paths in persisted references use normalized, repository-relative artifact URIs. Absolute paths, parent traversal, drive-qualified paths, UNC paths, and URI schemes outside the approved artifact scheme are rejected.

## 5. Run integrity

### 5.1 Persisted execution fingerprint

`RunManifest` stores the complete `ExecutionFingerprint`, containing:

- case digest;
- evidence manifest digest;
- harness digest;
- runtime/model configuration digest;
- grader-set digest;
- repeat index;
- code revision.

`run_id` is derived from the canonical fingerprint. Loading a run recomputes both the component digests and `run_id`; a mismatch is fatal.

The five component digests must equal the SHA-256 values in the corresponding case, evidence-manifest, harness-config, runtime-config, and grader-set references after those references have been verified against disk. `code_revision` exists only once, inside the fingerprint.

### 5.2 Artifact references

Every non-null `ArtifactRef` is validated against:

- normalized URI;
- expected artifact kind;
- expected schema identifier and version;
- exact byte size;
- exact SHA-256 of the stored bytes.

An artifact reference may not escape its declared artifact root. A reference expected to belong to the run must resolve inside that run directory.

### 5.3 Checksum completeness

`checksums.json` covers exactly every regular file below the run directory except `manifest.json` and `checksums.json`.

Validation rejects:

- a present file missing from the checksum list;
- a checksum entry whose file is absent;
- duplicate or non-normalized paths;
- a file whose size or digest differs;
- an unexpected extra file;
- a referenced run artifact not covered by checksums.

`checksums_ref` must point to the run's single `checksums.json` and must itself match its bytes.

### 5.4 Lifecycle invariants

Run states and timestamps are consistent:

- `created` and `running` have no `completed_at`;
- `completed` and `failed` have `completed_at >= started_at`;
- a terminal manifest is immutable;
- a completed run contains all required outputs and their references;
- a failed run contains its failure record and any partial trace that was successfully persisted.

## 6. Trace integrity

### 6.1 Event hash chain

Each `TraceEventV2` adds:

- `previous_event_sha256`: null for the first event, otherwise the prior event's content digest;
- `content_sha256`: SHA-256 of the canonical event projection.

The event projection includes all persisted event fields except `event_id` and `content_sha256`, and therefore includes `previous_event_sha256`.

`event_id` is `evt_` followed by the full `content_sha256`. It is not an independent random identifier.

The completed trace file is also anchored by its `ArtifactRef` and run checksum. The event chain detects editing or reordering inside the trace; the file digest detects suffix truncation and byte-level replacement after completion.

### 6.2 Structural and relational validation

Both append-time validation and the public `read_trace` path enforce:

- one run identifier throughout the file;
- consecutive sequence numbers starting at the declared initial value;
- valid previous-event hash links;
- unique event identifiers;
- valid timestamps and enum values;
- causal and parent references point only to existing earlier events;
- no self-reference or forward-reference;
- declared span relationships are internally consistent.

The public reader never returns events after performing only JSON parsing. Semantic validation is mandatory.

### 6.3 Event payload contracts

The wire contract defines minimum payload fields per event type:

- `model.called`: `turn`, `runtime_id`;
- `model.observed`: `turn`, `tool_call_count`, `has_output`, `input_tokens`, `output_tokens`, `cached_tokens`, `latency_ms`;
- `tool.called`: `call_id`, `tool_name`, `arguments`;
- `tool.observed`: `call_id`, `tool_name`, `output`, `is_error`, `error_code`;
- `run.started`: `status` with the constant value `running`;
- `run.completed`: `status` with the constant value `completed`;
- `stage.started`: `stage_id`;
- `stage.completed`: `stage_id`;
- `stage.failed`: `stage_id`, `error_code`.

All other registered event types require an object payload but do not gain new mandatory keys in this hardening change. Additional fields remain allowed for forward compatibility, but required fields and primitive types are validated. JSON Schema is the wire authority; Python validators must accept and reject the same fixtures.

### 6.4 Append and concurrency behavior

A recorder remembers the last validated sequence and digest. Before every append it verifies that the on-disk file has not advanced or changed since that state.

If another writer advanced the file, the recorder must either rescan and adopt the new valid tail under an exclusive file lock or fail with a stale-writer error. It may never reuse a sequence number or append against an unverified tail.

The initial v0.2 implementation uses a single-writer file lock and atomic append discipline. Multi-process write interleaving is rejected rather than repaired heuristically.

## 7. Acquisition integrity

### 7.1 Manual acquisition boundary

Codex or a human discovers source URLs and writes curated candidates. The benchmark agent receives only frozen offline tools backed by promoted artifacts.

TraceLane does not claim that a URL alone is a raw webpage snapshot. Each candidate distinguishes:

- source locator and metadata;
- exact curated text stored in the blob store;
- who or what curated it;
- acquisition time;
- document/event date and date precision;
- transformations applied to the curated text.

### 7.2 Candidate identity and loading

Candidate identifiers are recomputed from the canonical projection of query,
title, canonical source locator, document date, date precision, and
curated-content SHA-256. Curator identity and ordered transformation
references remain bound by the candidate-record digest, not by the candidate
identifier. `from_dict` rejects a stored identifier that does not match.

The candidate identifier is validated against its strict grammar before any path is constructed. All candidate, review, and promoted paths resolve through `ArtifactRoot`; parent traversal, separators, drive-qualified paths, and UNC paths are rejected.

Opening an existing acquisition session validates the session manifest, its self-digest, session identifier, and all referenced candidate and blob artifacts before reuse.

### 7.3 Approval binding

A review decision binds the exact:

- candidate identifier;
- candidate-record digest;
- curated-content blob digest;
- source locator identity;
- reviewer identity;
- decision and review timestamp.

Promotion revalidates all bound values and rereads the blob before writing evidence. Editing a candidate after approval invalidates the review.

### 7.4 Content safety

Before curated content, URLs, titles, or notes are persisted, the persistence boundary scans and redacts:

- configured secret values;
- API-key and bearer-token patterns;
- sensitive URL query parameters;
- email addresses and phone numbers where they are not explicit benchmark evidence;
- local absolute, drive-qualified, and UNC paths.

External source text remains untrusted data. It is never interpreted as an instruction by the benchmark agent or acquisition loader.

### 7.5 Dates

One durable evidence record represents one source item with one document/event date and explicit date precision. A composite candidate that spans multiple source items or dates must be split before promotion; ambiguous compound dates are rejected.

## 8. Historical evidence integrity

### 8.1 Provenance digest

`provenance_sha256` is recomputed from the canonical projection of every semantic evidence field:

- evidence identifier;
- document/event date and precision;
- `available_at` and `known_by_cutoff`;
- source type, title, locator, and stable source identity;
- curator identity;
- exact candidate-record and approval-review references and digests;
- curated-content reference;
- ordered transformation references;
- ordered fact identifiers;
- license classification;
- excerpt kind.

The stored `provenance_sha256` is excluded from that projection. Loading or promotion rejects an arbitrary or stale value.

### 8.2 Case and evidence manifest binding

The case's `evidence_manifest_ref` must match the exact loaded evidence manifest bytes, kind, and schema. Evidence manifests must point to the intended case identifier and version.

Admitted and rejected evidence are both verified:

- identifiers are unique;
- admitted and rejected sets are disjoint;
- all content and transformation references resolve and match;
- fact identifiers are unique and consistently referenced;
- admitted `available_at` values satisfy the case cutoff;
- rejected-future `available_at` values are after the cutoff and their records declare `known_by_cutoff: unavailable`.

When the HIST-001 candidate set is approved for promotion, its fixture-specific
release gate must assert that the named future-leakage control exists only in
the rejected set and retains its approved rejection rationale. This remains a
fixture/promotion gate rather than a generic v2 evidence-schema requirement.

License values must come from the exact published license enum. Unknown labels do not silently map to a permissive value.

### 8.3 Acquisition archive closure

Freezing promoted evidence preserves the exact acquisition bytes and their
existing `tracelane://artifacts/...` URIs. It does not relocate documents into a
new fixture-native namespace, rewrite references, recompute candidate or review
digests, or require a second approval.

The archived closure contains the promoted evidence record and every artifact
required to validate it:

- the exact candidate record named by `candidate_ref`;
- the exact approval review named by `review_ref`;
- the curated-content blob;
- every ordered transformation artifact;
- the promoted evidence record itself.

Each source artifact is verified before copying. The target fixture root
recreates the artifact's original URI-relative path and publishes the same
bytes through the hardened create-new boundary. An existing target is accepted
only when its bytes and reference metadata are identical, making archival
idempotent without silently overwriting a different artifact.

Historical loading resolves these preserved references relative to the frozen
fixture root and validates the complete candidate-to-review-to-evidence graph.
It does not impose a second path convention that would require rewriting an
authenticated record. Acquisition-session reopening may still enforce its
session-native deterministic paths because it validates the live acquisition
workspace rather than an archived fixture.

This protocol prepares a verifiable archive only. It does not create or approve
the public HIST-001 fixture; that promotion remains blocked on explicit user
approval.

## 9. Migration integrity

When a v1 migration target already exists, the migrator validates rather than blindly reuses it.

It recomputes and checks:

- the SHA-256 identity of the normalized absolute source root;
- source run identifier;
- the import identifier derived from source format, source run identifier,
  normalized source-root identity, and the authenticated entry inventory;
- target manifest schema and kind;
- every migrated entry and its digest;
- target artifact root;
- the relationship between the requested source and the existing target.

An unrelated or stale target is rejected. Individual payload files are written
atomically and the manifest is the atomic completion marker. A partial target
without that marker is verified against the requested source before deterministic
resume; it is never treated as a completed import.

## 10. Schema and validation consistency

- JSON Schema validation enables format checking, including RFC 3339 `date-time`.
- Regex constraints are fully anchored.
- The canonical `ArtifactRef` schema is defined once and embedded into each wire schema through a generated `$defs` block.
- A contract test compares every embedded `ArtifactRef` definition with the canonical definition so copies cannot drift.
- Python domain validators and JSON Schema validators share positive and negative fixture tables.
- Unknown trace payload fields are permitted for forward compatibility, but known secret and personal-data patterns are classified as restricted and redacted before persistence.

Because v2 is not yet released, these contracts are corrected in place. v1 remains readable and unchanged. Ignored local v2 acquisition sessions may be regenerated instead of receiving a public migration.

## 11. Test strategy

Every repair starts with a failing test and then the smallest implementation that makes it pass.

Required negative tests include:

- run fingerprint substitution;
- missing, extra, duplicate, and modified checksum files;
- wrong artifact kind, schema, size, digest, or escaped URI;
- trace event mutation, deletion, insertion, reordering, truncation, broken causation, invalid payload, and stale concurrent append;
- candidate path traversal, UNC path, record substitution, blob substitution, stale approval, and invalid reopened session;
- arbitrary provenance digest, manifest substitution, overlapping admitted/rejected evidence, invalid license, ambiguous date, and future evidence admitted;
- unrelated pre-existing migration target;
- invalid date-time accepted by shape-only validation;
- API keys, bearer tokens, sensitive URL parameters, email/phone values, and local paths reaching persisted artifacts.

Positive tests cover a complete acquisition-to-promotion path, a complete historical fixture load, a complete run finalization, and a valid v1 migration.

## 12. Verification and release gates

Before implementation is considered ready:

1. all v2 unit and adversarial integrity tests pass;
2. all existing v1 regression tests pass;
3. Ruff lint and format checks pass;
4. tracked files contain no configured secret or API-key pattern;
5. the same architecture, security, and testing cross-review is repeated;
6. every actionable review finding is fixed or explicitly rejected with evidence;
7. the full suite passes after the final review.

The missing HIST-001 frozen fixture remains an intentional product gate until
the user approves the candidate set. Hardening may be verified, reviewed, and
prepared or committed independently after its own complete gate. No public
HIST-001 fixture promotion or final v0.2 benchmark claim is made before
explicit candidate approval and the fixture-specific release gate.
