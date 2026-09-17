# 可观测性与脱敏合同

> Proposed。先读 [横切合同导读](../phase-1-cross-cutting-contracts.md)。

记录阶段、结果与延迟，避免把学习内容变成诊断数据。

## 精确语义与后续证据

下面保留原合同的任务映射、Must/Should/Later、conformance、验收和未决假设。它们约束后续实现，不能从文档存在推断产品通过。

<!-- retained-contract:start -->
**对应任务：`LF-TSK-OBS-0001`**

可观测性必须回答一次 caption 为什么得到、没有得到或拒绝了某个 hint，也必须在不保存用户学习内容的前提下回答性能、成本和可靠性问题。

### 6.1 Correlation model

**Must**

- API 为每个外部用例建立服务端可信的 correlation identity。可接受客户端 correlation 作为上游 hint，但必须限制格式/长度，且不能作为授权、幂等或 owner identity。
- durable work 拥有独立且稳定的 work identity；每次执行拥有 attempt identity。API trace 与 worker trace 通过 durable work link 关联，而不是假装为同一个同步 span。
- annotation result/revision、delivery attempt、`HintDisplayed`/`HintClicked` event 与 profile evidence 使用各自 Domain identity，并通过允许的引用链关联；correlation identity 不能替代这些业务 identity。
- 一次重试沿用原 work identity、产生新 attempt identity；重复 delivery 或 Learning event 可以从 telemetry 识别，但不能产生重复业务效果。
- correlation 传播越过 Extension、API、durable handoff、worker、Enrichment result、Delivery 和 Learning 时，只携带最小非敏感标识与受控版本；不得传播 caption 文本、term、Profile 内容或 token。

**Should**

- Extension 本地 English paint、L1 lookup、network request 和 stale discard 使用同一客户端 journey identity；服务端另生成可信 correlation 并记录二者的受控关联。
- profile projection/replay 使用 batch/replay identity，并把来源 evidence 数量作为 metric，而不把全部 event identity 放入日志。

**Later**

- Phase 3 固定 transport propagation；Phase 5 固定 browser performance 采集；Phase 7 固定跨环境 trace backend 与采样配置。

### 6.2 Structured log contract

每个结构化事件使用受控事件类型，而不是自由文本拼接。事件至少表达：发生阶段、outcome、标准 reason、受控版本、耗时/数量（如适用）和非敏感 correlation。具体字段名与日志框架留给实现阶段。

**Must**

- 记录生命周期事实：API 接收/拒绝、fast lane 完成、durable commit 成功/失败、worker claim/attempt/completion、Semantic outcome、annotation generated/delivered/stale-discard、Learning intake/projection。
- outcome 只使用合同定义的稳定类别；Provider 原始错误消息、响应体和 stack 中的输入数据不能直接进入日志。
- 日志不包含原始 caption/transcript、term/phrase、中文 hint、Vocabulary Profile、观看/点击明细、session/token/secret、Provider 输入输出或可逆内容摘要。
- 用户、设备、content、caption、job 等高基数业务 identity 默认不进入常规日志；确需诊断引用时使用短期、受控、不可逆且不复用作授权的诊断标识。
- 同一个业务事实只由 owner 发出权威 lifecycle event；adapter 可记录 transport 状态，但不得冒充 `displayed`、`learned` 或 profile update。

**Should**

- 正常高频路径采用聚合指标和采样日志；拒绝、contract mismatch、数据删除失败等安全/正确性事件保留可操作证据。
- log schema/version 受 contract test 管理；未知属性默认不输出，而不是默认放行。

### 6.3 Metrics contract

**Must**

- 使用 counter 表达 outcome/reason 数量，histogram 表达阶段延迟、queue wait、result age 和 projection lag，gauge 只表达可瞬时采样的 backlog/资源状态。
- 至少覆盖 English paint、Extension L1、API fast lane、durable handoff、worker queue/attempt、Semantic outcome/provider latency、各 cache tier、delivery/stale discard、Learning intake/projection。
- labels 仅使用有限 allowlist，如环境、runtime、capability、cache tier、outcome、reason、受控 provider/model family 和 contract version。
- 用户、设备、content/video、caption、term、correlation、job/attempt、任意错误文本和任意 URL 不得成为 metric label。
- `PASS`/健康不能由“进程存在”或“调用退出 0”推断；required journey/check 未运行必须显式呈现未验证或 `BLOCKED`。

**Should**

- cache hit 同时观察 stale reject、version reject 与 fallback，避免高 hit rate 掩盖错误复用。
- Provider 指标分开 timeout、refusal、malformed、rate limited、budget exhausted 和 contract mismatch，并观察重试放大率与单位成功成本。

**Later**

- Phase 3/4 用 `LF-TSK-PRF-0001` 的预算确定 SLI/SLO 阈值；Phase 7 再固定 dashboard 与 alert。

### 6.4 Trace contract

**Must**

- API 同步 span 在 durable handoff commit 或明确 no-pending 响应处结束，不等待 worker。
- Worker 从 durable work 建立新 trace，并以 link 关联提交方；claim、queue wait、Provider attempt、validation、Enrichment persistence 与 delivery publish 具有可区分阶段。
- span attribute 遵守与日志相同的 allowlist/redaction；trace baggage 不携带内容、个人状态、token 或 Provider payload。
- sampling 不影响正确性。未采样 trace 仍通过 metrics 和 durable business state 保留必要的 outcome。
- late/cancel/stale result 保持可观察，但不能为了 trace 完整而继续展示或写入 Learning 暴露。

**Should**

- 错误采样可提高，但先 redaction 后采样；采样策略不能把敏感 payload 当调试附件。
- 对 fan-out Provider attempt 使用 sibling spans/links，使总预算与获胜结果可解释。

[Lifecycle guarantees](../phase-1-lifecycle-guarantees.md) 进一步区分 work/attempt/fencing、取消后的提交权限与 delivery ACK/实际显示事实；trace 的成功 span 不得代替这些 owner 的 durable 状态。Learning canonical intent order、投影 pending 及删除 generation 也只传播最小非敏感关联，不扩展为内容日志。

### 6.5 Redaction 与保留

| 分类 | 示例 | Telemetry policy |
|---|---|---|
| Secret | auth/session token、Provider key、cookie | 永不采集；检测到即删除并触发安全事件。 |
| User learning data | Vocabulary Profile、行为明细、显式认识/不认识 | 不进入通用 logs/traces；只输出聚合 outcome。 |
| Content data | caption、transcript、term、hint、URL/标题 | 不进入通用 logs/metrics/traces；测试使用合成 fixture。 |
| Stable personal identifier | user/device/account identity | 不作为 label；诊断用途只用受控 pseudonymous reference 和有限保留。 |
| Operational metadata | runtime、capability、outcome、reason、受控版本、duration | allowlist 后可采集；仍受 retention policy 管理。 |

**Must**

- redaction 在事件离开进程前执行；不能依赖下游日志平台补救。
- exception、Provider SDK metadata、HTTP metadata 和 cache key 经过同一 allowlist，未知值默认移除。
- telemetry retention 与产品事实 retention 分离；删除 telemetry 不改变 Domain 状态，删除 Domain 数据也不能假设 TTL 会自动清除所有 telemetry。

**Later**

- Phase 7 决定每类 telemetry 的保留时长、访问审计与紧急诊断流程。

### 6.6 Acceptance evidence

`LF-TSK-OBS-0001` 的 `PASS` 证据至少包括：

1. 一条 caption journey correlation fixture，从 Extension English paint 经 API、durable work、worker、Semantic、Enrichment、Delivery 到 Learning；证明异步 trace 使用 link 且 identity 不混用。
2. structured event catalog，逐事件标明 owner、outcome/reason allowlist、必需版本和禁止内容。
3. metric cardinality review，证明所有 label 来自有限集合，用户/content/caption/term/correlation/job 不进入 labels。
4. redaction fixtures，向 caption、URL、token、Provider error、Profile 和 stack 注入 canary，证明 logs/metrics/traces 均不出现 canary。
5. failure journey，证明 timeout、malformed、refusal、enqueue failure、stale discard 和 projection lag 可分别定位，同时英文字幕不受影响。

### 6.7 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| OBS-A1 | 异步 trace link 加稳定 work identity 足以诊断大多数跨 runtime 问题。 | Phase 3/4 failure injection。 |
| OBS-A2 | 内容零采集的通用 telemetry 仍能定位主要质量问题。 | Phase 4 运营演练；必要时设计受控、显式授权的独立诊断流程。 |
| OBS-A3 | provider/model family 的受控枚举不会造成不可接受的 label cardinality。 | Phase 4 adapter inventory。 |
<!-- retained-contract:end -->
