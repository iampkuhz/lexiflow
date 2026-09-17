# 核心流程详细合同

> 状态：Proposed；这是详细参考，不是已实现或已验收的声明。

先读 [字幕与学习流程](../caption-and-learning-flows.md)，理解两条链路；本页保留完整步骤、快慢路径保证和一致性要求。

## 如何使用本页

先查所关心的边界，再核对对应后续验证场景。原章节编号保留，方便查阅历史评审引用；总览和专题页提供当前解释与实际工程状态。

<!-- retained-contract:start -->
## 7. YouTube caption 到 annotation 的完整链路


### 7.1 步骤与责任

1. Extension 从 YouTube adapter 获得 caption 和可用播放上下文，并先渲染纯英文。此步骤不访问网络。
2. Extension 以稳定 caption identity 查询 L1 cache。命中时可以先显示仍在有效期且 profile version 兼容的 annotation。
3. Extension 发送当前 caption、可用的前后文引用、播放位置、客户端已知 profile/cache version 和请求 correlation。具体协议留给 Phase 3。
4. API 完成认证、归属、输入大小、语言和重复请求检查，把请求交给 Application。
5. Content 将 YouTube 输入归一成来源无关的 Content Context。上下文缺失、乱序或下一句未知是合法状态。
6. Enrichment 同时查询 Global Lexicon 与用户 Vocabulary Profile snapshot，完成 normalization、token/phrase detection、candidate generation 和 need-hint prediction。
7. 已有高置信 lexical/context cache 的候选形成 fast annotation；已掌握或不值得打断的候选被过滤。Enrichment 同时返回需要 semantic slow lane 的工作意图，这还不等于工作已经入队。
8. 存在 slow work 时，Workflow Application 先提交 durable handoff。只有收到 durable commit 确认后，API 才把 fast result 与 `pending` 一起暴露给客户端；不得用内存 enqueue 或“准备提交”状态提前承诺 pending。
9. durable enqueue 失败时，API 等失败结论明确后返回已有 fast result 与 `no-pending` 降级语义。客户端继续显示英文/fast annotation，不能等待一个不存在的结果。没有 slow work 时，fast result 直接以 complete 语义返回。
10. 已提交的工作由 Worker 按当前句优先、下一句预取次之调度，调用 provider-independent Semantic port。输入使用有界的上一句/当前句/下一句上下文，并记录 provider/model/rule version 的可观察元数据。
11. Semantic 返回语境义、简短目标语言表达与置信信息。Worker 通过 Enrichment 再次执行展示策略，防止模型绕过用户已掌握状态或 annotation 数量限制。
12. Enrichment 持久化最终 annotation result 并填充 cache；durable result 的 owner 仍是 Enrichment，Worker 只是执行者，不能把结果写成 Worker 或 transport 私有状态。
13. `Client Delivery / Sync` 通过 Enrichment 公开 contract 发现并关联投递 durable result。它不要求原始 API 请求仍存活，也不允许 Worker 直接回调原 API 请求对象。Phase 3 在 polling、SSE 或 WebSocket 中选一种传输，不改变 owner 和 durable result 语义。
14. Extension 只在 video、caption identity 和 revision 仍匹配时合并；晚到结果静默丢弃，不覆盖当前字幕。

### 7.2 快慢路径的正确性

- fast path 没有 semantic 结果时可以返回空 annotation；“没有提示”是合法降级，不是错误字幕。
- `pending` 是 durable handoff 已提交的承诺。enqueue 失败必须返回 `no-pending`；不得让客户端对未持久化工作轮询或等待。
- slow result 必须基于与请求关联的 Content Context 和 profile version。若 profile 已变化，服务端可重算展示决策或标记客户端不得复用。
- semantic cache key 必须区分 term/phrase、语境指纹、目标语言和策略/模型版本。具体 key 结构留给 Phase 2/4。
- 语义结果只提供“是什么意思”的证据；最终“是否展示”仍由 Enrichment 规则决定。

### 7.3 Annotation 生命周期语义

`generated`、`delivered`、`displayed` 与 `clicked` 是四个不同事实，不得互相推断：

1. **generated**：Enrichment 已产生可识别的 annotation result；需要后续增量投递的结果以 Enrichment-owned durable record 为准。
2. **delivered**：`Client Delivery / Sync` 已按选定传输合同把特定 result revision 交给客户端。transport 成功不证明它仍适合当前 caption，更不证明用户看见了它。
3. **displayed**：Extension 完成 identity/revision 检查并把 annotation 实际提交到当前可见 overlay 后，才创建 `HintDisplayed` 行为事件。收到 payload、写入 L1 或准备渲染都不能上报 displayed。
4. **clicked**：用户对已显示 annotation 发生交互后创建 `HintClicked`，并引用对应 displayed/result identity。无法建立因果引用的 click 不能被 Learning 当成有效掌握度证据。

`generated` 和 `delivered` 主要用于 Enrichment/Delivery 的生命周期与效果分母；`displayed` 和 `clicked` 是客户端观察到的 Learning 行为。这样可以避免把生成但未送达、送达但已 stale、或进入缓存但未展示的提示误计为用户暴露。

### 7.4 取消、提交资格与投递 ACK

[Lifecycle guarantees](../phase-1-lifecycle-guarantees.md) 补充 pending/running/terminal、attempt fencing、取消与完成竞争，以及 receipt-unknown/received/discarded/displayed 的结果合同。取消不能保证 Provider 停止计算，但必须阻止失效 attempt 提交新的业务效果；caption 切换立即在客户端失效旧 revision。ACK 丢失重投同一 result，不重新生成模型结果，也不等于用户已看见。

## 8. Behavior 到 Learning 再到 Vocabulary Profile 的完整链路


### 8.1 步骤与一致性

1. Extension 把 `WordSeen`、`HintDisplayed`、`HintClicked`、`SentencePaused`、`SentenceReplayed`、`WordMarkedKnown`、`WordMarkedUnknown`、`TranslationExpanded` 等记录为行为事实。`HintDisplayed` 只能在 annotation 实际提交到可见 overlay 后产生，`HintClicked` 必须引用已显示的 result；generated/delivered 不得替代这两个客户端事实。事件带稳定客户端 identity，因此离线补传和重试不会重复计数。
2. API 验证用户/设备归属、事件类型、内容引用和基本因果关系。客户端不能提交最终 familiarity 数值。
3. Learning 先将事件 durable commit。PostgreSQL 不可用时不能声称事件已保存；客户端可以稍后重试。
4. 显式 `MarkedKnown/MarkedUnknown` 表达强用户意图。事件 durable commit 后，同一 use case 同步产生 evidence 并尝试更新 profile。只有投影成功，单次最终响应才携带 accepted 与新 profile version；投影失败则同一响应明确表示事件已保存、profile projection pending，由 worker 从 durable event 修复。禁止先返回 durable ACK，再返回第二个 version ACK。
5. 暂停、重播、展示、点击等隐式信号批量异步归约。Learning 使用带版本的、可解释 scoring rule 生成正/负/中性 evidence。
6. Vocabulary Profile 幂等应用 evidence，更新当前投影并保留事件来源/规则版本的可追踪关系。重复 job 不会重复增加 exposure 或 familiarity。
7. profile 更新后，服务端先使 Redis 中的旧 profile version 失效；`Client Delivery / Sync` 只向客户端传播当前 version，Extension 据此淘汰不兼容的 L1 条目。Vocabulary 不直接操作浏览器缓存。跨设备不要求推送完整 profile；后续请求凭服务端版本自然收敛。
8. 未来更换 scoring 算法时，从 checkpoint 或事件起点重放到新 projection，比较后再切换版本。原始事实保持不变。

Learning 是跨设备 canonical 接收顺序与 accepted explicit-intent revision 的 owner，Vocabulary projection version 表示已应用状态，二者不能混用。推荐显式动作按服务端意图 revision 条件接受；陈旧 base 得到可解释 conflict，而非由客户端时钟决定覆盖。重复/乱序、projection pending、重放和删除屏障的保证及三个冲突方案见 [Lifecycle guarantees](../phase-1-lifecycle-guarantees.md#learning-顺序与显式冲突)。具体 event schema、事务、reducer 和删除完成策略仍由后续阶段设计。

### 8.2 为什么不是直接 CRUD

直接把 `known/unknown` 或 familiarity 写回数据库简单，但会丢失行为背景、算法版本和重算能力。完整 Event Sourcing 又会让 Identity、Content、Lexicon 等无需回放的 Context 承担额外复杂度。推荐只对 Learning 事实采用 append-first，并把 Vocabulary Profile 作为可重建投影；其他 Context 使用普通事务状态。
<!-- retained-contract:end -->
