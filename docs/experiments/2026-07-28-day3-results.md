# Day 3 coding-eval matrix

The 36 frozen slots use OpenCode H0 and run strictly serially through Ark.
Provider failures remain reliability evidence rather than functional zeroes.
All comparisons are paired and descriptive; this is one repository and not a
statistical-significance or general model-ranking claim.

## Attempt summary

| Task | Model | Workflow | Scores | Full | Mean tokens | Mean seconds |
|---|---|---|---:|---:|---:|---:|
| BR-10 | deepseek-v4-pro | direct-build | 100/100 | 2/2 | 376774 | 306.7 |
| BR-10 | deepseek-v4-pro | plan-build | 0/100 | 1/2 | 268140 | 230.0 |
| BR-10 | glm-5.2 | direct-build | 100/0 | 1/2 | 974322 | 967.1 |
| BR-10 | glm-5.2 | plan-build | 75/0 | 0/2 | 691422 | 1175.9 |
| BR-10 | kimi-k2.7-code | direct-build | 100/100 | 2/2 | 759260 | 918.1 |
| BR-10 | kimi-k2.7-code | plan-build | 100/100 | 2/2 | 506398 | 956.6 |
| BR-11 | deepseek-v4-pro | direct-build | 30/70 | 0/2 | 1303561 | 730.5 |
| BR-11 | deepseek-v4-pro | plan-build | 90/0 | 0/2 | 1456707 | 691.7 |
| BR-11 | glm-5.2 | direct-build | 0/40 | 0/2 | 2166612 | 804.6 |
| BR-11 | glm-5.2 | plan-build | 0/20 | 0/2 | 1250082 | 1327.4 |
| BR-11 | kimi-k2.7-code | direct-build | 20/10 | 0/2 | 2616766 | 725.1 |
| BR-11 | kimi-k2.7-code | plan-build | 0/20 | 0/2 | 1547286 | 478.0 |
| BR-12 | deepseek-v4-pro | direct-build | 50/0 | 0/2 | 474560 | 193.3 |
| BR-12 | deepseek-v4-pro | plan-build | 0/0 | 0/2 | 35884 | 87.3 |
| BR-12 | glm-5.2 | direct-build | 50/60 | 0/2 | 2026899 | 552.0 |
| BR-12 | glm-5.2 | plan-build | 0/0 | 0/2 | 723554 | 342.1 |
| BR-12 | kimi-k2.7-code | direct-build | 60/60 | 0/2 | 1492688 | 446.6 |
| BR-12 | kimi-k2.7-code | plan-build | 0/0 | 0/2 | 899870 | 475.3 |

## Matched pairs

| Task | Model | Repeat | Direct | Plan | Score Δ | Token Δ | Seconds Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| BR-10 | deepseek-v4-pro | 1 | 100 | 0 | excluded | -285574 | -125.3 |
| BR-10 | deepseek-v4-pro | 2 | 100 | 100 | +0 | +68305 | -28.2 |
| BR-10 | glm-5.2 | 1 | 100 | 75 | -25 | -632024 | +42.7 |
| BR-10 | glm-5.2 | 2 | 0 | 0 | excluded | +66226 | +374.8 |
| BR-10 | kimi-k2.7-code | 1 | 100 | 100 | +0 | -16123 | -52.1 |
| BR-10 | kimi-k2.7-code | 2 | 100 | 100 | +0 | -489602 | +129.0 |
| BR-11 | deepseek-v4-pro | 1 | 30 | 90 | excluded | +1429424 | +376.7 |
| BR-11 | deepseek-v4-pro | 2 | 70 | 0 | excluded | -1123132 | -454.2 |
| BR-11 | glm-5.2 | 1 | 0 | 0 | excluded | -1744582 | -402.6 |
| BR-11 | glm-5.2 | 2 | 40 | 20 | -20 | -88476 | +1448.3 |
| BR-11 | kimi-k2.7-code | 1 | 20 | 0 | excluded | -2164065 | -584.7 |
| BR-11 | kimi-k2.7-code | 2 | 10 | 20 | +10 | +25105 | +90.5 |
| BR-12 | deepseek-v4-pro | 1 | 50 | 0 | excluded | -877354 | -211.8 |
| BR-12 | deepseek-v4-pro | 2 | 0 | 0 | excluded | +0 | +0.0 |
| BR-12 | glm-5.2 | 1 | 50 | 0 | excluded | -1589749 | -287.2 |
| BR-12 | glm-5.2 | 2 | 60 | 0 | excluded | -1016941 | -132.5 |
| BR-12 | kimi-k2.7-code | 1 | 60 | 0 | excluded | -359879 | +102.0 |
| BR-12 | kimi-k2.7-code | 2 | 60 | 0 | excluded | -825758 | -44.6 |

## Descriptive result

6/18 pairs are capability-analysis eligible. Direct averaged 75.0; plan→build averaged 69.2; paired delta averaged -5.8. Plan won 1, tied 3, and lost 2.

Among provider-valid plan attempts, the frozen semantic gate passed 5 and blocked 10. A gate pass is build admission, not a prediction of full functional completion.

BR-12 may later serve as a Meta-Harness task-level holdout. Its results must be
withheld from the proposer during search even though they remain in the audited
experiment store.

## Integrity and reliability

3 attempts keep their functional evidence but are excluded from capability comparisons because evaluator recoveries exceeded the frozen combined token budget:

- `day3v2-br-11-dsv4pro-r1-plan-build`: 2269107 charged tokens.
- `day3v2-br-11-k2.7code-r1-direct-build`: 3225981 charged tokens.
- `day3v2-br-11-glm52-r1-direct-build`: 2311278 charged tokens.

3 attempts are provider-invalid reliability evidence, not functional zeroes:

- `day3v2-br-12-dsv4pro-r1-plan-build`: terminal provider state `provider_rejected_before_stream`.
- `day3v2-br-12-dsv4pro-r2-plan-build`: terminal provider state `provider_rejected_before_stream`.
- `day3v2-br-12-dsv4pro-r2-direct-build`: terminal provider state `provider_rejected_before_stream`.

## Gate replay layer

These builds reuse frozen plans under corrected gates. They are diagnostic
counterfactuals and are not mixed into the 36-slot paired matrix.

| Task | Model | Source slot | Score | Tokens | Seconds |
|---|---|---|---:|---:|---:|
| BR-11 | deepseek-v4-pro | `day3v2-br-11-dsv4pro-r2-plan-build` | 30 | 1525865 | 510.6 |
| BR-11 | glm-5.2 | `day3v2-br-11-glm52-r1-plan-build` | 100 | 2077110 | 1209.6 |
| BR-12 | glm-5.2 | `day3v2-br-12-glm52-r1-plan-build` | 100 | 1154602 | 496.0 |
