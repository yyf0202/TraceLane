# BR-05 strict serial pairs 1–5

Date: 2026-07-27

Task: `BR-05-t1-causality-alignment` v2

Baseline: `b45f16b0864aff9a557ae05524071dae0e8b03a1`

## Scope

These are five BR-05 direct-build versus plan→build comparisons run strictly
serially from separate clean worktrees at the same frozen baseline. Both
workflows received the same total task budget per repeat: 1,800 wall seconds,
220 tool calls, and 2,000,000 provider model tokens.

There is a provider boundary after repeat 2:

- R1–R2 used `opencode-go/glm-5.2`.
- R3–R5 used `ark/glm-5.2` through the OpenAI-compatible Ark Coding endpoint.

These are five matched pairs under two provider conditions. They are
descriptive evidence about ten attempts, not a statistically significant
workflow result. The two provider groups must not be pooled as if their
latency, token accounting, caching, and failure behavior were identical.

## Results

| Repeat | Provider | Workflow | End | Plan gate | Function | Acceptance | Wall | Model tokens | Tools | Cost |
| ---: | --- | --- | --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| 1 | OpenCode Go | plan→build | completed | 100/100 | 100/100 | pass | 642.5 s | 810,558 | 32 | $0.4054 |
| 1 | OpenCode Go | direct-build | wall budget | — | 40/100 | fail | 1,800.5 s | 1,361,682 | 23 | $1.4065 |
| 2 | OpenCode Go | plan→build | token budget | 100/100 | 90/100 | fail | 1,090.9 s | 2,032,532 | 57 | $0.8341 |
| 2 | OpenCode Go | direct-build | wall budget | — | 0/100 | fail | 1,800.3 s | 1,137,141 | 22 | $0.5454 |
| 3 | Ark | plan→build | plan gate failed; no build | 75/100 | 0/100 | fail | 285.3 s | 227,260 | 11 | $0.0000 |
| 3 | Ark | direct-build | token budget | — | 40/100 | fail | 1,328.4 s | 2,011,626 | 29 | $0.0000 |
| 4 | Ark | plan→build | plan response wall budget; no build | — | 0/100 | fail | 1,201.0 s | 10,551 | 3 | $0.0000 |
| 4 | Ark | direct-build | token budget | — | 40/100 | fail | 473.5 s | 2,080,292 | 37 | $0.0000 |
| 5 | Ark | plan→build | build token budget | 100/100 | 80/100 | fail | 583.4 s | 2,085,380 | 50 | $0.0000 |
| 5 | Ark | direct-build | token budget | — | 40/100 | fail | 510.0 s | 2,116,463 | 30 | $0.0000 |

Ark reports zero provider cost in the OpenCode event stream. The importer
records that value faithfully; it does not imply that the Ark service has no
commercial cost.

Every plan→build budget is shared across its plan and build phases. Runtime
enforcement observes usage after a provider step completes, so a final step can
cross the nominal token limit. These attempts remain recorded as
`token_budget_exhausted`.

Build sessions are manually linked as children of their plan roots with
`phase_link: manual-cli-split`, because the OpenCode CLI cannot reliably switch
an existing session from plan to build. A build starts only after the frozen
plan passes the independent plan gate at 100/100.

## Functional diagnosis

The hidden grader awards partial credit across five independent slices:

- backtest T+1 execution with no first-day trade: 30 points;
- ranking T+1→T+2 labels: 20 points;
- OHLC T+1→T+2 labels: 20 points;
- FiLM stepwise static context: 20 points;
- bottleneck sequence context: 10 points.

R1 plan→build passed all five slices. R2 plan→build passed the first four and
missed bottleneck context, scoring 90/100.

All three Ark direct attempts converged to the same 40/100 outcome: both target
label slices passed, while engine execution and both model slices failed.

The Ark plan attempts varied:

- R3 produced a frozen plan that scored 75/100. It did not specify the required
  engine execution timing precisely enough, so the registered protocol stopped
  before build. The untouched baseline scored 0/100.
- R4 stopped during plan exploration after an Ark request produced no response
  event. No final plan existed and build did not start.
- R5 produced a 100/100 frozen plan and proceeded to build. Its implementation
  passed engine execution, both target slices, and bottleneck context, but
  failed FiLM stepwise static modulation, scoring 80/100.

## Ark response incident

The R4 plan trace contains a prepared second model request followed by no text
delta, provider error, or completion event. The OpenCode process remained alive
until the 20-minute plan-phase wall gate terminated it.

A separate, non-experimental diagnostic against the same endpoint forced a
tool call and a second model turn. It completed normally in 8.0 seconds with
one tool call and 19,830 model tokens. R5 plan also completed normally in
159.6 seconds with nine tool calls and 189,381 model tokens.

The evidence therefore does not support an endpoint configuration error or a
systematic OpenCode/OpenAI-compatible protocol incompatibility. The most
plausible classification is an intermittent Ark gateway or upstream
`glm-5.2` request hang. A single trace cannot distinguish the gateway from the
model backend. R4 is retained as a real provider-response failure and was not
automatically retried, avoiding retry-selection bias.

The configured endpoint matches the Volcengine Ark Coding documentation:
`https://ark.cn-beijing.volces.com/api/coding/v3`.

## What these pairs establish

- The complex task is not all-or-nothing: non-passing implementations received
  40, 80, and 90 according to independently working functional slices.
- A finite-budget plan workflow can pass BR-05 completely under the original
  provider (R1), and can produce a materially more complete Ark implementation
  than the repeated Ark direct outcome (R5: 80 versus 40).
- Ark plan execution was less stable across these three attempts: one plan gate
  failure, one provider-response hang, and one successful gate/build.
- The three Ark direct attempts were more consistent but plateaued at 40/100
  and exhausted their token budgets.
- These observations motivate more serial repeats and provider reliability
  instrumentation. They do not establish that planning is generally superior
  or inferior.

## Frozen TraceLane runs

- R1 plan→build:
  `e99cb9a552044be9d5e8597933d31b46191dd508b7c843caf1c8b245ca373c0c`
- R1 direct-build:
  `8ff2988ca4fbb41da55460ab2a4a6520e922607017cfe2ea95aa19e2efd8c340`
- R2 plan→build:
  `e914c98a38bfd96b18704863a03f023d10d4cccfabd0e950b7e21c03b16b5714`
- R2 direct-build:
  `8eb079c0feef36832382e84177ef13d45f116951d7f4de6906864694d0352e40`
- R3 plan→build:
  `985c134f738eb7c7c1f97bd1902f9d5eae0eacea5654bad7e7ec6011796bf566`
- R3 direct-build:
  `4cd6df1bd277ffd31f1297bda238ceb379d42450f778d0da24d1663da0940208`
- R4 plan→build:
  `c2cd3d6f350d032f832a699a17bc5072cdf912b96c394fe7f3d616ca4de33ec0`
- R4 direct-build:
  `625aeff02e39cea966537fea172a9072eb0793a8dcafbf065874900d178220a2`
- R5 plan→build:
  `d0c1b3c853bb3a382ecaf2644327890543fd174ec5121bdb6e36f810a02e0155`
- R5 direct-build:
  `e158bfe2557a0ec04f5cefbb31d435efd1d2bfe6bdb266e8d790e3375ea3c82c`

The imported artifacts include the frozen CodingTask, attempt metadata,
workspace snapshots and patches, sanitized OpenCode session traces, provider
and budget records, independent functional dimensions, plan gate outcomes,
workflow termination reasons, and TraceLane acceptance/diff grades.

## Excluded infrastructure attempt

The first R2 plan invocation returned exit code 0 after 228.9 seconds, six tool
calls, 39,198 model tokens, and $0.0626, but emitted no assistant final text.
Because no plan artifact could be frozen, it remains classified as
`missing_assistant_final_text` and excluded as an infrastructure retry. Its
trace is preserved under
`artifacts/raw-opencode/br05-serial-r2-plan-build`.
