# Semantic 结果合同

> Proposed。先读 [横切合同导读](../phase-1-cross-cutting-contracts.md)。

模型输出先成为可校验的语义证据，Enrichment 再决定是否展示。

## 精确语义与后续证据

下面保留原合同的任务映射、Must/Should/Later、conformance、验收和未决假设。它们约束后续实现，不能从文档存在推断产品通过。

<!-- retained-contract:start -->
**对应任务：`LF-TSK-SEM-0002`**

本合同细化 ADR-007。Semantic 只提供语义证据；Enrichment 仍独占是否展示、展示多少以及 annotation 最终版本的决定权。

### 3.1 Provider-neutral 结果包络

每次 Semantic capability 调用都产生一个且仅一个标准 outcome。包络在概念上包含以下语义元素；名称不承诺后续 wire 或 storage 字段名：

| 语义元素 | Must 约束 | 目的 |
|---|---|---|
| Capability | 标明本次任务是消歧、语境表达、短语抽取或句子解释中的哪一类；不同 capability 的 payload 不得互换。 | 防止 Provider 的通用文本响应渗入业务。 |
| Input binding | 关联规范化 term/phrase、Content Context 指纹和目标语言；不得只返回无法定位到输入的自由文本。 | 支持去重、缓存和 stale result 拒绝。 |
| Outcome | 明确区分 `success`、`abstained` 与 `failure`。 | 空结果不再同时表示“不确定”“拒绝”“超时”和“格式错误”。 |
| Semantic evidence | 仅在 `success` 时存在，表达 capability 所需的语境义、目标语言短表达或引用 span；不得携带“必须展示”一类 UI 指令。 | 保持 Models 与 Rules 分离。 |
| Confidence | 使用标准置信等级、来源和校准版本；Provider 原生分数不能未经校准直接比较。 | 允许 Enrichment 按一致策略消费证据。 |
| Provenance | 记录受控的 provider/model family、Semantic contract version、routing policy version 与完成时间。 | 支持解释、复算和有界 cache reuse。 |
| Failure classification | `failure` 时只给标准原因与可重试提示，不把 Provider 原始响应当成 Domain 错误。 | 隔离 Provider SDK 与不稳定错误格式。 |

`success` 必须通过 capability-specific 结构、语言、引用范围和大小校验。`abstained` 表示调用按合同完成，但系统没有足够证据给出可靠语义；它不是失败，也不能伪装成高置信空文本。`failure` 不得携带可展示语义 payload。

### 3.2 置信语义

**Must**

- 标准置信只描述“语义证据与当前有界上下文相符的可信程度”，不描述用户是否认识该词，也不直接决定 annotation 是否显示。
- 置信至少支持 `high`、`medium`、`low`、`unknown` 四种可比较等级；任何数值仅在同一校准版本内有意义。
- 每个非 `unknown` 结果必须说明置信来源：Provider 原生信号、确定性校验、多个候选一致性或校准器输出中的受控组合。
- 上下文缺失、引用 span 无法绑定或 Provider 不提供可靠置信时，结果不得默认提升为 `high`。
- Enrichment 必须能够拒绝低置信证据、回退到 Lexicon/fast result，或保持无提示；不得为了完成 slow work 强制生成中文。

**Should**

- 为不同 capability 分开校准；消歧的置信不能直接冒充翻译自然度或短语边界置信。
- 保存足够的校准版本和 outcome 聚合，使后续能比较“展示后点击/纠正”等效果，但不得在 telemetry 中保存原始字幕或 Vocabulary Profile。

**Later**

- Phase 4 依据代表性语料确定数值区间、校准算法和各 capability 的最低可用阈值。
- Phase 6 只可用脱敏、可追溯的 Learning evidence 调整阈值，不能让 Provider 自评替代用户行为事实。

### 3.3 Malformed、timeout、refusal 与其他失败

| 标准结果 | 判定 | 是否可作为语义成功缓存 | 调用方语义 |
|---|---|---|---|
| `malformed` | 响应无法通过结构、语言、引用边界、大小或 contract version 校验。 | 否 | 本 attempt 失败；丢弃原始 payload。是否重路由由 Semantic routing policy 决定。 |
| `timeout` | 调用在调用方 deadline 内没有得到已校验结果，包括连接、生成或读取超时。 | 否 | 本 attempt 终止；晚到响应不得附着到已完成 attempt。Durable work 可按独立重试策略继续。 |
| `refusal` | Provider 明确拒绝完成 capability，或安全策略使其不返回所需语义。 | 否 | 不是空成功；可由路由策略选择其他允许的 Provider，也可安全降级为无提示。 |
| `rate_limited` | Provider 表示容量/配额暂不可用。 | 否 | 作为可观测的暂时失败；不得阻塞 English first path。 |
| `unavailable` | Provider、网络或 adapter 暂不可用且未得到业务结果。 | 否 | 按路由、重试和熔断策略处理。 |
| `budget_exhausted` | 当前任务的成本、attempt 或 deadline 预算已经耗尽。 | 否 | 停止该工作单元的继续消耗，保留 fast/no-hint 结果。 |
| `contract_mismatch` | Provider adapter 不支持请求 capability/version，或返回版本无法归一。 | 否 | 视为配置/发布兼容问题，不做内容重试。 |
| `cancelled` | caption 已 stale、请求被上游取消或工作不再有用户价值。 | 否 | 停止结果发布；不把取消计为 Provider 内容失败。 |

**Must**

- deadline 从 Application 预算向 Semantic port 传入；Provider adapter 只能缩短，不能延长。排队等待、Provider 执行和结果校验都必须计入可观察阶段。
- attempt-level timeout/refusal/malformed 与 durable job 的最终状态分离。一次 attempt 失败不自动表示 job 永久失败；job 重试也不能把旧 attempt 改写成成功。
- Provider 输出按不可信输入处理。adapter 完成结构归一和约束校验后，Semantic 才能发布标准结果。
- 对同一工作单元的重试、fallback 和 hedge 必须有总预算与去重身份，不能无界放大成本。
- 失败时客户端仍得到英文、有效 fast result 或明确的 no-pending/无增量降级；不得生成整句双语兜底。

**Should**

- malformed 与 contract mismatch 进入独立告警维度，避免被笼统归入 Provider timeout。
- 短期 failure suppression 可以减少对持续故障 Provider 的重复调用，但必须与成功语义缓存分开，并使用短、有限的有效期。

**Later**

- Phase 4 决定每个 capability 的 deadline、fallback 顺序、attempt 上限和 Provider 适配细节。
- Phase 7 用压测和生产 SLO 决定 circuit breaker、bulkhead 与回压参数。

### 3.4 Acceptance evidence

`LF-TSK-SEM-0002` 只有在以下证据可复核时才可判为 `PASS`：

1. 一份 Provider-neutral contract review，证明四类 capability 都通过标准 outcome 表达，且没有 Provider SDK 类型、自由文本响应或展示决定泄漏到 Enrichment。
2. 一组结构化 contract fixtures，至少覆盖有效 success、合法 abstained、低/未知置信、malformed、timeout、refusal、rate limit、contract mismatch 和 late response。
3. 一张状态转换表，证明 attempt、durable job、annotation result 三者的成功/失败不会互相冒充。
4. 一个降级 journey，证明 Semantic 的每类失败都不会延迟或替换英文字幕，且 failure payload 不会成为 annotation。
5. 一份版本/置信评审记录，证明跨 calibration version 的数值不会直接比较，旧结果会因 contract/context/version 不兼容而失效。

### 3.5 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| SEM-A1 | 四类 capability 可共享 outcome 包络，但需要各自 payload invariant。 | Phase 4，Semantic owner 用 fake 与至少两个 Provider adapter 验证。 |
| SEM-A2 | 分级置信足以驱动首版 Enrichment，数值校准不会成为 MVP 前置。 | Phase 4 离线评估；若误提示率无法区分，再引入数值校准。 |
| SEM-A3 | refusal 与 malformed 的替代 Provider 路由收益高于额外成本。 | Phase 4 代表性失败注入与成本测量。 |
<!-- retained-contract:end -->
