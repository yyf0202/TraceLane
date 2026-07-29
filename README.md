# TraceLane

TraceLane 是一个 coding agent 评测仓库。

它固定任务、代码基线和预算，保存 session trace、workspace diff、独立验收结果与成本，用来回答两个问题：一次 coding run 实际做了什么；换一种工作流后，结果有没有变好。

## 目标

- 让每次 run 可以检查和复盘；
- 在同一任务与预算下比较工作流；
- 用独立 grader 判断功能，并保留失败样本。

## 已有数据

| 实验 | 任务 | 正式槽位 | 可分析配对 | 结果 |
| --- | ---: | ---: | ---: | --- |
| Day 1 | 2 | 4 | 2 | 四条 run 均通过；只验证评测链路 |
| BR-05 | 1 | 10 | 5 | 同一复杂任务的重复实验 |
| Day 2 | 3 | 36 | 9 | direct 56.7，plan 48.3；plan 3 胜、3 平、3 负 |
| Day 3 | 3 | 36 | 6 | direct 75.0，plan 69.2；plan 1 胜、3 平、2 负 |

Provider 故障、预算异常和 gate replay 单独记录，不混入能力比较。现有结果只描述 BeRicher 上的这些 run，不代表统计显著性，也不能当作通用模型排名。

## 记录

- [Day 1](docs/experiments/2026-07-27-day1-coding-eval.md)
- [BR-05](docs/experiments/2026-07-27-br05-serial-pair.md)
- [Day 2](docs/experiments/2026-07-28-day2-consolidated.md)
- [Day 3](docs/experiments/2026-07-28-day3-results.md)
- [任务集](docs/experiments/bericher-v0.9-taskset.md)
- [Meta-Harness 就绪性](docs/experiments/2026-07-28-meta-harness-readiness.md)
