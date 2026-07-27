# Day 2 complex-task matrix

The 36 preregistered attempt slots ran strictly serially through Ark. Attempts
rejected by Ark's account-quota window remain in the reliability record but are
excluded from capability deltas. BR-07/08 analysis scores use frozen v2
adjudications while retaining the original v1 scores. These are descriptive paired
results, not a statistical-significance claim, and they are not pooled with
OpenCode Go.

## Attempt summary

| Task | Model | Workflow | Scores | Full | Normal finish | Mean tokens | Mean seconds |
|---|---|---|---:|---:|---:|---:|---:|
| BR-06 | deepseek-v4-pro | direct-build | 0/0 | 0/2 | 0/2 | 40550 | 139.5 |
| BR-06 | deepseek-v4-pro | plan-build | 80/0 | 0/2 | 1/2 | 687616 | 226.0 |
| BR-06 | glm-5.2 | direct-build | 100/100 | 2/2 | 0/2 | 2021608 | 425.9 |
| BR-06 | glm-5.2 | plan-build | 100/100 | 2/2 | 2/2 | 1450067 | 421.3 |
| BR-06 | kimi-k2.7-code | direct-build | 0/100 | 1/2 | 0/2 | 1026336 | 285.9 |
| BR-06 | kimi-k2.7-code | plan-build | 0/0 | 0/2 | 0/2 | 919573 | 381.1 |
| BR-07 | deepseek-v4-pro | direct-build | 60*/60* | 0/2 | 2/2 | 1241042 | 276.3 |
| BR-07 | deepseek-v4-pro | plan-build | 75*/70* | 0/2 | 2/2 | 1513152 | 479.8 |
| BR-07 | glm-5.2 | direct-build | 0*/0* | 0/2 | 0/2 | 0 | 9.1 |
| BR-07 | glm-5.2 | plan-build | 0*/60* | 0/2 | 0/2 | 1016340 | 319.6 |
| BR-07 | kimi-k2.7-code | direct-build | 60*/0* | 0/2 | 0/2 | 1017982 | 172.2 |
| BR-07 | kimi-k2.7-code | plan-build | 0*/0* | 0/2 | 0/2 | 158240 | 141.4 |
| BR-08 | deepseek-v4-pro | direct-build | 30*/35* | 0/2 | 1/2 | 1595360 | 467.6 |
| BR-08 | deepseek-v4-pro | plan-build | 10*/10* | 0/2 | 0/2 | 280586 | 325.2 |
| BR-08 | glm-5.2 | direct-build | 55*/55* | 0/2 | 0/2 | 2024352 | 575.3 |
| BR-08 | glm-5.2 | plan-build | 10*/10* | 0/2 | 0/2 | 231270 | 436.5 |
| BR-08 | kimi-k2.7-code | direct-build | 35*/30* | 0/2 | 0/2 | 2039338 | 448.1 |
| BR-08 | kimi-k2.7-code | plan-build | 10*/10* | 0/2 | 0/2 | 400670 | 265.3 |

## Paired deltas

Positive score means plan→build completed more functional points. Positive
resource values mean plan→build used more.

| Task | Model | Repeat | Score Δ | Token Δ | Seconds Δ |
|---|---|---:|---:|---:|---:|
| BR-06 | deepseek-v4-pro | 1 | +80 | +1294131 | +153.0 |
| BR-06 | deepseek-v4-pro | 2 | +0 | +0 | +20.2 |
| BR-06 | glm-5.2 | 1 | +0 | -845126 | +56.3 |
| BR-06 | glm-5.2 | 2 | +0 | -297956 | -65.6 |
| BR-06 | kimi-k2.7-code | 1 | excluded (frozen-gate false block) | +783148 | +411.9 |
| BR-06 | kimi-k2.7-code | 2 | -100 | -996673 | -221.5 |
| BR-07 | deepseek-v4-pro | 1 | +15 | +387847 | +277.0 |
| BR-07 | deepseek-v4-pro | 2 | +10 | +156371 | +130.0 |
| BR-07 | glm-5.2 | 1 | excluded (quota) | +0 | -3.0 |
| BR-07 | glm-5.2 | 2 | excluded (quota) | +2032681 | +624.1 |
| BR-07 | kimi-k2.7-code | 1 | -60 | -1775280 | -74.6 |
| BR-07 | kimi-k2.7-code | 2 | excluded (quota) | +55795 | +13.1 |
| BR-08 | deepseek-v4-pro | 1 | excluded (frozen-gate false block) | -1714983 | -182.2 |
| BR-08 | deepseek-v4-pro | 2 | excluded (frozen-gate false block) | -914565 | -102.7 |
| BR-08 | glm-5.2 | 1 | excluded (frozen-gate false block) | -1879701 | -269.8 |
| BR-08 | glm-5.2 | 2 | excluded (frozen-gate false block) | -1706462 | -7.9 |
| BR-08 | kimi-k2.7-code | 1 | excluded (frozen-gate false block) | -1642293 | -249.8 |
| BR-08 | kimi-k2.7-code | 2 | -20 | -1635041 | -115.7 |

## Model view

| Model | Eligible pairs | Direct mean | Plan mean | Mean Δ | W/T/L | Quota-rejected slots |
|---|---:|---:|---:|---:|---:|---:|
| deepseek-v4-pro | 4 | 30.0 | 56.2 | +26.2 | 3/1/0 | 0 |
| glm-5.2 | 2 | 100.0 | 100.0 | +0.0 | 0/2/0 | 3 |
| kimi-k2.7-code | 3 | 63.3 | 3.3 | -60.0 | 0/0/3 | 2 |

Eligible-pair composition differs by model because quota rejection and frozen
gate false blocks are excluded. This table describes observed model/workflow
outcomes; it is not a controlled ranking of the three models.

## Answers to the three questions

### Did planning improve complex-feature completion?

Across 9 analysis-eligible matched pairs, plan→build averaged 48.3/100 and direct-build averaged 56.7/100. The paired score delta averaged -8.3 points; plan won 3 pairs, tied 3, and lost 3.

### Did the plan gate predict build success?

The frozen gate passed 6/16 quota-eligible plans; versioned adjudication passes 12. 6 adjudicated passes were blocked by the frozen gate, so no build outcome exists for them. Of the 6 adjudicated passes that actually built, 2 reached 100/100 functional completion. 4 plans still fail after adjudication. This is descriptive gate calibration, not a general predictive claim.

### Did plan overhead buy more functional slices?

Plan→build used an average of -412414 tokens and +17.7 seconds per pair relative to direct-build, for the -8.3-point mean functional delta above. The paired table shows where extra resources did and did not buy slices.

## Provider lifecycle

Last-turn states across imported phases: `completed` 24, `gateway_no_response_headers` 11, `model_completed_processor_incomplete` 2, `provider_rejected_before_stream` 5.

Local budget terminations: `token_budget_exhausted` 11. No failed attempt was automatically rerun.

## Scope

The preregistered matrix contains three tasks, three Ark models, two workflows,
and two repeats. Asterisks in the score table mark BR-07/08 v2 adjudication.
Quota-rejected attempts are retained as provider evidence but excluded from
capability deltas; frozen-gate false blocks are retained for gate calibration
but excluded from build-completion deltas because no build outcome exists.
The matrix can reveal concrete cross-task patterns and gate failures; it is too
small and repository-specific for a statistical or universal claim about planning.
