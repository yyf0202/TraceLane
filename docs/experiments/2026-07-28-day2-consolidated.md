# Day 2 layered evidence inventory

This document indexes the Day 2 evidence without rewriting or pooling its layers.
All comparisons are descriptive and repository-specific.

## Layer inventory

| Layer | Attempts | Provider-valid records | Purpose |
|---|---:|---:|---|
| Original preregistered matrix | 36 | 31 slots | Primary Day 2 matrix |
| Recovery1 | 6 | 0 attempts | Ark failure record |
| Corrected-gate replay | 6 | 6 builds | Diagnose frozen-gate false blocks |
| BR-07 recovery2 | 6 | 6 attempts | Three replacement pairs with stable transport |

## Recovery2 matched pairs

| Model | Repeat | Direct | Plan→build | Δ | Direct tokens | Plan tokens |
|---|---:|---:|---:|---:|---:|---:|
| glm-5.2 | 1 | 60 | 60 | +0 | 2044406 | 2004780 |
| glm-5.2 | 2 | 60 | 30 | -30 | 2085543 | 2038223 |
| kimi-k2.7-code | 2 | 60 | 60 | +0 | 2009650 | 2041882 |

Recovery2 direct averaged 60.0; plan→build averaged 50.0; mean paired delta -10.0. Plan won 0, tied 2, and lost 1.

## Gate replay

| Task | Model | Frozen gate | Corrected gate | Function | End |
|---|---|---:|---:|---:|---|
| BR-06 | kimi-k2.7-code | 75 | 100 | 80 | token_budget_exhausted |
| BR-08 | kimi-k2.7-code | 65 | 100 | 55 | token_budget_exhausted |
| BR-08 | glm-5.2 | 65 | 100 | 30 | token_budget_exhausted |
| BR-08 | glm-5.2 | 65 | 100 | 55 | token_budget_exhausted |
| BR-08 | deepseek-v4-pro | 65 | 100 | 30 | token_budget_exhausted |
| BR-08 | deepseek-v4-pro | 65 | 100 | 35 | token_budget_exhausted |

The corrected gate allowed all six frozen plans to build and recover 30–80
functional points. This establishes frozen-gate false negatives; it does not
make a 100/100 plan score a predictor of full implementation.

## Interpretation boundary

- Original matrix estimates remain exactly as originally reported.
- Recovery1 transport failures are not functional zeroes.
- Recovery2 replaces missing evidence only within its three explicitly labeled pairs.
- Gate replays are counterfactual build diagnostics, not preregistered matrix rows.
- Ark and OpenCode Go remain separate provider strata.
