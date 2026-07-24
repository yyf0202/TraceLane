# TraceLane v0.2 设计：拿破仑反事实研究与 Harness 诊断闭环

- 状态：已批准
- 日期：2026-07-24
- 目标版本：v0.2.0
- 首个用例：`HIST-001`

## 1. 目标

TraceLane v0.2 将 v0.1 的 trace-first Agent Harness 扩展为一套小型、可复现的
Harness 研究系统。它不仅运行 Agent 和打分，还要回答：

1. Agent 从哪一步开始失败；
2. 失败属于模型、Prompt、Context、Tool、Workflow、Memory、Data 还是 Eval；
3. 修改一个 Harness 变量后，失败是否稳定减少；
4. 改动是否解决目标问题，同时没有在其他任务上引入明显退化；
5. 运行轨迹能否沉淀为后续 Eval 和 Post-training 可消费的数据。

v0.2 的首个纵向用例是：

> 假设拿破仑在 1812 年决定不跨越涅曼河、不发动俄国远征。在仅使用决策时点可获得
> 信息的前提下，提出并比较替代战略，选择一条最可行的路线，并分析它可能如何影响
> 1812—1815 年的欧洲局势。

项目不判断某条虚拟历史是不是唯一正确答案。它评估研究过程是否遵守时间边界、是否
有证据、因果链是否连贯、是否保留关键约束，以及结论是否稳定。

## 2. 非目标

v0.2 不包含：

- 自动修改任意 Python 源码并合入主分支；
- RL、SFT 或其他模型权重训练；
- 大规模 Multi-Agent swarm；
- 自动搜索无限 Workflow 空间；
- 对外公共排行榜；
- 将反事实推演包装为确定的历史预测；
- 声称分叉重跑构成严格的因果证明。

## 3. 现有基础

v0.1 已经提供：

- `gather → analyze → debate? → finalize → validate → publish` Agent Loop；
- `raw` 与 `pit_budgeted` Context Policy；
- `always` 与 `conditional` Debate Policy；
- Point-in-Time 证据冻结；
- 追加式 JSONL trace；
- 哈希链 checkpoint 与可信恢复；
- Completion、Grounding、PIT、Recovery 和 Operational graders；
- 固定模型、Seed 和任务的 Context Policy 单变量 ablation；
- 确定性 Stub Runtime 和 12 个合成任务。

v0.2 在这些契约之上增加诊断、受控修复、分叉实验与开放研究任务，不重写已有的
artifact、trace 和 checkpoint 基础。

## 4. 两层循环

### 4.1 内层：完成研究任务

```text
Task + decision cutoff
  → freeze evidence
  → select context
  → build evidence ledger
  → analyze alternatives
  → counterargument
  → scenario branches
  → finalize research report
  → grade
```

内层产物是历史研究报告。它回答拿破仑在当时条件下有哪些选项、各自需要什么前提、
可能触发哪些后续分支，以及不确定性来自哪里。

### 4.2 外层：改进 Harness

```text
Run
  → inspect typed trace
  → validate constraints
  → locate first critical failure
  → attribute responsible layer
  → propose one scoped repair
  → human approval
  → branch from checkpoint
  → repeat and compare
  → promote or reject
  → create regression eval
```

外层产物是 Harness 实验报告。它说明失败在哪里、为什么归因到某一层、改变了什么、
目标指标如何变化、付出了多少成本，以及是否通过回归检查。

## 5. HIST-001 任务契约

### 5.1 决策节点

- 决策者：拿破仑及其帝国决策体系；
- 截止点：1812 年俄国远征正式开始之前；
- 唯一固定的反事实干预：不跨越涅曼河，不发动俄国远征；
- 推演窗口：1812—1815 年；
- 任务要求：提出至少两条替代战略，比较后选择一条，再推演多个条件分支。

不预先规定最终替代战略。重新谈判俄法关系、东线军事威慑、调整大陆封锁、处理
伊比利亚半岛和巩固现有帝国体系，都只是 Agent 可以研究的候选行动。

### 5.2 最小变更原则

反事实世界只在决策节点强制改变一件事：不发动俄国远征。其他变化必须由 Agent
说明因果路径，不能同时假定：

- 俄国自动接受法国条件；
- 英国停止抵抗；
- 西班牙战争自然结束；
- 奥地利与普鲁士永久忠诚；
- 法国获得现实中不存在的资源或情报。

### 5.3 Evidence Pack

Agent 可见证据优先使用决策时点前已经存在的公共领域一手材料或可合法再发布的
节选/转述。证据按以下主题组织：

- 俄法关系与大陆封锁；
- 法国、俄国及盟军的军事部署；
- 后勤、补给、距离与季节约束；
- 伊比利亚半岛战争；
- 奥地利、普鲁士、波兰与英国的利益；
- 法国国内政治、财政和帝国治理约束。

现代历史研究可以用于设计 Rubric 和人工校准，但默认不直接进入 Agent 的
Point-in-Time Context，避免把后见解释伪装成当时可用信息。

#### 5.3.1 Evidence Acquisition Lane

模型本身不直接拥有网络。TraceLane 将“在线找资料”和“使用冻结资料完成评测”分成
两个运行模式：

```text
live_research
  search_web → fetch_url → extract candidate → review → freeze bundle

frozen_eval
  list_evidence → read_evidence → analyze → grade
```

`live_research` 是证据采集过程，不产生可与 Benchmark 直接比较的正式得分。每次搜索
保存 Query、Provider、排名、Result URL 和搜索时间；每次抓取保存最终 URL、HTTP
元数据、抓取时间、Content Type、字节数、内容哈希和原始 Blob。提取出的 Evidence
Record 先进入 Candidate 状态，只有通过日期、来源、许可证和内容审核后才能被提升到
冻结 Evidence Manifest。

`frozen_eval` 默认禁用所有网络工具。它只允许读取 Manifest 明确引用的内容，从而保证
同一 Evidence Bundle 哈希对应同一组输入。若实时研究结果需要进入 Benchmark，必须
生成新 Bundle 版本和新哈希，不能静默改变旧 Bundle。

搜索服务通过 `SearchProvider` 接口接入，首个实现使用显式 API 配置；不把某个非正式
网页抓取技巧当成稳定搜索协议。HTTP Fetcher 限制协议、域名、重定向次数、超时、响应
大小和 Content Type，并在写入前执行 Secret/PII 扫描。网页内容可能包含 Prompt
Injection，因此采集文本始终标记为不可信数据，不能变成 Harness 指令。

### 5.4 历史时间与来源字段

v0.1 的 `EvidenceRecord` 只有 `available_at`、`source` 和 `text`。HIST-001 需要在
兼容现有字段的前提下补充：

```text
document_date
date_precision          # day | month | year | estimated
known_by_cutoff         # known | plausibly_known | unavailable
source_type             # primary | secondary | dataset
source_title
source_locator          # URL、卷页或档案定位
license
excerpt_kind            # verbatim | translated | paraphrased
provenance_sha256
```

`available_at` 继续作为 Harness 的硬性准入时间；新增字段解释历史来源的日期精度、
决策者可获得性和出处。

### 5.5 研究报告结构

Agent 输出至少包含：

1. 决策时点摘要；
2. 已知事实与证据账本；
3. 关键未知信息；
4. 不发动远征后可选的替代战略；
5. 每个战略的资源、外交和军事约束；
6. 选定战略及理由；
7. 至少三个后续条件分支；
8. 反方论证和可能失败方式；
9. 不确定性与不可判断部分；
10. 带证据引用的结论。

报告中的内容必须区分：

- `observed_fact`：证据直接支持的事实；
- `assumption`：推演所需假设；
- `inference`：从事实推导出的判断；
- `scenario`：条件成立时的可能后续；
- `unknown`：当前证据无法回答的内容。

## 6. Trace Schema v2

v0.1 的 `TraceEvent` 保留通用信封：

```text
sequence
event_type
stage
run_id
recorded_at
payload
```

v0.2 为 `event_type` 和 `payload` 增加可校验的类型契约，首批事件为：

```text
run.started
evidence.collected
evidence.rejected
context.selected
plan.created
model.called
tool.called
tool.observed
claim.created
assumption.created
scenario.branched
checkpoint.saved
constraint.checked
violation.detected
stage.completed
stage.failed
answer.finalized
grade.completed
diagnosis.completed
repair.proposed
repair.approved
replay.started
replay.completed
```

所有与证据相关的事件都记录 `evidence_id`；与 Harness 组件相关的事件记录
`component_id` 和配置哈希；诊断事件引用产生结论的 trace sequence，不能只保存一段
脱离原始轨迹的自然语言总结。

## 7. 失败诊断

### 7.1 行为失败类型

首版采用固定分类：

```text
plan_adherence_failure
invented_information
invalid_invocation
misinterpreted_tool_output
intent_plan_misalignment
under_specified_intent
unsupported_intent
guardrail_triggered
system_failure
temporal_leakage
unsupported_causal_claim
counterfactual_constraint_violation
```

前三组参考 AgentRx；最后三项针对 Time-Aware 历史研究补充。

### 7.2 责任层

行为失败与修复位置分开记录。责任层为：

```text
prompt
context_policy
tool_schema
workflow
memory_state
recovery_policy
runtime_model
evidence_data
evaluation
```

系统必须允许得出“无需修改 Harness”的结论。例如证据缺失应修 Evidence Pack，
Grader 错误应修 Eval，模型能力不足应记录为 `runtime_model`。

### 7.3 First Critical Failure

诊断输出：

```text
diagnosis_id
run_id
critical_sequence
failure_type
responsible_layer
evidence_sequences
constraint_violations
explanation
confidence
review_status
```

`critical_sequence` 是第一次使任务在现有后续步骤下无法自然恢复的错误，不等同于
最后触发失败的动作。

首版采用：

1. 确定性约束检查；
2. 固定 Rubric 的诊断器；
3. 对种植故障的人工标签校准。

不使用一个无约束的 LLM 对整条 trace 做自由总结，作为唯一诊断依据。

## 8. 受控修复

### 8.1 可修改范围

v0.2 允许的 Repair 类型：

```text
prompt_patch
context_policy_patch
tool_schema_patch
workflow_patch
guardrail_patch
evidence_patch
grader_patch
```

自动 proposer 只能生成结构化建议。人类批准后，Runner 才能应用白名单内的声明式
修改。任意源码修改、依赖安装和自动 Git 操作不在 v0.2 范围内。

### 8.2 Change Manifest

每项修改必须在运行前声明：

```text
change_id
parent_experiment_id
target_failure_type
target_responsible_layer
hypothesis
single_variable
control_value
treatment_value
expected_metric_changes
allowed_regressions
risk
rollback_condition
approval
```

修改后才补充：

```text
observed_metric_changes
prediction_result       # supported | contradicted | inconclusive
regression_result
decision                # promote | reject | retry
```

这使修改成为可证伪实验，而不是看到结果后再编理由。

## 9. Checkpoint 分叉与 Intervention Replay

v0.1 checkpoint 用于可信恢复；v0.2 增加实验分叉。

每个分叉记录：

```text
parent_run_id
parent_checkpoint_sha256
branch_id
change_id
changed_component
replay_mode             # suffix_live
repeat
```

`suffix_live` 表示 checkpoint 之前的状态固定，之后重新调用 Runtime。由于模型可能
具有随机性，TraceLane 不把单次分叉称为严格因果结论。

Runner 在 Context Policy 执行前保存 `evidence_frozen` 根 checkpoint。对于 Context
修复，Control 与 Treatment 都从同一个根 checkpoint 分叉：Control 应用显式 no-op，
Treatment 应用唯一获批改动；两条后缀都重新执行，且只通过 parent prefix 哈希引用
共同前缀，不复制或伪造子 Run 的旧事件。

每个 Control/Treatment 至少重复五次，报告：

- 各指标的均值、范围和逐次结果；
- 失败类型分布；
- 目标失败是否仍然发生；
- Token、工具调用和延迟；
- 是否出现新的失败；
- no-op Control 的自然波动。

## 10. HIST-001 Fault Fixtures

首个案例包含一个正常条件和三个可确定验证的种植故障：

### 10.1 `future_leakage`

Evidence Pack 中混入截止点之后的材料。期望责任层为 `evidence_data` 或
`context_policy`，硬性目标是未来证据不能进入有效上下文。

### 10.2 `logistics_context_omission`

关键后勤证据存在于冻结包中，但 Context Policy 未选入。期望系统区分“模型没有理解”
和“模型根本没看到”。

### 10.3 `ambiguous_source_contract`

工具返回材料时缺少日期精度或决策者可获得性说明，导致 Agent 把后来整理的材料当成
当时已知信息。期望责任层为 `tool_schema`。

正常条件作为 no-op Control，检查 proposer 是否会为不存在的故障制造无意义修复。

## 11. Workflow Arms

首版声明式 Workflow：

```text
direct
evidence_ledger
evidence_ledger_counterargument
evidence_ledger_counterargument_scenarios
```

Workflow 是实验变量，不预设阶段越多越好。每个额外阶段都必须用报告质量、稳定性和
成本证明其价值。

## 12. Grader 体系

### 12.1 硬性 Graders

- `temporal_integrity`：未来证据使用次数必须为零；
- `citation_validity`：引用必须存在于有效 Evidence Bundle；
- `intervention_compliance`：不得偷偷恢复俄国远征；
- `claim_type_validity`：事实、假设、推断和场景类型合法；
- `artifact_integrity`：Trace、Checkpoint、Config 和 Branch 哈希一致。

### 12.2 研究质量 Graders

- `evidence_faithfulness`：关键判断是否被证据支持；
- `evidence_coverage`：是否覆盖主要军事、外交、经济和后勤约束；
- `causal_coherence`：中间因果步骤是否完整；
- `alternative_quality`：替代战略是否真实不同且可执行；
- `constraint_preservation`：是否避免无依据地改变其他历史条件；
- `counterargument_quality`：是否检验选定战略的失败方式；
- `uncertainty_calibration`：是否区分可能、条件可能和未知；
- `report_completeness`：报告结构是否完整。

开放质量 Grader 输出分项分数、证据和理由，不只输出总分。v0.2 的模型 Grader 必须
隐藏被评 Workflow 和 Repair 名称，并使用少量人工样例校准。

### 12.3 Trajectory、Reliability 与 Cost

- `trajectory_validity`；
- `first_critical_failure_accuracy`，仅用于有人工标签的 Fault Fixtures；
- `recovery_success`；
- `repeat_pass_rate`；
- `failure_recurrence_rate`；
- `model_calls`、`tool_calls`、`input_tokens`、`output_tokens` 和 `latency`。

## 13. 实验设计

### 13.1 首批研究问题

**H1：** PIT Guard 能否在所有重复运行中阻止未来证据进入有效上下文。

**H2：** Evidence Ledger 相对 Direct Workflow 是否减少 Unsupported Causal Claims。

**H3：** Counterargument 阶段是否改善约束覆盖，同时其成本是否可接受。

**H4：** 对 `logistics_context_omission` 的诊断能否正确归因到 Context Policy，而不是
Runtime Model。

**H5（Stretch）：** Repair proposer 读取原始 trace 时，是否比只读取总分或摘要提出
更有证据支持的修改。

### 13.2 受控变量

每个实验固定：

- Task 与 Evidence Bundle 哈希；
- Runtime Model 与版本；
- Prompt/Tool/Workflow 中除目标变量外的其他配置；
- Repeat 编号；
- Grader 版本；
- 代码 Commit。

Control 与 Treatment 只能有一个声明的配置差异。运行器在实验前比较规范化配置并拒绝
多变量漂移。

### 13.3 结果解释

不因为一次最高分就宣称改善。一个 Repair 可被提升的最低条件是：

- 硬性 Grader 无退化；
- 目标失败在至少四个配对 Repeat 中改善，或结果被标记为 `inconclusive`；
- 所有逐次结果和成本公开；
- Fault Fixture 的责任层与人工标签一致；
- 未通过条件时保留失败候选与原因，而不是删除。

一个案例及其故障变体只构成 Pilot Eval Suite，不承担跨领域泛化结论。

## 14. 运行产物

### 14.1 文件系统就是公共 API

TraceLane 的数据不能依赖“人知道某个文件是什么意思”。所有可持久化对象都必须：

- 有 JSON Schema；
- 有 `schema_id` 和 `schema_version`；
- 有稳定 ID；
- 有规范化哈希；
- 有父子关系或来源引用；
- 能被 CLI 和第三方程序读取；
- 能在没有 Markdown 报告的情况下完整重建实验。

JSON/JSONL 是权威数据源。Markdown、HTML 和图表是从结构化数据生成的阅读视图，不得
包含结构化产物中不存在的实验事实。

Schema 放在：

```text
src/tracelane/schemas/v2/
├── object-envelope.schema.json
├── artifact-ref.schema.json
├── suite-manifest.schema.json
├── case.schema.json
├── evidence-record.schema.json
├── evidence-manifest.schema.json
├── fault-fixture.schema.json
├── workflow-config.schema.json
├── harness-config.schema.json
├── runtime-config.schema.json
├── grader-set.schema.json
├── trace-event.schema.json
├── checkpoint.schema.json
├── constraint-violation.schema.json
├── diagnosis.schema.json
├── change-manifest.schema.json
├── run-manifest.schema.json
├── experiment-manifest.schema.json
├── comparison.schema.json
├── research-report.schema.json
├── grade-report.schema.json
├── trajectory-export.schema.json
├── preference-export.schema.json
├── reward-event.schema.json
└── migration-manifest.schema.json
```

JSON Schema Draft 2020-12 是磁盘和交换格式的权威契约；Python 内部继续使用 frozen
dataclass 表达强类型对象。每个 dataclass 都必须有序列化契约测试，确保生成的 JSON
能通过对应 Schema，避免“Python 类型是一套、磁盘格式是另一套”。Schema 的 Major
版本进入路径，Minor/Patch 版本进入对象；破坏兼容性的字段修改必须进入新 Major 路径。

每个顶层 JSON 对象使用类似的信封：

```json
{
  "schema_id": "tracelane://schemas/evidence-record/v2",
  "schema_version": "2.0.0",
  "object_id": "hist-001-ev-0001",
  "created_at": "2026-07-24T00:00:00Z",
  "content_sha256": "<64-char lowercase sha256>"
}
```

### 14.2 ArtifactRef

文件之间不使用无法校验的裸路径字符串，而使用统一引用：

```json
{
  "kind": "evidence_record",
  "uri": "tracelane://fixtures/v0.2/history/hist-001/evidence/records/hist-001-ev-0001.json",
  "media_type": "application/json",
  "sha256": "<64-char lowercase sha256>",
  "size_bytes": 2048,
  "schema_id": "tracelane://schemas/evidence-record/v2"
}
```

规则：

- `uri` 使用 `/`，不写操作系统绝对路径；
- 解析后的路径必须位于声明的 Artifact Root 内；
- 禁止 `..`、符号链接逃逸和未声明外部文件；
- 读取时校验大小、哈希和 Schema；
- 大文本和二进制内容使用 Content-addressed Blob，不重复复制。

### 14.3 HIST-001 仓库数据布局

```text
fixtures/v0.2/
├── manifest.json
├── splits/
│   ├── development.json
│   └── heldout.json
└── history/hist-001/
    ├── case.json
    ├── suite-entry.json
    ├── evidence/
    │   ├── manifest.json
    │   ├── records/
    │   │   ├── hist-001-ev-0001.json
    │   │   ├── hist-001-ev-0002.json
    │   │   └── ...
    │   └── blobs/
    │       └── sha256/<first-two>/<full-sha256>.txt
    ├── faults/
    │   ├── future-leakage.json
    │   ├── logistics-context-omission.json
    │   └── ambiguous-source-contract.json
    ├── rubrics/
    │   ├── historical-research-v1.json
    │   └── diagnosis-v1.json
    └── workflows/
        ├── direct.json
        ├── evidence-ledger.json
        ├── evidence-ledger-counterargument.json
        └── evidence-ledger-counterargument-scenarios.json
```

`fixtures/v0.2/manifest.json` 明确列出 Suite、Case、Fault、Rubric 与 Split 的引用，
不允许测试运行器靠扫描目录猜测数据集。用于设计和调试的 Fault Fixture 放入
`development`；最终报告使用预先登记、调参阶段不读取的 `heldout`。开源仓库不能把
公开文件伪装成秘密测试集，因此这里的 heldout 是实验流程隔离：执行评测前锁定 Commit、
配置和假设，Runner 不把标签传给 Agent 或 Repair Proposer。即使 v0.2 只有一个案例，
也先建立这一边界，避免一边看结果一边调 Harness。Split 列出的是稳定的
`scenario_id`（Case + 可选 Fault），而不只是 Case ID；同一历史案例的干净条件、
开发故障和 heldout 故障可以被分别登记。

`evidence/manifest.json` 是冻结证据包的入口，记录：

```text
case_id
cutoff_at
record_refs[]
rejected_future_refs[]
source_licenses[]
transformations[]
bundle_sha256
```

单条 Evidence Record 不只保存文本，还保存可程序化 provenance：

```json
{
  "schema_id": "tracelane://schemas/evidence-record/v2",
  "schema_version": "2.0.0",
  "evidence_id": "hist-001-ev-0001",
  "document_date": "1812-05-01",
  "date_precision": "month",
  "available_at": "1812-05-31T23:59:59Z",
  "known_by_cutoff": "plausibly_known",
  "source_type": "primary",
  "source_title": "<title>",
  "source_locator": "<stable URL or archival locator>",
  "license": "Public Domain",
  "excerpt_kind": "translated",
  "content_ref": {
    "kind": "evidence_blob",
    "uri": "tracelane://fixtures/v0.2/history/hist-001/evidence/blobs/sha256/ab/<sha256>.txt",
    "media_type": "text/plain",
    "sha256": "<sha256>",
    "size_bytes": 1024
  },
  "fact_ids": ["hist-001-fact-0001"],
  "provenance_sha256": "<sha256>"
}
```

翻译、节选和转述都记录 transformation lineage，保留源 Blob、目标 Blob、转换类型、
工具/人工责任人和两个哈希。不能只留下最终中文文本。

### 14.4 Trace 的程序化标识

v0.2 在现有递增 `sequence` 之外增加：

```text
event_id
trace_id
span_id
parent_span_id
correlation_id
causation_id
```

- `trace_id` 关联一次 Run；
- `span_id` 表示一次 Stage、Model Call、Tool Call、Grader 或 Replay；
- `parent_span_id` 表示调用树；
- `correlation_id` 关联 Request/Response、Repair/Replay 等成对对象；
- `causation_id` 指向直接触发当前事件的 `event_id`；
- `sequence` 保留权威文件顺序。

字段语义尽量与
[OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
兼容，至少覆盖 Agent、Workflow、Model Request/Response、Tool Call、Token Usage 和
Evaluation。TraceLane 的领域事件继续保留，通过 Exporter 映射为 OpenTelemetry spans，
而不是让 OpenTelemetry SDK 成为核心存储依赖。每个 Run Manifest 固定
`semantic_convention_version` 与 exporter 版本，防止外部标准升级后同一份 trace
产生不同映射。

单条 Trace Event 示例：

```json
{
  "schema_id": "tracelane://schemas/trace-event/v2",
  "schema_version": "2.0.0",
  "event_id": "evt_<content-id>",
  "sequence": 17,
  "event_type": "tool.observed",
  "trace_id": "run_<run-id>",
  "span_id": "span_<span-id>",
  "parent_span_id": "span_<parent-id>",
  "correlation_id": "toolcall_<id>",
  "causation_id": "evt_<tool-called-event>",
  "run_id": "<run-id>",
  "stage": "evidence_ledger",
  "recorded_at": "2026-07-24T00:00:00Z",
  "attributes": {
    "gen_ai.tool.name": "retrieve_evidence",
    "gen_ai.tool.call.id": "toolcall_<id>"
  },
  "payload_ref": {
    "kind": "tool_result",
    "uri": "tracelane://artifacts/blobs/sha256/ab/<sha256>.json",
    "media_type": "application/json",
    "sha256": "<sha256>",
    "size_bytes": 4096
  }
}
```

小型 Payload 可以内联；达到配置阈值后必须写入 Blob Store，并通过 `payload_ref` 引用，
避免 `events.jsonl` 失控增长。

Trace 保存可观察行为：模型输入、公开输出、工具调用、工具结果、状态转换、错误、
成本和 grader 证据；不要求、也不保存供应商未公开的隐藏 Chain-of-Thought。需要解释
行为时保存短的 `decision_summary`，并明确它是模型输出字段而非真实内部思维。

每个 Run 固定 `redaction_policy_id`。写入前对 API Key、Cookie、Authorization Header、
本机绝对路径、邮箱和电话号码等敏感值做结构化脱敏；事件记录
`payload_classification` 和 `redaction_applied`。原始敏感 Payload 默认不落盘，公开
导出必须再次通过 Secret/PII Scanner。证据包只保存许可证允许再发布的原文；其余来源
保存定位信息、必要短摘录、事实转述和 transformation lineage。

### 14.5 Run 与 Experiment 布局

```text
artifacts/
├── blobs/
│   └── sha256/<first-two>/<full-sha256>.<ext>
├── acquisition/<session-id>/
│   ├── manifest.json
│   ├── search-results.jsonl
│   ├── fetches.jsonl
│   └── candidates/<candidate-id>.json
├── imports/v1/<import-id>/
│   ├── manifest.json
│   └── payload/...
├── runs/<run-id>/
│   ├── manifest.json
│   ├── input/
│   │   ├── case.json
│   │   ├── evidence-manifest.json
│   │   ├── harness-config.json
│   │   ├── runtime-config.json
│   │   ├── grader-set.json
│   │   └── lineage.json
│   ├── checksums.json
│   ├── trace/events.jsonl
│   ├── checkpoints/
│   │   └── <sequence>-<stage>.json
│   ├── diagnosis/
│   │   ├── violations.jsonl
│   │   └── diagnosis.json
│   ├── output/research-report.json
│   ├── output/research-report.md
│   └── output/grades.json
└── experiments/<experiment-id>/
    ├── manifest.json
    ├── change-manifest.json
    ├── arms/
    │   ├── control/repeats/0001/run-ref.json
    │   └── treatment/repeats/0001/run-ref.json
    ├── comparison.json
    ├── harness-report.md
    └── exports/
        ├── trajectories.jsonl
        ├── preferences.jsonl
        └── reward-events.jsonl
```

Run 全局只保存一次；Experiment Arm 使用 `run-ref.json` 引用 Run，避免复制。同一
Experiment 的 Control/Treatment/Repeat 由 Manifest 明确列出，不能依赖目录遍历猜测
实验结构。`checksums.json` 列出 Run 内全部权威文件的相对 URI、大小和 SHA-256；
`manifest.json` 保存该清单的根哈希，因此复制、压缩或上传后仍能检查完整性。为避免
循环哈希，`checksums.json` 不包含自身和 `manifest.json`；Manifest 自身通过
`content_sha256` 校验，并保存 `checksums.json` 的哈希。

`RunManifest` 至少保存：

```text
run_id
lifecycle_status
started_at
completed_at
case_ref
evidence_manifest_ref
harness_config_ref
runtime_config_ref
grader_set_ref
code_revision
environment_fingerprint
semantic_convention_version
redaction_policy_id
trace_ref
checkpoint_refs[]
diagnosis_ref
output_refs[]
grade_report_ref
parent_run_id
branch_id
checksums_ref
```

`ExperimentManifest` 至少保存：

```text
experiment_id
research_question
preregistered_hypothesis
suite_ref
independent_variable
control_arm
treatment_arms[]
repeat_plan
pairing_strategy
primary_metrics[]
guardrail_metrics[]
stopping_rule
budget
code_revision
run_refs[]
change_manifest_ref
comparison_ref
lifecycle_status
```

`GradeReport` 中每个 grader result 保存 `grader_id`、`grader_version`、`metric_id`、
`value`、`unit`、`passed`、`threshold`、`evidence_refs[]`、`reason_code` 和
`explanation`。`Comparison` 保存每个 Arm 的逐次值、聚合方法、离散程度、配对差值、
硬性违规、成本差值和最终决策；总分只是派生字段，不能替代分项结果。

### 14.6 ID、指纹与谱系

- `case_id` 是稳定语义 ID，例如 `hist-001`；
- `evidence_id` 是案例内稳定 ID；
- `run_id` 来自 Case、Evidence Bundle、Harness、Runtime、Grader、Repeat 和代码版本
  的规范化执行指纹；
- `experiment_id` 来自研究问题、Suite、Arms、Repeats 和 Grader Set；
- `branch_id` 来自 Parent Checkpoint、Change Manifest 和 Repeat；
- `diagnosis_id` 来自 Run、Critical Sequence 和 Diagnoser Version。

所有 ID 使用小写 ASCII、固定前缀和可校验格式；所有时间使用 UTC RFC 3339。对象的
`content_sha256` 是删除 `content_sha256` 字段后，对 Canonical JSON Bytes 计算得到的
SHA-256。文件名只是存储位置，身份由对象 ID 与内容哈希共同确认。

所有派生对象保存 `parents[]` 与 `derived_from[]`。给定 Experiment，程序可以反向找到：

```text
Comparison
  → Arm
  → Run
  → Branch + Change
  → Checkpoint
  → Trace Event
  → Evidence Record
  → Source Blob
```

### 14.7 写入、校验与升级规则

- JSON 使用现有 Canonical Serialization，拒绝 NaN、Infinity 和未知字段；
- JSONL 每行是一个完整 JSON 对象并独立通过对应 Schema，只追加、不原地修改历史事件；
- 修正历史数据时创建新对象并使用 `supersedes`，不静默覆盖；
- 文件继续采用临时文件、`fsync` 和原子替换；
- Manifest 在发布前校验全部 Ref、Schema、哈希和路径包含关系；
- 生成的 `catalog.json` 或 SQLite Index 只能作为可重建索引，不能成为唯一事实源；
- v1 Artifact 继续由现有 v1 Reader 读取；导入器将其逐字节复制到
  `artifacts/imports/v1/<import-id>/payload/`，生成带来源、大小和哈希的 Migration
  Manifest，不修改原始 v1 产物，也不把旧答案伪装成原生 v2 Research Report；
- Markdown 报告中的数字和引用必须能回链到结构化对象 ID。

提供统一的 `validate artifact|run|experiment|suite` 校验入口。校验失败时返回稳定的
错误代码、对象 URI 和 JSON Pointer，方便 CI、编辑器和其他程序消费，而不只打印
自然语言异常。

原始 trace、诊断证据、被拒绝的 Repair 和 no-op 结果都保留，形成可供后续 proposer
程序化查询的完整实验历史。

## 15. CLI 边界

保留 v0.1 命令并逐步增加：

```text
tracelane eval
tracelane ablate
tracelane inspect
tracelane acquire
tracelane diagnose
tracelane replay
tracelane compare
tracelane export
tracelane migrate
```

第一条端到端演示命令必须能够从 HIST-001 配置生成：

1. Baseline 研究报告；
2. 失败诊断；
3. Control/Treatment 五次重复；
4. Harness 对照报告；
5. Training-ready JSONL。

## 16. Research Evidence and Design Rationale

| TraceLane 本地缺口 | 参考机制 | v0.2 采用方式 | 验证 |
|---|---|---|---|
| Trace 尚未成为可查询的改进历史 | [Meta-Harness](https://arxiv.org/html/2603.28052v1) | 保存完整 Config、Trace、Score、Change 和父实验 | Stretch：score/summary/raw-trace proposer ablation |
| Grader 不能定位第一处关键错误 | [AgentRx](https://www.microsoft.com/en-us/research/blog/systematic-debugging-for-ai-agents-introducing-the-agentrx-framework/) | 约束日志、固定失败分类、First Critical Failure | Fault Fixture 人工标签 |
| 修改缺少事前预测与回滚条件 | [Agentic Harness Engineering](https://arxiv.org/abs/2604.25850) | Change Manifest 与 Promote/Reject | 对比预测和观测变化 |
| 可确定禁止项仍依赖 Prompt | [AutoHarness](https://arxiv.org/abs/2603.03329) | PIT、引用、结构和 Intervention 硬约束 | Fault Injection 违规率 |
| Workflow 写死，难做公平消融 | [AFlow](https://proceedings.iclr.cc/paper_files/paper/2025/hash/5492ecbce4439401798dcd2c90be94cd-Abstract-Conference.html) | 四个声明式 Workflow Arms | 单变量重复实验 |
| 容易误以为存在万能 Harness | [Adaptive Auto-Harness](https://arxiv.org/abs/2606.01770) | v0.2 保留 Task Family，不提前实现路由 | 案例扩展后再验证 |
| 人工问题没有沉淀为 Eval | [OpenAI Tax AI](https://openai.com/index/building-self-improving-tax-agents-with-codex/) | Finding → Eval Case → Regression | 修复后的复发率 |
| Trace 不能进入训练接口 | [Agent Lightning](https://microsoft.github.io/agent-lightning/latest/) | 只实现标准化 Exporter | Schema 与完整性测试 |
| Checkpoint 只用于恢复 | [Causal Agent Replay](https://arxiv.org/abs/2606.08275) 与 [HarnessFix](https://arxiv.org/abs/2606.06324) | 单变量分叉和 Suffix Replay，不宣称严格因果 | 种植故障与重复结果分布 |
| Proposer 可能修不存在的问题 | [Phantom Guardrails](https://arxiv.org/abs/2607.13083) | 正常条件与 no-op Control | 无真实故障时应选择不修改 |

这些新近工作是设计依据和待复现机制，不是 TraceLane 已经获得相同论文结果的证据。

## 17. 两周范围

### Week 1：形成可运行的纵向骨架

1. HIST-001 Task/Evidence/Answer 契约；
2. Evidence Acquisition 接口、受限网络工具与冻结入口；
3. Trace Event 类型与 Constraint Log；
4. 历史研究报告结构；
5. 硬性 Graders 与 Fault Fixtures；
6. First Critical Failure 诊断。

### Week 2：闭合实验

1. Change Manifest；
2. Checkpoint Branch 与 Suffix Replay；
3. 五次重复和 Control/Treatment Comparison；
4. Harness 实验报告；
5. Training-ready Exporter；
6. README Demo、测试、发布整理。

受控 Meta proposer 为 Stretch。若核心闭环尚未通过验收，不占用核心工期。

## 18. 完成标准

v0.2 设计完成后的实现必须满足：

- HIST-001 能在离线 Stub Runtime 下确定性演示完整链路；
- 至少一个真实模型 Runtime 能运行同一契约；
- 三个 Fault Fixtures 都能产生可审计诊断；
- Future Evidence 在所有有效运行中被硬性阻止；
- 一个白名单 Repair 能从 checkpoint 分叉并完成五次 Control/Treatment；
- Comparison 同时报告质量、稳定性、失败类型和成本；
- 正常 no-op 条件不会被自动制造成必须修复的问题；
- 所有实验产物可由 Config、Evidence、Runtime、Grader 和 Commit 哈希追溯；
- 导出的 JSONL 通过 Schema 和敏感信息检查；
- v0.1 测试与契约保持兼容。
