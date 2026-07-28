# Day 2 recovery and gate replay

这次追加实验不改写原始 36 条结果，分成两个独立层：

1. 对受 Ark 配额窗口影响的 3 个 BR-07 v2 配对重新执行；
2. 对被旧 plan gate 错拦的 6 条计划，只重放 build，并继承原计划剩余预算。

所有 attempt 严格串行执行，没有自动重试。以下仍是少量、仓库特定的描述性证据。

## Quota recovery

| Model | Repeat | Workflow | 终态 | Model tokens | 功能分 |
| --- | ---: | --- | --- | ---: | ---: |
| kimi-k2.7-code | 2 | plan→build | 中途流式响应超时 | 150,060 | 不纳入 |
| kimi-k2.7-code | 2 | direct-build | Ark 无响应头 | 0 | 不纳入 |
| glm-5.2 | 1 | plan→build | Ark 无响应头 | 0 | 不纳入 |
| glm-5.2 | 1 | direct-build | Ark 无响应头 | 0 | 不纳入 |
| glm-5.2 | 2 | direct-build | Ark 无响应头 | 0 | 不纳入 |
| glm-5.2 | 2 | plan→build | Ark 无响应头 | 0 | 不纳入 |

这 3 个配对仍然没有形成可用的能力对比。Kimi plan 已完成若干轮并消耗
150,060 tokens，随后 CLI 报 `SSE read timed out`；其余 5 条请求已发出，但没有
响应头、首 token、tool call 或 token 消耗。它们是基础设施样本，不是 0 分模型样本。

稍后执行的 6 条 gate replay 均能持续获得 Ark 响应，因此这里更像一个时间局部的
网关/连接故障，而不是任务、模型或总 token 预算造成的统一失败。

## Corrected-gate build replay

| Task | Model | 旧 gate | 修正 gate | 功能分 | Plan tokens | Build tokens | 合计 tokens | Build 终态 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| BR-06 v2 | kimi-k2.7-code | 75 | 100 | 80 | 792,804 | 1,250,041 | 2,042,845 | token budget |
| BR-08 | kimi-k2.7-code | 65 | 100 | 55* | 377,549 | 1,624,881 | 2,002,430 | token budget |
| BR-08 | glm-5.2 r1 | 65 | 100 | 30* | 148,579 | 1,880,445 | 2,029,024 | token budget |
| BR-08 | glm-5.2 r2 | 65 | 100 | 55* | 313,962 | 1,725,532 | 2,039,494 | token budget |
| BR-08 | deepseek-v4-pro r1 | 65 | 100 | 30* | 336,342 | 1,688,091 | 2,024,433 | token budget |
| BR-08 | deepseek-v4-pro r2 | 65 | 100 | 35* | 224,830 | 1,800,028 | 2,024,858 | token budget |

星号表示使用 BR-08 v2 的实现等价裁决；原冻结 grader 分数仍保留。总 token 略高于
2,000,000 是 runner 在完整 step 结束后才观察到越界并终止造成的离散超调，不是放宽预算。

修正 gate 的 6 条计划全部通过并真正进入 build，最终得到 30–80 个功能点，平均
47.5/100。这说明旧 gate 的假阴性有实质影响：它阻止了本来能够完成部分功能的 build。

但 100/100 plan gate 并不意味着 build 会满分。6 条 replay 没有一条达到 100/100，
且全部在继承剩余预算后触及 token 上限。gate 适合判断计划是否覆盖关键语义，不应被
解释成 build 成功预测器。

## 与 direct-build 的诊断性对照

BR-06 对应 direct run 本身是 provider 故障，因此不比较。其余 5 个 BR-08 replay
与原矩阵同 repeat 的 direct-build 对照如下：

| Model | Repeat | Direct | Plan replay | 差值 |
| --- | ---: | ---: | ---: | ---: |
| kimi-k2.7-code | 1 | 35 | 55 | +20 |
| glm-5.2 | 1 | 55 | 30 | -25 |
| glm-5.2 | 2 | 55 | 55 | 0 |
| deepseek-v4-pro | 1 | 30 | 30 | 0 |
| deepseek-v4-pro | 2 | 35 | 35 | 0 |

这 5 个诊断性配对中，plan 赢 1、平 3、输 1；direct 平均 42，plan replay 平均
41，平均差 -1。replay 与 direct 并非同一时间窗口执行，因此只用于补齐旧 gate
造成的缺失 build，不替代新的完整配对实验。

## 结论

- 修正后的 gate 消除了这 6 个已知假阴性，并允许所有 build 启动。
- gate 通过与功能满分不是一回事；复杂任务的剩余 build token 是当前主要约束。
- 本轮没有修复 BR-07 的缺失配对，因为 Ark 响应链路再次失败。
- 原始 36 条、recovery 和 gate replay 必须继续分层报告，不能合并成一个模型排名。

机器可读结果保存在 `artifacts/day2-recovery/results.json`；导入目录包含 12 条独立
CodingTask attempt、workspace snapshot、trace、grader、diff、provider diagnosis 和成本记录。
