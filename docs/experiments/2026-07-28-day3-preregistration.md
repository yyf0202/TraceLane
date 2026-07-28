# Day 3 preregistration

Day 3 adds BR-10, BR-11 and BR-12 without changing the frozen Day 1 or Day 2
results. The matrix contains 36 attempt slots:

- three BeRicher tasks;
- `glm-5.2`, `deepseek-v4-pro` and `kimi-k2.7-code`;
- direct-build and plan→build;
- two repeats per task, model and workflow.

Attempts run strictly serially. Within each matched pair the first workflow alternates,
so direct or plan is not systematically first. There are no automatic attempt retries.
Provider failures remain in a reliability layer and are not converted to functional zeroes.

## Frozen harness

Every attempt uses `opencode-h0-06d9803be9`. H0 binds the complete buildable OpenCode
fork revision, repository tree, full git archive and Darwin ARM64 binary. Runs isolate
OpenCode's configuration home and disable project configuration, external plugins, external
skills and provider-turn retries.

The H0 candidate boundary includes agent, session, tool, permission and CLI-run code.
Observation, provider, authentication, configuration, task, grader and budget enforcement
remain protected. Future Meta-Harness candidates will be complete detached OpenCode
worktrees derived from H0 rather than prompt fragments.

## Gate and budget

BR-10–12 use a semantic Day 3 plan gate. It accepts equivalent implementation structure
but rejects contradictions such as:

- saying ahead commits will be pushed while skipping push when no new change exists;
- saying orders follow a successful daily run while placing generation before it;
- mentioning per-epoch state while calling `set_epoch` only outside the epoch loop.

Each direct or combined plan→build attempt has 1,800 seconds, 220 tool calls and 2,000,000
model tokens. Plan may consume at most 900 seconds, 80 tool calls and 1,000,000 tokens;
build inherits only the remainder.

The machine-readable preregistration freezes the exact 36-slot order and hashes the
harness, gate, task manifests, hidden graders, attempt runner, shared execution engine and
plan handoff.

## V2 amendment before restart

The original matrix halted after three pilot slots. The first direct attempt completed,
but the matched plan's build process never dispatched: plan and build shared a raw directory,
and the runner treated the already-created isolated configuration directory as an error.
The next plan was operator-interrupted immediately after diagnosis.

Those three raw attempts are preserved as an infrastructure-pilot layer and excluded from
the 36-slot analysis. V2 makes configuration-directory creation phase-safe, records operator
interruptions without orphaning OpenCode, changes every formal attempt ID to `day3v2-*`, and
freezes new runner hashes before restart. No model attempt is silently retried.

## Analysis boundary

The primary metric is the independent weighted functional score. Model tokens and wall
time are reported as separate costs. Gate-blocked plans calibrate the gate but have no build
completion outcome. Results are descriptive and BeRicher-specific; they are not a
statistical claim or a universal model ranking.
