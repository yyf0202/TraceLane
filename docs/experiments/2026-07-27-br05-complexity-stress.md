# TraceLane × OpenCode BR-05 complexity stress

Date: 2026-07-27

Model: `opencode-go/glm-5.2`

Task: `BR-05-t1-causality-alignment` v2

## Scope

This is a six-attempt budget-enforcement and complexity stress run, not a
direct-build versus plan→build result. Three direct phases and three plan
phases ran concurrently. Provider contention can affect wall time, and no
attempt completed the full requested workflow, so workflow-quality comparisons
would be invalid.

BR-05 spans backtest execution timing, two label generators, and two
Transformer static-feature fusion paths. `data/**`, model artifacts, output
directories, and paper-trading state remained protected.

## Results

| Workflow | Repeat | End | Functional score | Build reached | Changed paths |
|---|---:|---|---:|---:|---|
| direct-build | 1 | token budget exhausted | 40/100 | yes | engine, models, target generator |
| direct-build | 2 | token budget exhausted | 40/100 | yes | engine, target generator |
| direct-build | 3 | wall budget exhausted | 0/100 | yes | none |
| plan→build | 1 | plan wall budget exhausted | 0/100 | no | none |
| plan→build | 2 | plan wall budget exhausted | 0/100 | no | none |
| plan→build | 3 | plan wall budget exhausted | 0/100 | no | none |

The two partial direct solutions correctly moved both label families to the
T+1→T+2 interval. Neither produced passing model behavior, even though repeat
1 had started editing the model file. Their engine changes incorrectly invoked
the strategy on the first day with an empty prior signal. The third direct
attempt made no change.

The plan attempts did not produce a completed frozen plan before their phase
wall budget, so no build phase was started. This is a workflow failure for this
run, but not evidence that planning generally harms performance.

## Harness finding

The first calibration exposed that CodingTask budgets were recorded but not
enforced during OpenCode execution. The new runner now terminates process
groups and writes a machine-readable outcome when total provider tokens, tool
calls, or wall time exceed the frozen limits. Total provider tokens include
uncached input, cached input, output, and reasoning tokens.

BR-05 v1's 220,000-token limit was too small even for initial repository
analysis. BR-05 v2 raises that limit to 2,000,000 and records the calibration
in the frozen `bericher-v0.3` suite. The stress run still exhausted its limits,
which is a valid negative result.

## Next valid comparison

The next comparison must run one attempt at a time, use the same provider
concurrency slot, share the full task budget across plan and build, and freeze
the exact completed plan before starting build. It should be scheduled as a
separate experiment rather than reclassifying these pressure-run attempts.
