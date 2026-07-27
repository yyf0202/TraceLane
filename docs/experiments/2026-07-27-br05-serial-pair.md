# BR-05 strict serial pairs 1–2

Date: 2026-07-27

Model: `opencode-go/glm-5.2`

Task: `BR-05-t1-causality-alignment` v2

Baseline: `b45f16b0864aff9a557ae05524071dae0e8b03a1`

## Scope

These are two BR-05 direct-build versus plan→build comparisons run strictly
serially from separate clean worktrees at the same frozen baseline. Both
workflows received the same total task budget per repeat: 1,800 wall seconds,
220 tool calls, and 2,000,000 provider model tokens.

These are two matched pairs. They are descriptive evidence about four attempts,
not a statistically significant workflow result.

## Results

| Repeat | Workflow | End | Function score | Acceptance | Diff | Wall time | Model tokens | Tools | Cost | Raw trace |
| ---: | --- | --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | plan→build | completed | 100/100 | pass | pass | 642.5 s | 810,558 | 32 | $0.4054 | 5,910,611 B |
| 1 | direct-build | wall budget | 40/100 | fail | pass | 1,800.5 s | 1,361,682 | 23 | $1.4065 | 11,924,937 B |
| 2 | plan→build | token budget | 90/100 | fail | pass | 1,090.9 s | 2,032,532 | 57 | $0.8341 | 13,619,552 B |
| 2 | direct-build | wall budget | 0/100 | fail | pass | 1,800.3 s | 1,137,141 | 22 | $0.5454 | 9,422,587 B |

Every plan→build budget is shared across its plan and build phases. Repeat 1
used 155,772 plan tokens and 654,786 build tokens. Repeat 2 used 130,383 plan
tokens and 1,902,149 build tokens.

The R2 build crossed its remaining token limit by 32,532 tokens because runtime
enforcement observes usage only after a provider step completes. It is
therefore recorded as `token_budget_exhausted`, even though its wall and tool
budgets were not exhausted.

Both frozen plans passed the independent plan gate at 100/100 before their
build phases started. Each build session is manually linked as a child of its
plan root with `phase_link: manual-cli-split`, because the OpenCode CLI cannot
reliably switch an existing session from plan to build.

## Functional diagnosis

R1 plan→build passed all five hidden dimensions. R1 direct-build passed the two
target-label dimensions and failed the other three, for 40/100.

R2 plan→build passed four dimensions:

- backtest T+1 execution with no first-day trade: 30/30;
- ranking T+1→T+2 label: 20/20;
- OHLC T+1→T+2 label: 20/20;
- FiLM stepwise static context: 20/20;
- bottleneck sequence-mean context: 0/10.

R2 direct-build spent its complete wall budget reading and reasoning without
changing the workspace. It therefore scored 0/100. The unmodified baseline
still passed byte compilation and `git diff --check`; those checks do not imply
functional acceptance.

## Excluded infrastructure attempt

The first R2 plan invocation returned exit code 0 after 228.9 seconds, six tool
calls, 39,198 model tokens, and $0.0626, but emitted no assistant final text.
The observation trace ended with `finish=unknown`; its only text part was the
user prompt. Because no plan artifact could be frozen, this invocation is
classified as `missing_assistant_final_text` and excluded as an infrastructure
retry. Its trace remains preserved under
`artifacts/raw-opencode/br05-serial-r2-plan-build`.

## What these pairs establish

- The BR-05 gate is graded by functional slices, not all-or-nothing. Three
  non-passing attempts received 40, 90, and 0 according to their actual
  behavior.
- A plan workflow can complete BR-05 under the finite shared budget: R1 passed
  100/100. R2 nearly completed it at 90/100 but exhausted tokens during extended
  validation.
- In both matched pairs, plan→build achieved a higher functional score than
  direct-build. The resource relationship was not consistent: plan used fewer
  tokens in R1 but more in R2.
- Both direct attempts exhausted the 30-minute wall budget. R1 began editing
  late and completed two functional slices; R2 never edited.
- Two matched pairs are still too few for a workflow-effect claim. They justify
  further serial repeats and investigation of convergence behavior, not a
  statistical conclusion.

## Frozen TraceLane runs

- R1 plan→build:
  `533601362ad61a7d8f6856d5632b9c3a8b00a8b12f93ece05715cde164f5f2b0`
- R1 direct-build:
  `bc3ba669dae6978581ff8c22221ce381da5284813810fe1d9eae8279c47d95f6`
- R2 plan→build:
  `63dbaa7c33652c6075fcdb1606d2c2e9b59458f6d4bef6c1de9ca1d9cdb87a59`
- R2 direct-build:
  `f26fb6129f37c943b7d516386bd9cb6da7101b6eb80d2d8e2d56effe2d53fb0b`

The imported artifacts include the frozen CodingTask, attempt metadata,
workspace snapshots and patches, sanitized OpenCode session traces, provider
cost and budget records, independent functional dimensions, and TraceLane
acceptance/diff grades.

## Next experiment

Keep execution serial. Run the pre-registered third independently clean matched
pair with the same prompt, model, baseline, task budget, plan gate, and grader.
Do not interpret aggregate pass rates or efficiency distributions as
statistically stable after only three repeats.
