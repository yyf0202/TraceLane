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

样本还少，而且中途更换过 provider。目前只能描述这些 run，不能据此声称 plan→build 或 direct-build 普遍更好。
