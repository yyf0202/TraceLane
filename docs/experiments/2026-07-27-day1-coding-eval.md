# TraceLane × OpenCode Day 1 coding-eval

Date: 2026-07-27

Model: `opencode-go/glm-5.2`

Observer revision: OpenCode fork `7cd3d44`

## Scope

This is a four-attempt vertical-slice check of the coding evaluation chain. It
tests whether TraceLane can freeze a historical task and baseline, preserve a
direct or plan→build OpenCode session tree, capture the final workspace and
diff, run independent acceptance checks, and retain provider cost evidence.

The sample is intentionally too small for statistical inference. The results
must not be described as evidence that either workflow is generally better.

## Frozen results

Input tokens below separate uncached and cached provider input. Trace size is
the combined raw OpenCode observation JSONL size; CLI JSONL is retained
separately as the source for provider cost.

| Task | Workflow | Result | Sessions | Changed paths | Input + cached | Output | Agent wall | Cost (USD) | Raw trace | Run ID |
|---|---|---:|---:|---|---:|---:|---:|---:|---:|---|
| BR-01 | direct-build | pass | 1 | `src/components/data_fetcher_tushare.py` | 178,751 + 304,000 | 2,801 | 272.4s | $0.341616 | 2.23MB | `2bf1eee501c40031f83d0881cee32082746b04255480d118be990014ddf48776` |
| BR-01 | plan→build | pass | 2 | `src/components/data_fetcher_tushare.py` | 53,873 + 139,328 | 2,255 | 226.5s | $0.121569 | 1.26MB | `7b26b90ce2a91e9f4c732c3d9ac75fb1acac83fdb36288bdc772f91a8993daec` |
| BR-02 | direct-build | pass | 1 | `src/cli/daily_run.py` | 188,410 + 124,608 | 2,819 | 220.1s | $0.308576 | 1.69MB | `206071e1b39dc2d7e465e6bf08998988f194e661194158c8e26ef6f4fd05df09` |
| BR-02 | plan→build | pass | 2 | `src/cli/daily_run.py` | 394,007 + 334,976 | 5,009 | 436.4s | $0.660743 | 3.63MB | `b1614382da8bbd725a268b0b91725683cea690bd82fc598397b8d0c3f9438120` |

All four runs passed:

- public syntax and diff checks;
- task-specific hidden acceptance;
- a SHA-256 check that binds each hidden grader's contents to the task;
- editable/protected/ignored path grading;
- final-workspace capture relative to the pinned baseline.

## Direct vs plan→build

The two paired tasks move in opposite directions:

- BR-01 plan→build used 60.0% fewer provider input tokens (including cached
  input), cost 64.4% less, and completed 16.9% faster than direct-build.
- BR-02 plan→build used 132.9% more provider input tokens, cost 114.1% more,
  and took 98.3% longer than direct-build.

These are descriptive measurements of two task pairs, not an estimated
workflow effect. More frozen tasks and repeated runs are required before any
comparative claim.

## Adapter limitation

OpenCode CLI did not reliably switch an existing session from `plan` to
`build` using `--agent build`. Each plan→build attempt therefore uses two
independent CLI sessions. During import, the build session is explicitly
linked as a child of the plan root and the attempt records
`phase_link: manual-cli-split`. The link is an adapter assertion, not a native
OpenCode parent relationship.

## Reproduction and artifacts

Run `scripts/import_day1_coding_eval.py` with the four pinned worktree paths.
The generated, git-ignored artifact root is
`artifacts/day1-coding-eval/`. Each run contains:

```text
input/coding-task.json
input/attempt.json
input/sessions/<session-id>.json
workspace/initial.json
workspace/initial.patch
workspace/final.json
workspace/final.patch
trace/events.jsonl
output/coding-grades.json
output/provider-cost.json
```

Frozen task manifests live under `fixtures/coding/bericher-v0.1/`. Baseline
`tree_sha256` values are SHA-256 digests of the deterministic `git archive`
tar stream for the pinned commit.
