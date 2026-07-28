# Meta-Harness readiness

## Decision

现有数据可以用于验证 proposer 是否能读取历史、提出候选修改、触发评测并保存新一代
结果，但还不能用于可信的 Meta-Harness 优化实验，更不能据此声称自动发现的 harness
优于当前工作流。

问题不在 raw trace 的总量。TraceLane 已经保存了代码基线、任务、workflow、完整 trace、
独立分数、diff、成本和终止原因，具备经验仓库的基本形状。真正的缺口是不同任务太少，
重复主要集中在同一个 BeRicher 仓库和少数任务上，且 Day 2 仍包含 provider 配额、
流中断和旧 gate 误拦等缺失机制。

## Readiness check

| Requirement | Status | Evidence or gap |
|---|---|---|
| Candidate code, score and raw trace are jointly queryable | Ready | Imported run stores retain task, attempt, workspace, trace, grader and cost |
| Evaluation runs independently from proposer | Ready | Frozen hidden graders and the unified coding-eval entry point |
| Transport failures are distinguishable from capability failures | Ready for new runs | Provider preflight and request-lifecycle observations |
| Resource overshoot is explicit | Ready for new runs | Wall, tool and model-token limits record observed overshoot |
| Gate changes are immutable and replayable | Ready | Versioned gate contract and separately labelled gate replay |
| Search set is difficult and non-saturated | Partial | BR-05–08 are difficult, but only four executed complex tasks |
| Enough task diversity for code-space search | Not ready | All executed tasks come from one repository; repeated runs do not replace new tasks |
| Independent validation or held-out tasks | Not ready | No task split has been frozen before proposer access |
| Stable matched evidence across workflows and models | Partial | Recovery improves coverage, but missingness remains provider- and gate-dependent |
| Candidate-selection objective is frozen | Not ready | Quality/cost Pareto rules and promotion criteria are not yet preregistered |

## What can run now

A smoke-only outer loop may use existing attempts to test:

1. filesystem/history navigation;
2. candidate parent and change-manifest creation;
3. lightweight interface validation;
4. evaluation dispatch and artifact ingestion;
5. Pareto-front calculation over function score, tokens and wall time.

Its output must be labelled plumbing evidence. Existing Day 1/2 results must not be used both to
shape a candidate and to claim that candidate generalizes.

## Evidence needed before a real experiment

Return to work items 7–9:

1. Freeze task-specific plan gates for BR-10–12 before any model sees the tasks.
2. Run BR-10–12 with `glm-5.2`, `deepseek-v4-pro` and `kimi-k2.7-code`; direct-build and
   plan→build each repeat twice, strictly serial and alternating execution order.
3. Keep provider strata separate and preserve every failed slot. Add a third repeat only for a
   preregistered dispute rule.
4. Use BR-10 and BR-11 as the initial search set and reserve BR-12 as a sealed task-level holdout.
   The holdout grader result must remain unavailable to the proposer until candidate selection.
5. Freeze the optimization target before search: functional slice score first, then Pareto-report
   model tokens and wall time; provider failures are missing observations, not zero scores.
6. After the first held-out check, add tasks from a second repository before making a
   repository-general claim.

This is a deliberately smaller pilot than the published Meta-Harness experiments. It can answer
whether TraceLane's outer loop produces a promising, auditable candidate; it cannot establish a
general harness improvement from three new tasks.

## Basis

The [Meta-Harness paper](https://arxiv.org/html/2603.28052v1) exposes every prior candidate's
source, score and execution trace to a coding-agent proposer, evaluates proposed harnesses outside
the proposer, and retains the complete history. Its authors recommend a difficult, discriminative
search set, lightweight validation before expensive evaluation, and roughly 50 candidate
evaluations per search run. Their coding demonstration used 89 challenging tasks and ten search
iterations; other experiments separate search feedback from held-out tasks or models.
