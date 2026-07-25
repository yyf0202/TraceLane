# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Persisted execution fingerprints bind every v0.2 run to its exact case, evidence, harness, runtime, grader set, code revision, and repeat.
- Exact checksum closure detects missing, extra, duplicated, or substituted run files.
- Bound acquisition reviews tie approval to the exact candidate record, curated bytes, source locator, and review metadata.
- Manifest-last acquisition transactions journal both ingest and promotion, preflight the complete base inventory, and recover interrupted publication without losing inventory.
- Byte-preserving evidence archives retain promoted record, candidate, review, content, transformation references, URIs, digests, sizes, and exact bytes.
- Verified historical provenance enforces fixture references, evidence manifests, source licenses, transformations, and decision cutoffs.
- Trace hash chaining detects event edits, deletion, insertion, reordering, stale writers, and broken causal or parent links.
- Structural trace identities fail closed when their final serialized values collide with configured secrets.
- Generic trace metadata and decoded acquisition URLs apply the same configured-secret, credential, personal-data, and absolute-path persistence policy while preserving valid typed SHA-256 identities.
- Completed run manifests require at least one authenticated output, and frozen history cases are reauthenticated at their public consumption boundary.
- Candidate identifiers bind query, title, canonical source URL, document date and precision, and curated-content digest; curator and transformation edits remain protected by the candidate-record digest and stale-review checks.
- Local v1 migration binds the normalized absolute source root, serializes concurrent publication, publishes each file through a create-new boundary, and authenticates completion with a persisted create-new marker over the copied inventory.
- Lock files are reauthenticated after OS acquisition and before unlock, and acquisition journals use identity-bound tombstone retirement instead of raw pathname deletion.
- Initial HIST-001 candidate evidence records provide a project-scoped registry with pending primary-source paraphrases and a future-information control.

## [0.1.0] - 2026-07-24

### Added

- Deterministic evidence-grounded Agent Loop with conditional debate.
- Point-in-time evidence freezing and budgeted context selection.
- Content-addressed run store, append-only trace, and hash-chained checkpoints.
- Trusted checkpoint resume with input, config, model, and state verification.
- Completion, grounding, PIT, recovery, and operational graders.
- Deterministic twelve-task synthetic suite with a hashed manifest.
- `demo`, `eval`, `ablate`, and `inspect` CLI workflows.
- Context-policy ablation with isolated control and treatment artifacts.
- Secret redaction, path containment, mutation, and fail-open observability tests.
- Fixed-clock golden and cross-directory byte-reproducibility tests.

[Unreleased]: https://github.com/yyf0202/TraceLane/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yyf0202/TraceLane/releases/tag/v0.1.0
