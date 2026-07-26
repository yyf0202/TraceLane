# Integrity, threat model, and migration boundaries

This document collects the operational and trust details that the README keeps
out of the way. Read it if you operate TraceLane on real artifacts or migrate
between versions.

## Artifact integrity

TraceLane verifies internal consistency: hashes, references, trace order,
approval bindings, and complete run contents. It detects corruption and
partial or stale substitutions. It does **not** claim authenticity against an
attacker who can rewrite every artifact and the Git history; a published Git
commit or release digest is the external anchor.

Manual acquisition uses a per-session lock and an authenticated operation
journal for both ingest and promotion. Recovery validates the complete base
inventory before materializing pending documents, validates the merged
inventory, publishes the session manifest last, and removes the journal only
after the published manifest is reread successfully.

Promoted evidence can be archived into another artifact root without rewriting
its references. The archive authenticates the evidence record, candidate,
approval review, curated content, and ordered transformations before copying,
then recreates each original `tracelane://artifacts/...` path with the same
digest, size, and bytes. An identical archive is idempotent; conflicting target
bytes are never overwritten. This archive protocol does not itself create or
approve a public historical fixture.

Trace payloads and free-text mapping keys retain redaction, while structural
trace identities fail closed when their final serialized values collide with a
configured secret. Identity fields are not redacted because changing them would
invalidate the trace hash chain.

## Reproducibility

- Fixtures are synthetic and generated without network access or current time.
- The suite manifest stores hashes for every task and the generator.
- Schemas reject unknown fields and malformed structured outputs.
- Canonical serialization rejects non-finite numbers.
- Fixed-clock golden tests lock normalized output.
- Core artifacts are byte-stable across different output directories.

## Migration trust boundary

The v1-to-v2 importer is a local, operator-controlled migration boundary. It
copies a selected v1 run without executing its contents or using the network,
rejects linked trees and a target/import tree placed inside the selected
source, and binds the source and copied payload to explicit file inventories
and root digests. A target outside the source may contain the source; this is
not rejected as an overlap. The importer also verifies that the source remains
unchanged throughout the copy. Each migrated file is published atomically, and
the migration is considered complete only after an authenticated completion
marker covers the published inventory.

Acquisition import is supported on Windows only. On other platforms it fails
before creating the target or sibling staging namespace with `evidence import
is unavailable on this platform`. The dependency-free importer requires an
authenticated open source-directory handle and a handle-relative destination
move; the current design has no reviewed POSIX or macOS primitive providing the
same ownership guarantee without resolving the source pathname again. Registry
read, verification, query, and rebuild operations remain portable.

The v1 consistency guarantee assumes TraceLane readers and writers cooperate
through the shared physical-root lock. A same-account process that ignores the
lock and deliberately relocates or replaces TraceLane-owned namespace objects
during an operation is outside the v1 threat model. Existing defensive checks
remain fail-closed where they detect interference, but v1 does not claim
complete namespace custody against that actor. Retained quarantine requires
explicit, ownership-confirmed offline maintenance.

Those hashes prove internal snapshot consistency; they do not establish who
created the v1 source or whether its claims are true. Operators must select a
trusted local source. For published experiments, the repository commit and
release digest remain the external publication anchor.

## HIST-001 registry state

The tracked HIST-001 registry contains nine pending candidates, no approvals
or reviews, and one post-cutoff future-information control. Verification
derives project and global indexes from authenticated source inventory,
candidate, review, transformation, and blob records before accepting persisted
indexes.

## The v0.2 release gate

The public `fixtures/v0.2` package remains intentionally absent and
unapproved. Its tracked-package test is a release gate: today it proves only
that the package is absent. A separate passing test exercises the same schema,
loader, and provenance closure against a generated temporary v0.2-shaped
package. Publication still requires a separate review and approval.

Until `fixtures/v0.2` is separately approved and published, the focused run

```bash
python -m pytest tests/v2/test_hist001_fixture.py -q
```

has one expected failure for the absent tracked package; its generated-package
schema, loader, and provenance test passes.
