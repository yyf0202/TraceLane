# Project Evidence Registry Design

**Date:** 2026-07-25
**Status:** Approved
**Scope:** Repository-tracked research evidence registry and the initial
HIST-001 candidate import

## 1. Purpose

TraceLane needs a durable research evidence layer between acquisition and
benchmark fixtures. Acquisition sessions are working records. Fixtures are
approved, frozen benchmark inputs. Neither is a suitable home for the complete
research process, including pending, rejected, and superseded candidates.

This change adds a repository-root `evidence/` registry that:

- organizes evidence by research project;
- retains every candidate and review decision;
- supports deterministic machine indexing;
- deduplicates permitted content by SHA-256;
- records source, date, license, fact, domain, and transformation lineage;
- separates mutable research decisions from immutable fixture releases;
- can be cloned and verified without ignored local state; and
- provides the source of approved evidence for future fixture freezing.

The first project is HIST-001, the Napoleon 1812 counterfactual. Its registry
starts with nine pending candidate records derived from seven source
specifications. No candidate is approved merely because it was imported.

## 2. Non-goals

This change does not:

- fetch evidence from the network;
- call a language model;
- implement `search_evidence`, `read_evidence`, or `submit_report`;
- create or approve `fixtures/v0.2`;
- run the HIST-001 agent experiment;
- add SQLite or another database;
- store arbitrary third-party pages when their retention rights are unclear;
- treat URLs, search snippets, or model output as verified evidence; or
- delete the temporary external candidate package before a verified import.

The offline tools and the first History Fork are separate follow-up designs.

## 3. Three data layers

TraceLane has three explicit data layers:

| Root | Role | Version control |
|---|---|---|
| `evidence/` | Ongoing research registry, including pending and rejected work | Tracked |
| `fixtures/` | Approved, immutable benchmark snapshots | Tracked |
| `artifacts/` | Per-run traces, reports, checkpoints, and scores | Ignored |

The intended flow is:

```text
external source or acquisition session
              |
              v
evidence/projects/<project-id>/candidates
              |
              v
pending -> approved | rejected | superseded
              |
              v
approved closure only
              |
              v
fixtures/<release>
              |
              v
agent runs -> artifacts/<run-id>
```

`evidence/` is the research source of truth. `fixtures/` is a frozen derivative
of an explicitly approved evidence closure. `artifacts/` never becomes a fact
source merely because an agent produced it.

## 4. Repository layout

```text
evidence/
├── README.md
├── registry.json
├── blobs/
│   └── sha256/
│       └── <first-two-hex>/
│           └── <sha256>.blob
└── projects/
    └── hist-001/
        ├── README.md
        ├── project.json
        ├── index.json
        ├── candidates/
        │   └── candidate_<candidate-id>.json
        ├── reviews/
        │   └── review_<review-id>.json
        └── transformations/
            └── transformation_<transformation-id>.json
```

All paths are repository relative. Persisted records must not contain the
absolute path of the working copy or the external import directory.

### 4.1 Global registry

`evidence/registry.json` is the deterministic project catalog. Each project
entry contains:

- `project_id`;
- `title`;
- `status`;
- `project_ref`;
- `index_ref`; and
- the digest and size of both referenced files.

Project entries are sorted by `project_id`. Duplicate IDs or paths are invalid.
The initial allowed project statuses are `active`, `paused`, `completed`, and
`archived`.

### 4.2 Project definition

`project.json` defines the research question rather than individual evidence.
HIST-001 records:

- project ID and title;
- research question;
- historical cutoff `1812-06-23T23:59:59Z`;
- intervention;
- required evidence domains;
- the future-control policy;
- admitted source types;
- project status; and
- schema version.

The initial intervention is:

> Napoleon does not cross the Niemen or launch the Russian campaign.

Refinements such as maintaining eastern deterrence, renegotiating Franco-
Russian relations, and redirecting resources to Iberia belong in the later
task specification. They must not silently change the stable HIST-001
intervention during an evidence import.

### 4.3 Candidate records

Each candidate file is the current canonical revision of an evidence proposal
with these field groups:

**Identity**

- `schema_id`;
- `project_id`;
- `candidate_id`;
- canonical record digest.

**Source**

- stable source-specification ID;
- query used to locate it;
- canonical source URL;
- title;
- document date;
- date precision;
- source type;
- source locator notes.

**Content**

- content `ArtifactRef`;
- ordered transformation references;
- media type and encoding;
- curated-content SHA-256.

**Research interpretation**

- evidence domains;
- allowed fact IDs;
- role: `evidence` or `future-control`;
- repository-authored scope note.

**Rights and lineage**

- license basis;
- raw-retention decision;
- content authorship classification;
- acquisition session ID;
- imported source candidate ID and digest.

Candidate identity uses the approved stable formula based on query, title,
canonical source URL, document date, date precision, and curated-content
digest. Project interpretation, license evaluation, and review state do not
silently rename the source candidate. Any change to the serialized record does
change its record digest and invalidates reviews bound to the old digest.

Candidate files are revised only through a normal Git change. Git retains prior
record bytes, while the record digest makes every review revision-specific.
There is no in-place runtime mutation API for checked-in candidates.

### 4.4 Reviews

Review files are immutable and append-only. A review contains:

- `review_id`;
- `project_id`;
- `candidate_id`;
- exact candidate record digest;
- decision: `approved`, `rejected`, or `superseded`;
- a non-empty reason;
- reviewer identity label;
- review timestamp;
- approved fact IDs;
- approved evidence domains;
- license and raw-retention decision;
- optional `supersedes_review_id`; and
- review record digest.

`pending` is the absence of a valid current review. It is not stored as a
review decision.

Reviews form a deterministic chain:

- a first review has no predecessor;
- a later review names the review it supersedes;
- a review can supersede only the current chain head;
- forks in a review chain are invalid;
- a review bound to a stale candidate digest is historical but not current; and
- changing a candidate returns its effective status to `pending`.

The effective candidate status is derived from the valid review-chain head. It
is never an independently editable source-of-truth field.

### 4.5 Transformations

Transformations record how content changed without claiming that the derived
text is the original source. Supported initial transformation types are:

- `manual_excerpt`;
- `repository_paraphrase`;
- `translation`;
- `ocr`; and
- `normalization`.

Every transformation records:

- input content reference;
- output content reference;
- transformation type;
- tool or human actor label;
- deterministic parameters or written method;
- creation timestamp;
- input and output digests; and
- license implications.

Transformation references use the existing typed
`evidence_transformation` contract and forbid a schema ID on the generic
`ArtifactRef`.

### 4.6 Content-addressed blobs

Permitted content bytes live under:

```text
evidence/blobs/sha256/<first-two-hex>/<sha256>.blob
```

A blob path, recorded digest, recorded size, and actual bytes must agree.
Existing different bytes must never be overwritten.

The registry may store:

- repository-authored paraphrases;
- repository-authored notes;
- public-domain source text;
- source text whose license explicitly permits repository retention; and
- derived text whose transformation and license are recorded.

When full-text retention is unclear, the registry stores the URL, metadata,
hash where lawfully obtained, and a repository-authored paraphrase. It does not
commit the third-party full text.

The same content digest is stored once globally and may be referenced from
multiple projects.

### 4.7 Reference and serialization rules

Registry references use these namespaces:

```text
tracelane://evidence/registry.json
tracelane://evidence/projects/<project-id>/project.json
tracelane://evidence/projects/<project-id>/candidates/<file>
tracelane://evidence/projects/<project-id>/reviews/<file>
tracelane://evidence/projects/<project-id>/transformations/<file>
tracelane://evidence/blobs/sha256/<sha256>
```

The resolver maps these URIs only within the repository-root `evidence/`
directory. It rejects absolute paths, dot segments, encoded traversal, links,
reparse points, and a resolved path outside the evidence root.

New wire documents use versioned schema IDs under
`tracelane://schemas/evidence-*/v1`. References carry kind, URI, SHA-256, and
size. Structured records use canonical UTF-8 JSON with sorted keys, compact
separators, one trailing newline, finite numbers only, and no byte-order mark.

Imported source timestamps and review timestamps are explicit UTC values.
Generated indexes do not contain a build time or current clock value, so an
unchanged registry rebuild remains byte-identical.

## 5. Deterministic project index

`index.json` is generated, not hand-maintained. Canonical candidate and review
records remain the source of truth.

Each sorted index entry contains:

- candidate ID and record reference;
- effective status;
- current review reference, if any;
- document date and precision;
- source type;
- role;
- domains;
- fact IDs;
- content digest;
- license class; and
- transformation IDs.

The index supports deterministic filtering by:

- project;
- effective status;
- candidate ID;
- domain;
- fact ID;
- role;
- source type;
- license class;
- document date; and
- date range.

Rebuilding an unchanged project index must produce byte-identical canonical
JSON. The global registry follows the same rule.

The initial command surface is:

```text
tracelane evidence list --project hist-001 --status pending
tracelane evidence find --project hist-001 --fact logistics.prewar_supply_plan
tracelane evidence verify --project hist-001
tracelane evidence rebuild-index --project hist-001
```

No query command mutates evidence.

## 6. HIST-001 initial import

The initial import reads an operator-supplied candidate package outside the
repository. Its source path is a command input and must not be persisted in
registry records, indexes, reports, documentation, or error messages.

The importer:

1. authenticates the acquisition session manifest;
2. authenticates all nine candidate records and referenced content bytes;
3. rejects incomplete or inconsistent lineage;
4. converts acquisition records into canonical project candidate records;
5. records acquisition IDs and digests for provenance;
6. copies only permitted content into the global blob store;
7. creates no review records;
8. derives nine `pending` index entries;
9. verifies the generated registry and index; and
10. compares imported candidate IDs and content digests with the source
    package.

The nine records cover:

1. Treaty of Tilsit;
2. Berlin Decree;
3. Milan Decree;
4. Russian foreign-trade arrangements for 1811;
5. Napoleon's March 1812 supply correspondence;
6. Wellington's May 1811 Iberian correspondence;
7. the December 1810 conscription recommendation;
8. the 1811 conscription record; and
9. the December 1812 29th Bulletin.

The nine records derive from seven stable source-specification IDs. A source
specification that yields more than one dated document retains one
source-specification ID while each dated candidate keeps its own candidate ID.

The 29th Bulletin is imported with role `future-control`. It remains searchable
for audit and grader construction but must never appear in the clean agent
evidence view.

The import is transactional:

- validate the complete source before target publication;
- create new files without overwriting unrelated files;
- publish the project index last;
- publish the global registry after the project is valid;
- accept reruns only when existing bytes are identical; and
- leave no partially valid project after a rejected import.

After import, an explicit comparison report proves that the source package and
registry contain the same nine candidate identities and content digests. Only
then may the external package be removed, and removal requires a separate
explicit user instruction.

## 7. Review workflow

Every HIST-001 candidate is reviewed against four independent questions:

1. **Locator and authenticity:** Is the source location real, stable enough,
   and correctly described?
2. **Date:** Is the document date and precision supported?
3. **Rights:** May TraceLane retain the proposed bytes, or only its own
   paraphrase?
4. **Claim scope:** Do the proposed fact IDs and domains stay within what the
   source actually supports?

Approval requires a reason and the exact allowed fact IDs. A reviewer may
approve fewer facts than the candidate proposes.

Rejected and superseded records remain in the registry and indexes. They are
excluded by status, not deleted.

## 8. Fixture-freeze boundary

Evidence Registry completion does not approve a fixture.

The later freeze operation may consume only a complete approved closure:

- approved candidate record;
- current review bound to its exact digest;
- permitted content blobs;
- required transformations;
- license decision;
- project definition; and
- complete required-domain coverage.

HIST-001 freeze additionally requires:

- diplomacy, economy, logistics, Iberia, military, and imperial-governance
  coverage;
- all admitted evidence dated at or before the historical cutoff;
- the 29th Bulletin present as a future control;
- the future control explicitly excluded from the agent-visible set;
- deterministic development and held-out splits; and
- byte-identical regeneration.

Freeze failure must not leave a partial `fixtures/v0.2`.

## 9. Security and failure behavior

All registry operations fail closed.

They reject:

- path traversal, symlinks, junctions, and reparse points;
- references outside the evidence root;
- missing or altered blobs;
- size or digest mismatches;
- duplicate IDs with different bytes;
- stale reviews;
- review-chain forks;
- hand-edited indexes that disagree with source records;
- future evidence mislabeled as clean evidence;
- full third-party text without a positive retention decision;
- configured secrets, generic credentials, personal contact data, and local
  absolute paths in persisted free text;
- source mutation during import; and
- partial or conflicting target state.

Error messages use stable public categories and do not expose local absolute
paths or sensitive values.

The integrity model remains internal consistency rather than adversarial
authenticity. Git commits and release digests provide the external publication
anchor. This design does not add PKI or signatures.

## 10. Test strategy

### 10.1 Contract and schema parity

Positive and negative tables exercise Python and JSON Schema for:

- registry;
- project definition;
- candidate;
- review;
- transformation; and
- project index.

### 10.2 Index determinism

Tests delete and rebuild project and global indexes, then require byte-identical
output. Ordering is deterministic across operating systems.

### 10.3 Review lifecycle

Tests cover:

- pending candidates;
- first approval and rejection;
- superseding a review;
- stale candidate digest;
- review-chain forks;
- reduced approved fact scope; and
- retention of rejected history.

### 10.4 Blob integrity and rights

Tests cover:

- deduplication;
- missing, truncated, replaced, and conflicting blobs;
- raw text allowed by license;
- raw text rejected when license is unclear; and
- repository-authored paraphrases.

### 10.5 Cutoff and future control

Tests prove that:

- admitted evidence is at or before cutoff;
- the 29th Bulletin is indexed as `future-control`;
- clean evidence queries exclude it;
- audit queries can find it; and
- a mislabeled post-cutoff candidate is rejected.

### 10.6 Import

The checked-in HIST-001 registry must contain:

- nine candidates;
- nine pending statuses;
- seven source specifications;
- no formal reviews;
- expected source candidate IDs and content digests;
- one future-control record; and
- no persisted local backup path.

The import is tested for interruption, rerun, conflict, source mutation, and
target mutation.

### 10.7 Freeze gate

Before explicit human approval:

- registry validation passes;
- evidence search and index tests pass;
- fixture freeze is rejected because no candidates are approved; and
- `fixtures/v0.2` remains absent.

## 11. Documentation

`evidence/README.md` explains:

- the difference between evidence, fixtures, and artifacts;
- how project records are organized;
- how to list, find, verify, import, and review evidence;
- why rejected candidates are retained;
- the license and raw-retention policy; and
- the human approval gate before fixture freezing.

`evidence/projects/hist-001/README.md` explains:

- the research question and cutoff;
- the nine initial candidates;
- the future-control role;
- current review progress; and
- the next step required before freezing.

## 12. Definition of done

This subproject is complete when:

- the repository-root `evidence/` registry exists and is tracked;
- the global registry and HIST-001 project index rebuild deterministically;
- all nine HIST-001 candidates are imported with verified lineage;
- every initial candidate is effectively `pending`;
- the future control is correctly indexed and excluded from clean views;
- raw content retention follows explicit license decisions;
- no external absolute path is persisted;
- validation, search, corruption, lifecycle, and import tests pass;
- a fresh clone can verify the registry without ignored state;
- the external package comparison succeeds;
- `fixtures/v0.2` remains absent; and
- no LLM, Fake Tool, or History Fork execution is claimed as part of this
  subproject.

After this definition is met, the next design covers the three offline evidence
tools. The first History Fork follows only after tool and fixture readiness.
