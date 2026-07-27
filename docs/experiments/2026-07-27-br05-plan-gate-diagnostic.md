# BR-05 plan artifact gate diagnostic

Date: 2026-07-27

Model: `opencode-go/glm-5.2`

Baseline: `b45f16b0864aff9a557ae05524071dae0e8b03a1`

## Question

The BR-05 complexity-stress run produced no completed plan in three attempts.
This diagnostic asks whether BR-05 is intrinsically unable to produce a plan
under bounded execution, or whether the earlier plan protocol failed to force
convergence.

## Diagnostic protocol

One plan-only attempt ran with no concurrent OpenCode sessions:

- hard wall limit: 1,200 seconds;
- total provider-token limit: 1,000,000;
- tool-call limit: 25;
- no delegation requested;
- only the three editable files and direct interfaces were in scope;
- the prompt required a final build-ready artifact after at most 12
  exploratory tool calls.

The independent plan gate awarded points for:

1. all three editable paths;
2. previous-signal T+1 execution and no first-day trade;
3. both ranking and OHLC T+1→T+2 labels;
4. FiLM per-timestep modulation and Bottleneck sequence-mean context;
5. concrete validation steps.

Gate SHA-256:
`a45c40b25c0b62b0d1a6677bc0a5aaef2f3469e323d486152d41f22b36902d7c`.

## Result

| Outcome | Wall | Tool calls | Total provider tokens | Plan score |
|---|---:|---:|---:|---:|
| completed | 263.0s | 7 | 155,772 | 100/100 |

The worktree remained clean. The plan was frozen with:

- session: `ses_05cb75362ffew9q1Nq97fS04ur`;
- content SHA-256:
  `c2329981f031405a3aa3e0d43c7696cfd92e93ba2eae513e05e8b207d4e506c1`;
- content length: 18,276 characters.

## Diagnosis

BR-05 does not make plan production intrinsically impossible, and larger token
limits were not required. The successful diagnostic used fewer tokens, fewer
tools, and less wall time than every failed pressure-run plan.

The evidence is consistent with the earlier failures being caused by a missing
convergence contract plus concurrent provider pressure:

- the old prompt allowed open-ended pipeline and documentation exploration;
- one attempt delegated further investigations;
- none had a required artifact structure or exploration cutoff;
- all three ran concurrently with direct-build sessions.

Because serial execution and the constrained prompt changed together, this
single diagnostic does not estimate the individual causal effect of either
change. It is sufficient to reject the claim that plan mode is necessarily
incapable of handling BR-05.

## Decision

Keep bounded plan execution. Do not remove token limits. For the next paired
experiment, use this plan gate as a precondition: a plan must finish, leave the
workspace unchanged, and score 100/100 before build starts. A failed gate is a
`plan_generation_failed` workflow outcome rather than an implicit fallback to
direct-build.
