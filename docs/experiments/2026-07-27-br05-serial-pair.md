# BR-05 strict serial pair 1

Date: 2026-07-27

Model: `opencode-go/glm-5.2`

Task: `BR-05-t1-causality-alignment` v2

Baseline: `b45f16b0864aff9a557ae05524071dae0e8b03a1`

## Scope

This is the first valid BR-05 direct-build versus plan→build comparison run
strictly serially from separate clean worktrees at the same frozen baseline.
Both workflows received the same total task budget: 1,800 wall seconds, 220 tool
calls, and 2,000,000 provider model tokens.

This is one matched pair. It is descriptive evidence about these two attempts,
not a statistically significant workflow result.

## Result

| Workflow | End | Independent function score | Acceptance | Diff | Wall time | Model tokens | Tool calls | Cost | Raw trace |
| --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| plan→build | completed | 100/100 | pass | pass | 642.5 s | 810,558 | 32 | $0.4054 | 5,910,611 B |
| direct-build | wall budget exhausted | 40/100 | fail | pass | 1,800.5 s | 1,361,682 | 23 | $1.4065 | 11,924,937 B |

The plan→build budget is shared across both phases:

- plan gate: 263.0 seconds, 155,772 model tokens, 7 tool calls;
- build: 379.5 seconds, 654,786 model tokens, 25 tool calls;
- combined: 642.5 seconds, 810,558 model tokens, 32 tool calls.

The frozen plan passed its independent plan gate at 100/100 before the build
phase started. The build session is manually linked as a child of the plan root
with `phase_link: manual-cli-split`, because the OpenCode CLI cannot reliably
switch an existing session from plan to build.

## Functional diagnosis

The plan→build attempt passed all five hidden dimensions:

- backtest T+1 execution with no first-day trade: 30/30;
- ranking T+1→T+2 label: 20/20;
- OHLC T+1→T+2 label: 20/20;
- FiLM stepwise static context: 20/20;
- bottleneck sequence-mean context: 10/10.

The direct-build attempt passed both target-label dimensions, for 40/100. It
did not satisfy the backtest execution dimension and did not modify the model
file, so both Transformer context dimensions failed. Its partial patch still
passed byte compilation and `git diff --check`.

## What this pair establishes

- The BR-05 gate is not all-or-nothing: the budget-exhausted direct attempt
  received credit for two independently working functional slices.
- A plan workflow can complete BR-05 under the same finite total budget; the
  earlier 0% plan completion rate was not proof that the task was impossible
  under the gate.
- For this pair, plan→build completed more functionality while using 551,124
  fewer model tokens, 1,158.0 fewer wall seconds, and about $1.00 less provider
  cost. It used nine more tool calls.
- The direct attempt spent most of its wall budget reading and generating a
  long response before beginning edits near the end. Its failure is recorded as
  budget exhaustion plus partial functional completion, not as a crash or an
  invalid trace.

## Frozen TraceLane runs

- plan→build:
  `533601362ad61a7d8f6856d5632b9c3a8b00a8b12f93ece05715cde164f5f2b0`
- direct-build:
  `bc3ba669dae6978581ff8c22221ce381da5284813810fe1d9eae8279c47d95f6`

The imported artifacts include the frozen CodingTask, attempt metadata,
workspace snapshots and patches, sanitized OpenCode session traces, provider
cost and budget records, independent functional dimensions, and TraceLane
acceptance/diff grades.

## Next experiment

Keep execution serial. Run additional independently clean matched pairs with the
same prompt, model, baseline, task budget, plan gate, and grader. Pre-register
the repeat count before interpreting aggregate pass rates or efficiency
distributions.
