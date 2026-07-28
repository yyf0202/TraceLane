# Day 2 BR-07 recovery2

`recovery1` 的 6 条 BR-07 v2 attempt 因 Ark 流中断或无响应头而不能用于能力比较。
链路恢复后，我们用新的 run ID 重跑同样 3 个配对。原始矩阵和 `recovery1` 均保持不变。

执行仍然严格串行、没有自动重试。每条 attempt 的总预算是 1,800 秒、220 次工具调用、
2,000,000 model tokens；plan 与 build 共享总预算。

## 结果

| Model | Repeat | Direct | Plan→build | 分数差 | Direct tokens | Plan tokens | 时间差 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| kimi-k2.7-code | 2 | 60 | 60 | 0 | 2,009,650 | 2,041,882 | +439.0 s |
| glm-5.2 | 1 | 60 | 60 | 0 | 2,044,406 | 2,004,780 | +256.2 s |
| glm-5.2 | 2 | 60 | 30 | -30 | 2,085,543 | 2,038,223 | -18.4 s |

三组 direct 平均 60/100，plan→build 平均 50/100，平均差 -10。plan 赢 0、平 2、
输 1。样本只有三个配对，这不是统计结论。

6 条 attempt 都稳定获得 Ark 响应并进入实现阶段，最终都在完整 step 结束后被本地
token gate 终止。观测 token 略超过 2,000,000 是 step 边界的离散超调，不是放宽预算。

## 功能切片

5 条 60 分实现完成了：

- 独立的 `factor_logvar_max` prior/posterior factor-head 约束；
- config、model kwargs 与 CLI 的完整透传。

GLM r2 plan→build 得到 30 分，只完成了 factor-head 约束，没有完成配置和 CLI 闭环。
所有 6 条都没有完成：

- 每个 epoch 保存可加载 checkpoint，且 checkpoint I/O 失败不使训练失败；
- fold 训练开始前写入 partial kfold metadata。

因此，本批次的主要瓶颈不是 Ark transport，也不是 plan gate，而是在 2M token 内把
“模型参数链路”和“训练恢复/时序语义”同时做完。plan 没有提高已完成切片，且两组平局
都付出了更多墙钟时间。

## 边界

这批结果只补齐 BR-07 的三个缺失配对。它不与 `recovery1` 的基础设施失败分数合并，
也不改变原始 36 条预注册结果。机器可读记录在
`artifacts/day2-recovery2/results.json`，其中包含 6 条独立 CodingTask attempt、
workspace snapshot、trace、grader、diff、成本和 provider lifecycle。
