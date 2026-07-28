# Plan gate contract

Plan gate 是 build 的准入检查，不是 build 成功预测器。

它回答的是：计划是否覆盖了任务中不可省略的状态、时序、作用域和验证语义。它不判断
代码是否已经正确，也不因为计划使用某个 helper 名称或某一种代码结构就额外加分。

## 应通过

- 关键语义正确，但 helper、变量名或控制流组织方式不同；
- 明确覆盖 editable scope、状态转换、时间边界、失败路径和验证；
- 使用与冻结目标等价的中英文表达。

## 应失败

- 文字包含正确关键词，但伪代码与文字结论矛盾；
- 把逐时间步语义退化成时间均值或单步广播；
- 声称按组执行，实际仍逐 simulation 调用；
- checkpoint 位于 epoch loop 之外；
- 用全局 rolling 代替 per-symbol rolling，或在过滤后才豁免持仓。

## 版本与结果

- 已产生的 plan、gate 分数和 build 结果永不回写；
- gate 修复必须新建版本，并加入正例、等价实现和“正确文字 + 错误语义”反例；
- 被旧 gate 阻断的计划只能通过 build-only `gate-replay` 补充；
- replay 复用原 plan 和 prompt，并扣除原 plan 已消耗的墙钟、工具调用与 token；
- gate-replay 单独报告，不进入原预注册矩阵。

Gate 100/100 只表示计划具备进入 build 的必要语义。功能完成度仍由独立隐藏 grader
决定。

当前 BR-08 v4 在保留 v3 结果不变的前提下，新增了“文字声称 grouped once，但伪代码
仍然 `for sim ... run_date_range_multi`”的反例。
