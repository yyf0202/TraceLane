# TraceLane

TraceLane 是一个 coding agent 评测仓库。

我们想解决一个朴素的问题：一次 coding run 到底做了什么，为什么通过或失败，换一种工作流后结果有没有变好。

每次实验会固定任务、代码基线和预算，保存 session trace、workspace diff、独立验收结果与成本。TraceLane 不负责写代码，它负责把评测链路留清楚。

## 目标

- 让一次 run 可以检查和复盘；
- 在同一任务与预算下比较不同工作流；
- 用独立 grader 判断功能，而不是只看 agent 自述；
- 保留失败样本，不把偶发故障悄悄重跑掉。

## 已有实验

### Day 1：链路验证

两个小型 BeRicher 修复任务，各运行一次 direct-build 和 plan→build。四条 run 均通过功能验收。这组数据只说明评测链路能工作。

| Task | Workflow | Result | Provider input（含缓存） | Wall | Cost |
| --- | --- | ---: | ---: | ---: | ---: |
| BR-01 | direct-build | pass | 482,751 | 272.4 s | $0.3416 |
| BR-01 | plan→build | pass | 193,201 | 226.5 s | $0.1216 |
| BR-02 | direct-build | pass | 313,018 | 220.1 s | $0.3086 |
| BR-02 | plan→build | pass | 728,983 | 436.4 s | $0.6607 |

[完整记录](docs/experiments/2026-07-27-day1-coding-eval.md)

### BR-05：复杂任务串行对比

同一冻结基线上的五组 direct-build 与 plan→build。R1–R2 使用 OpenCode Go，R3–R5 使用 Ark。每条 run 的总预算为 1,800 秒、220 次工具调用、2,000,000 model tokens。

| Repeat | Provider | Workflow | End | Plan gate | Function | Model tokens | Wall |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | OpenCode Go | plan→build | completed | 100/100 | 100/100 | 810,558 | 642.5 s |
| 1 | OpenCode Go | direct-build | wall budget | — | 40/100 | 1,361,682 | 1,800.5 s |
| 2 | OpenCode Go | plan→build | token budget | 100/100 | 90/100 | 2,032,532 | 1,090.9 s |
| 2 | OpenCode Go | direct-build | wall budget | — | 0/100 | 1,137,141 | 1,800.3 s |
| 3 | Ark | plan→build | plan gate failed | 75/100 | 0/100 | 227,260 | 285.3 s |
| 3 | Ark | direct-build | token budget | — | 40/100 | 2,011,626 | 1,328.4 s |
| 4 | Ark | plan→build | provider response stalled | — | 0/100 | 10,551 | 1,201.0 s |
| 4 | Ark | direct-build | token budget | — | 40/100 | 2,080,292 | 473.5 s |
| 5 | Ark | plan→build | build token budget | 100/100 | 80/100 | 2,085,380 | 583.4 s |
| 5 | Ark | direct-build | token budget | — | 40/100 | 2,116,463 | 510.0 s |

[完整记录](docs/experiments/2026-07-27-br05-serial-pair.md)

### Day 2：三个复杂任务、三个模型

36 个预注册槽位严格串行运行。5 条被 Ark 配额窗口拒绝，6 条 plan 被旧 gate
错误拦下；这些样本保留在故障与 gate 记录中，不混入功能配对。剩余 9 个可分析配对里，
plan→build 平均 48.3/100，direct-build 平均 56.7/100，plan 赢 3、平 3、输 3。

| Model | 可分析配对 | Direct 平均 | Plan 平均 | 平均差 |
| --- | ---: | ---: | ---: | ---: |
| deepseek-v4-pro | 4 | 30.0 | 56.2 | +26.2 |
| glm-5.2 | 2 | 100.0 | 100.0 | 0.0 |
| kimi-k2.7-code | 3 | 63.3 | 3.3 | -60.0 |

各模型留下的可分析任务构成不同，这张表不能当作模型排名。旧 gate 放行 6/16 个
配额有效 plan；版本化裁决认为 12 个应通过，其中 6 个没有 build 结果。

[完整记录](docs/experiments/2026-07-27-day2-results.md)

### Day 2 补充：配额恢复与 gate replay

对 3 个受配额影响的 BR-07 配对做了串行恢复，但 6 条 attempt 再次遇到 Ark
流中断或无响应头，因此不作为能力分数。另将旧 gate 错拦的 6 条计划在修正 gate
下重放 build：6 条都能启动并得到 30–80 个功能点，但都在继承剩余预算后触及
token 上限。BR-08 的 5 个可对照 replay 中，plan 赢 1、平 3、输 1，平均差 -1 分。

[完整记录](docs/experiments/2026-07-28-day2-recovery.md)

BR-07 随后用新 ID 再做一次恢复，6 条均获得稳定响应。三个配对中 plan→build
得到 60、60、30 分，direct-build 均为 60 分；plan 0 胜、2 平、1 负，平均差
-10 分。所有 attempt 都在约 2M token 时终止。

[BR-07 recovery2](docs/experiments/2026-07-28-day2-recovery2.md)

样本仍少，而且存在 provider 与 gate 缺失数据。目前只能描述这些 run，不能据此声称
某种工作流或模型普遍更好。

[Day 2 分层总表](docs/experiments/2026-07-28-day2-consolidated.md)

### Day 3：跨任务矩阵

BR-10–12 的 36 个预注册槽位已完成并导入，另保留 3 条独立 gate replay。只有
6/18 个配对同时满足 provider、预算和 build 完整性：direct-build 平均 75.0，
plan→build 平均 69.2；plan 赢 1、平 3、输 2。这只是 BeRicher 上的描述性结果。

3 条 Ark HTTP 429 和 3 条累计超预算的 evaluator recovery 不进入能力对比；
gate replay 也不混入原矩阵。

[结果](docs/experiments/2026-07-28-day3-results.md) ·
[任务集](docs/experiments/bericher-v0.9-taskset.md) ·
[预注册](docs/experiments/2026-07-28-day3-preregistration.md)

## 接下来

现有数据可用于验证 Meta-Harness 的导入、提案、隔离构建和盲测 plumbing，但正式
优化实验仍需要更多未被 proposer 看过的跨任务 holdout。

[Meta-Harness 就绪性](docs/experiments/2026-07-28-meta-harness-readiness.md)

实验统一从 `scripts/coding_eval.py` 进入。正式运行前会串行检查所有 provider；
检查结果只用于判断请求链路是否健康，不计为 coding attempt。
