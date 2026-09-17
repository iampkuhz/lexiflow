# LexiFlow Phase 1 横切架构合同

> 状态：`Proposed`，随 Phase 1 架构一并等待人工评审
>
> Catalog tasks：`LF-TSK-SEM-0001`、`LF-TSK-SEM-0002`、`LF-TSK-PRF-0002`、`LF-TSK-SEC-0001`、`LF-TSK-OBS-0001`、`LF-TSK-ADP-0001`
>
> 本文补充 [Phase 1 架构](phase-1.md) 与 [架构决策记录](decisions.md)，并受 [Product Architecture Spec](../../openspec/specs/product-architecture/spec.md) 约束。发生表述歧义时，以 OpenSpec 的规范性要求、ADR 的 owner/依赖决策和本文更具体的横切合同依次解释，不改变既有 Domain 所有权。

## 1. 范围与判定词

本文只固定跨模块可依赖的架构语义和后续实现必须证明的证据，不定义 SQL 字段、具体 REST endpoint、Docker Compose、代码类型、序列化格式、Prompt 或具体 Provider 配置。

判定词含义如下：

- **Must**：进入下一阶段前必须保持的正确性、安全或边界条件；缺少对应证据时相关任务不得报告 `PASS`。
- **Should**：推荐的生产演进方向；若暂不实现，必须记录替代措施、风险与复审触发器。
- **Later**：已有明确 owner 和激活阶段，但 Phase 1 不提前决定的实现选择。

六份合同共同遵循三条底线：英文字幕渲染不等待后端或模型；PostgreSQL 保存事实和 durable state，其他缓存均可重建；外部输入、客户端声明和 Provider 输出都不是可信业务事实。

## 2. Semantic Capability Port 合同

**对应任务：`LF-TSK-SEM-0001`**

本合同细化 ADR-007 的任务级抽象。业务模块只表达需要完成的语义能力、受约束的输入语义和预算；Semantic 负责能力路由、Provider 隔离与标准结果发布。端口不是通用文本生成接口，也不允许调用方选择具体 Provider、模型或 Prompt。

### 2.1 任务级能力

| Capability | 调用意图与有界输入 | Semantic 的责任 | 不属于 Semantic 的责任 |
|---|---|---|---|
| `disambiguate` | 为一个已定位的 term/phrase，在当前 caption 与允许的相邻 context 中判定候选语义。 | 返回与输入绑定、可校验且带置信与 provenance 的语境义证据，或明确 abstain/failure。 | 修改 Global Lexicon、推断用户熟悉度、决定是否展示。 |
| `translateInContext` | 为已选定的 term/phrase 与语境义生成目标语言的短表达；目标语言和表达约束由调用方声明。 | 保持 term、语境义、引用范围一致；只产生短语级语义证据。 | 生成整句双语字幕、决定 UI 文案长度或样式、覆盖原英文。 |
| `extractPhrase` | 在一个有界 caption/context 中找出具有整体语义的候选 span。 | 返回可绑定回原输入的候选 span、整体语义证据与置信；可返回无可靠候选。 | 判断用户是否需要提示、调整字幕 DOM、把每个候选都变成 annotation。 |
| `explainSentence` | 对用户显式请求或受控学习流程中的一个句子及有界 context 给出结构化解释证据。 | 区分词义、结构或语用等受支持的解释类别，并保持输入引用。 | 在正常观看 fast path 自动生成整句中文、替代 Learning 或 Enrichment 决策。 |

四类 capability 共享调用包络和 `LF-TSK-SEM-0002` 的标准 outcome，但各自拥有独立的输入约束、结果 invariant、质量评估与预算。一个 capability 的成功 payload 不能作为另一 capability 的成功结果使用。

### 2.2 调用包络与责任边界

以下元素描述架构语义，不承诺后续代码方法、wire 字段或序列化格式。

**Must**

- 请求必须携带 capability 与 contract version、规范输入 identity、最小必要的有界 context、目标语言或解释意图（若该 capability 需要）、调用方 deadline/cancellation 和数据分类。缺少必需上下文时应 abstain 或失败，不能补造字幕事实。
- 调用方负责 Content identity、source/context revision、用户授权和输入最小化；Semantic 不回读其他 Domain 的私有表来补全请求。
- Semantic 负责把任务路由到兼容 adapter、约束总 attempt/deadline/cost budget、归一并校验结果，再以 `LF-TSK-SEM-0002` 的 provider-neutral outcome 返回。
- Provider adapter 只实现外部系统适配与不可信输出归一，不拥有业务展示规则、Vocabulary Profile 或 annotation 生命周期。
- Enrichment 负责结合 Lexicon、Profile、规则和 Semantic evidence 决定 annotation；Client Delivery/Extension 只消费已发布的业务合同，不直接调用 Provider。
- deadline 和 cancellation 从调用方传入并沿路缩短。Semantic 只对 request/input identity、context revision 和 contract version 做晚到兼容校验；Enrichment 在形成或发布 annotation 前重验 profile/rule/annotation 前置版本；Client Delivery/Extension 在投递或显示前重验 caption/annotation/profile version。任何一层都不能用晚到结果延长已结束的观看路径。
- 默认只发送完成 capability 所需的最小 context。原始整段 transcript、Vocabulary Profile、账户标识和行为历史不得作为隐式上下文；确需用户相关信号时必须由 owner 提供已授权、最小化且可审计的派生信息。
- 输出必须是标准结构化 outcome。Provider 自有 SDK 对象、model name、token/accounting 字段、HTTP 状态、原始错误或自由文本响应不得穿过 Semantic port 进入 Domain。

**Should**

- 每类 capability 拥有独立 conformance fixtures，证明 fake、local 和外部 adapter 对同一请求语义产生可比较的标准 outcome。
- capability version 与 result contract version 独立演进；破坏性变化通过显式兼容审查，不用 Provider 路由配置暗中改变业务语义。
- 调用方给出用途标签和隐私等级，使 routing policy 能选择满足数据驻留、保留和成本限制的 adapter，但用途标签不能包含原始学习内容。

**Later**

- Phase 4 决定代码级 port 形态、各 capability 的具体 payload schema、routing/fallback 策略、Provider 清单和 deadline 数值。
- Phase 4/7 用离线标注集、故障注入与生产 SLO 确定 adapter 资格、预算和熔断参数。
- Prompt、Provider 参数与凭据装配均属于 adapter/operations 实现，不在 Phase 1 合同中固定。

### 2.3 Provider-neutral conformance review

审查以业务调用方可见的 port 为边界，并对四类 capability 逐项回答：

| 检查 | `PASS` 条件 | 失败示例 |
|---|---|---|
| 能力语义 | 名称和输入表达业务任务，结果可绑定回规范输入。 | `generate(prompt)`、无法定位来源的自由文本。 |
| Provider 隔离 | Domain 只看 capability、预算和标准 outcome。 | SDK request/response、model ID、HTTP status 出现在 Domain contract。 |
| Owner 边界 | Semantic 只提供证据；Enrichment/Profile/Learning 保持各自决策权。 | Semantic 直接标记“用户不认识”或“必须显示”。 |
| 预算与取消 | deadline、cancellation、attempt/cost budget 可被 adapter 遵守和观测。 | Provider 自行无限重试，晚到结果无版本检查地发布。 |
| 隐私最小化 | 每项输入都能说明 capability 必要性、授权来源和保留边界。 | 默认发送完整 transcript、账户 ID 或完整 Profile。 |
| 替换能力 | fake/local/外部 adapter 可在不改变调用方合同的情况下替换。 | 调用方分支判断特定 Provider 错误或输出格式。 |

### 2.4 Acceptance evidence

`LF-TSK-SEM-0001` 只有在以下证据可复核时才可判为 `PASS`：

1. 一份覆盖四类 capability 的 Provider-neutral type review，逐项确认业务名称、输入责任、标准 outcome 与 owner 边界，没有 SDK、Prompt、model 或 transport 泄漏。
2. 一张 capability responsibility matrix，证明 Content/Enrichment/Semantic/adapter/Client 的输入提供、路由、验证、展示和持久化责任唯一。
3. 至少两种不同 adapter（其中一种可为 deterministic fake）的概念 conformance walk-through，证明调用方合同不随 Provider 改变。
4. 一个 English-first 降级 journey，证明 timeout、abstain、failure 或 cancellation 均不会延迟英文字幕，也不会触发整句双语兜底。
5. 一份 privacy review，说明每类 capability 的最小上下文、禁止输入、授权 owner 与 retention owner。

### 2.5 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| SEM-PORT-A1 | 四类能力足以支撑 MVP，新增能力无需退化为通用文本接口。 | Phase 4，Semantic 与 Enrichment owner 用代表性语料验证。 |
| SEM-PORT-A2 | `explainSentence` 主要服务显式学习动作，不进入正常 caption fast path。 | Phase 5/6 可用性测试与成本测量。 |
| SEM-PORT-A3 | 最小相邻 caption window 足以覆盖多数消歧与短语抽取。 | Phase 4 离线标注集比较；不足时通过有版本的 context policy 调整。 |

## 3. Semantic Result 合同

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

## 4. Cache 正确性、有效期与隐私合同

**对应任务：`LF-TSK-PRF-0002`**

缓存优化读取和复用，不改变事实 owner。Vocabulary Profile 的权威版本、Learning facts、durable handoff 和 Enrichment durable result 均不能只存在于 L1 或 Redis。

### 4.1 层级与 owner

| 层/逻辑缓存 | 内容与 owner | 是否可丢弃 | 命中前 Must 校验 |
|---|---|---|---|
| Extension L1 | 当前设备的 annotation/profile snapshot 副本；Client Delivery / Sync 定义兼容合同。 | 是 | 用户/设备归属、content/caption identity、annotation revision、profile version、策略/合同版本、hard expiry。 |
| Redis hot cache | Lexicon、Profile query、annotation delivery、Semantic/Context 的热点派生副本；各 Domain port 仍保有 owner。 | 是 | namespace、数据分类、owner version、生成版本、source/profile revision 与 hard expiry。 |
| PostgreSQL durable state | Learning facts、Profile 投影、durable work、Enrichment durable result。它是事实/恢复来源，不因被 read-through 使用就变成 cache。 | 否 | owner、事务状态、业务版本和授权边界。 |
| PostgreSQL derived reuse | 可选的 Semantic/Context 派生结果，用于跨重启复用；仍由对应 Domain 管理，可重建。 | 是，但删除/重建须受 owner 控制 | contract/context/source/model-policy version、数据保留和隐私 eligibility。 |
| Semantic logical cache | 经校验的 Provider-neutral 语义 evidence；物理上可位于 Redis 或 PostgreSQL derived reuse。 | 是 | capability、规范输入、context fingerprint、目标语言、Semantic contract/calibration/routing-compatible version。 |
| Context logical cache | Content 规范化片段和有界 context window；物理上可位于 L1、Redis 或 PostgreSQL derived reuse。 | 是 | canonical content identity、source revision、segment identity/window、normalization version、完整度。 |

### 4.2 Key contract

以下是 key 的语义组成，不规定字符串拼接或序列化格式。

**Must**

- 所有 key 都有 schema namespace 和 owner version；不同环境、租户隔离等级、数据类别和 cache purpose 不得共用可碰撞空间。
- 用户专属 annotation/Profile key 必须绑定后端确认的用户边界及 profile version。客户端提供的 user/profile 声明不能单独决定命中。
- Semantic key 至少区分 capability、规范化 term/phrase、目标语言、context fingerprint、Semantic contract version 和影响语义的策略/model family 版本。
- Context key 至少区分 canonical content identity、source revision、当前 segment、所请求的有界窗口和 normalization version。
- Context/Semantic fingerprint 使用规范化输入的不可逆摘要；原始字幕、整段 transcript、Vocabulary Profile 或行为历史不得直接出现在 cache key、日志 key 或 metric label。
- 只有证明不含用户专属输入和输出的 Semantic/Context 派生结果才可跨用户复用。只要 Profile、个人偏好或行为影响结果，就必须进入用户隔离 namespace。
- Annotation cache key 必须覆盖语义结果版本与 need-hint/rule version；只更新 Semantic 内容但遗漏展示规则版本会导致错误复用。

**Should**

- key 构造使用稳定 canonicalization contract，并用 golden fixture 证明空白、大小写、Unicode、时间窗口和 segment 排序的确定性。
- 大对象内容只作为 value；key 使用稳定 identity 和摘要，避免 PII 暴露与超长 key。
- 对相同 semantic/context miss 使用有界 single-flight，且等待者仍遵守自己的 deadline。

**Later**

- Phase 2 固定持久 identity/revision 的数据语义；Phase 4 固定 Semantic canonicalization；Phase 5 固定 Extension L1 的物理格式。

### 4.3 TTL 与 freshness contract

TTL 数值由真实延迟、成本和内容变化率决定，因此 Phase 1 固定语义而不伪造秒数。每个 cache policy 必须配置有限的 soft/hard 有效期；“未配置”不得解释为永久有效。

| 层 | Soft expiry 后 | Hard expiry 后 | 不受 TTL 替代的失效条件 |
|---|---|---|---|
| Extension L1 | 可在 English first 已完成后暂用兼容旧值并异步刷新；UI 必须能识别 stale。 | 不再显示该 annotation，只保留英文并请求新结果。 | 用户切换/登出、profile version 不兼容、caption/revision 改变、撤回/删除信号。 |
| Redis Profile/annotation | 可触发 read-through refresh；旧值只有在 owner 明确允许 stale-read 时可返回。 | 必须 miss 并从 owner 读取。 | profile/annotation version 变化、权限或数据归属变化。 |
| Redis Semantic/Context | 可在调用预算内 refresh 或暂用仍兼容证据；不得延长错误/拒绝为成功。 | 必须重新计算或降级。 | source/context/normalization/contract/model-policy version 改变。 |
| PostgreSQL derived reuse | 可标记待 refresh，继续使用需满足 owner 的兼容规则。 | 不参与新决策，并进入 owner 管理的清理流程。 | 删除请求、保留策略、source 撤回、contract/隐私分类变化。 |
| PostgreSQL facts/durable state | 不使用 cache TTL 决定正确性。 | 不适用。 | 由业务 retention、显式删除和状态机规则管理。 |

**Must**

- 每个 entry 具有可比较的生成时间、soft/hard freshness 界限和影响正确性的版本集合；wall-clock TTL 与版本校验必须同时成立。
- TTL 到期不是用户数据删除证明；删除、撤回和权限失效需要主动 invalidation/purge 流程。
- invalidation 失败时系统优先 miss/回源或不提示，不能继续展示可能跨用户或跨版本的值。
- Profile 更新只失效用户相关 annotation/Profile cache，不应清空可跨用户复用的 Lexicon/Semantic/Context cache。
- Content source revision、normalization version 或 context fingerprint 改变时，所有基于旧上下文的 annotation 与 Semantic evidence 均不得命中新决策。
- 缓存失败、flush 或冷启动不会丢失 Learning fact、durable work、Profile 权威状态或 Enrichment durable result。

**Should**

- Redis hard expiry 使用受控 jitter 防止同批 key 同时过期；不得用无限滑动续期掩盖旧版本。
- negative/no-result cache 只覆盖可确定复用的 abstained/no-candidate 语义，并使用短于成功结果的有限有效期；timeout/refusal/rate limit 只允许进入独立短期 failure suppression。
- 以 owner event/version hint 做主动失效，以读取时版本检查做最终防线；任何 pub/sub 通知都不能成为唯一正确性机制。

**Later**

- Phase 3/4 基准测试给出各 policy profile 的 soft/hard 默认值和 stale-read 预算。
- Phase 7 依据生产 hit rate、stale discard、删除完成时延和成本调整 TTL；调整不得改变上述正确性条件。

### 4.4 隐私与最小化

**Must**

- Extension L1 只保存完成当前体验所需的最小派生结果，并服从登出、账号切换、权限撤回和用户清除动作；不得保存完整观看历史或整个 Vocabulary Profile。
- Redis 不承担长期用户数据仓库；用户相关 value 必须隔离、有限有效并可由 owner 定位清除。
- Provider 原始响应、原始模型输入、真实字幕、用户词汇状态和行为历史不得进入通用共享 cache、key、日志或测试 fixture。
- Semantic 跨用户复用前必须证明 value 不含用户或设备标识、个人 Profile 影响、Provider 返回的个人信息和来源访问凭据。
- 缓存观测只暴露受控 tier/result/reason/version 维度；禁止以用户、视频、caption、term 或摘要值作为 metrics label。

**Should**

- 共享派生数据与用户派生数据使用不同 namespace、访问策略和 retention policy。
- 隐私清除 evidence 同时列出 L1 通知、Redis purge 和 PostgreSQL owner deletion；无法触达的离线 L1 依靠有限 hard expiry 与下次鉴权拒绝共同收敛。

**Later**

- Phase 2 的 `LF-TSK-SEC-0002` 固定用户导出、删除完成语义和 retention；Phase 7 验证生产清除时限。

### 4.5 Acceptance evidence

`LF-TSK-PRF-0002` 的 `PASS` 证据至少包括：

1. cache inventory，逐项标明业务 owner、物理 tier、可否丢弃、用户隔离、key 输入、版本、soft/hard expiry 和 invalidation source。
2. key golden fixtures，证明 context、target language、profile/source/rule/contract version 任一影响正确性的输入改变都会 miss；等价规范输入仍稳定命中。
3. invalidation scenario matrix，覆盖 Profile 更新、caption/source revision、规则升级、Provider/contract 升级、登出/用户切换、删除、Redis flush 和 L1 离线。
4. cold-cache recovery journey，证明 Redis/L1 全空仍可从 PostgreSQL facts/durable result 恢复，英文渲染不受影响。
5. privacy review，证明 key/value/telemetry/test fixture 均无真实字幕、观看历史、Vocabulary Profile、Provider 原始输入输出或身份泄漏。

### 4.6 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| PRF-A1 | 短期 stale-compatible annotation 比“hard expiry 后无提示”更有体验收益。 | Phase 5 Extension journey 与用户测试。 |
| PRF-A2 | Semantic/Context 的共享命中率足以抵消 canonicalization 与隐私审查成本。 | Phase 4 benchmark，Semantic/Performance owner。 |
| PRF-A3 | 版本 hint 加读取时校验能满足跨设备 Profile 收敛，无需强一致分布式 cache。 | Phase 3/5 多设备 contract test。 |
| PRF-A4 | PostgreSQL derived reuse 的保留成本在初期可控。 | Phase 4/7 容量与删除测试。 |

## 5. 信任边界与威胁合同

**对应任务：`LF-TSK-SEC-0001`**

### 5.1 资产与信任区域

| 资产 | 业务 owner | 主要风险 |
|---|---|---|
| 用户、设备与会话身份 | Identity & Access | token 窃取、会话混淆、越权。 |
| Caption/Context 与来源位置 | Content | 私密内容泄漏、恶意内容污染、错误归属。 |
| Vocabulary Profile 与 Learning facts | Vocabulary Profile / Learning | 跨用户读取、行为伪造、不可解释篡改。 |
| Durable work 与 annotation result | Workflow/Enrichment | replay、重复执行、篡改、stale 投递。 |
| Provider credentials、预算与路由策略 | Platform/Semantic | 密钥泄漏、成本滥用、供应链风险。 |
| Logs、metrics、traces 与 run evidence | Platform/Observability | 二次数据泄漏、不可控保留和关联识别。 |

信任区域如下：

1. **Browser/page 区域**：网页、YouTube DOM、第三方脚本和页面文本不可信。
2. **Extension 区域**：官方 Extension 代码可受浏览器上下文、旧版本或本地篡改影响；它是受限客户端，不是授权事实来源。
3. **Public network / API boundary**：所有客户端输入在此重新认证、授权、限流、规范化和绑定 owner。
4. **Application / Domain 区域**：只消费已经通过入口校验的 command，但仍按 Domain invariant 验证行为语义。
5. **Worker / durable handoff 区域**：任务来自可信存储却可能重复、过期、旧版本或被错误配置；消费端必须幂等并重验合同。
6. **Store 区域**：PostgreSQL/Redis 通过各 Domain outbound port 访问；基础设施权限不赋予跨 Domain 数据所有权。
7. **External Provider 区域**：Provider 是外部处理方；输入必须最小化，输出始终不可信，凭据只能由 server-side adapter 持有。

### 5.2 Boundary crossing rules

**Must**

- 网页内容只作为数据进入 source adapter；其中的指令性文本、标记或脚本不得改变系统 policy、工具权限、Provider routing 或观测规则。
- Extension 永远不能自证 user、device、profile version、Learning effect 或数据 owner。API 用服务端身份上下文重新绑定并校验所有引用。
- API 在 durable intake 前限制输入大小、上下文窗口、语言/编码、引用归属、幂等身份和速率；失败输入不得进入 Domain 或 worker queue。
- Worker 对 job identity、task/change contract version、owner、attempt/retry budget 和 stale/cancel 状态重新校验；重复交付只产生一次业务效果。
- Domain 只能通过自己的 port 访问 store；不得利用共享数据库权限读取其他 Domain 表或绕过公开 contract。
- 发送 Provider 的数据仅包含当前 capability 所需的有界文本和非个人化语义上下文；不得发送完整观看历史、完整 Vocabulary Profile、会话凭据或无关 transcript。
- Provider credential 只存在于 server-side secret boundary；不得进入 Extension、durable payload、日志、trace、fixture 或 Provider result。
- Provider 输出经过大小、结构、语言、引用和 Semantic contract 校验；输出文本不得直接变成代码、查询、权限或路由指令。
- telemetry 按第 6.5 节 redaction contract 处理；生产数据不得复制到测试或 Agent handoff。

**Should**

- Extension 权限保持最小 host/feature 范围，页面隔离层与 privileged Extension 层分开。
- API、worker 与 store 使用独立运行身份和最小权限；Provider adapter 按 provider/环境隔离 credential 与预算。
- 高成本 Semantic capability 同时受用户、设备、来源和全局预算保护，避免合法账号被自动化放大。

**Later**

- Phase 2 固定授权、用户隔离、导出和删除控制；Phase 3 固定 transport 认证与输入限制；Phase 4 固定 Provider data-handling；Phase 7 完成密钥轮换、恢复和外部安全测试。

### 5.3 Threat 与 abuse-case register

| ID | Abuse case | 边界 | Must control / safe outcome |
|---|---|---|---|
| SEC-T1 | 恶意页面伪造 caption、超长文本、控制字符或指令性内容。 | Page → Extension → API | 本地隔离、规范化、大小限制；当作内容数据；失败时只显示原英文/原来源内容。 |
| SEC-T2 | 被篡改 Extension 使用他人 content/profile/result identity。 | Extension → API | 服务端认证与 owner binding；跨用户引用统一拒绝且不泄漏存在性。 |
| SEC-T3 | 离线重放、重复点击/展示或伪造 Learning event。 | Extension → Learning | 幂等 identity、引用链和语义校验；客户端数值不能直接写 familiarity。 |
| SEC-T4 | 攻击者制造大量难句触发 Provider 成本。 | API/Worker → Provider | capability allowlist、配额、总预算、回压与可观测拒绝；英文路径继续。 |
| SEC-T5 | durable job 重复、过期、版本不兼容或被错误路由。 | Store → Worker | contract/version/owner 校验、幂等、stale cancel；不产生重复 annotation/evidence。 |
| SEC-T6 | Provider 返回 malformed、恶意标记、过长内容或诱导系统改变 policy。 | Provider → Semantic | 标准化与约束校验；失败分类；原始输出不进入 Domain/telemetry。 |
| SEC-T7 | Redis key 碰撞或共享 Semantic cache 混入用户信息。 | Cache → Application | namespace/version/user isolation、跨用户复用 eligibility；可疑命中按 miss 处理。 |
| SEC-T8 | 日志/trace 记录字幕、term、Profile、token 或 Provider payload。 | Runtime → Telemetry | allowlist 属性、集中 redaction、forbidden-field scan；事件保留但敏感值删除。 |
| SEC-T9 | 内部模块凭共享数据库绕过 Domain owner。 | Module → Store | port-only dependency、最小数据库权限、architecture/contract test。 |
| SEC-T10 | 账号切换或撤权后离线 L1 继续显示前用户 hint。 | Identity → Extension L1 | user/session binding、主动清除、有限 hard expiry、下一次鉴权拒绝。 |
| SEC-T11 | source revision 改变后旧 annotation 被投递到新 caption。 | Delivery → Extension | content/caption identity、revision/profile version 比对；stale 静默丢弃。 |
| SEC-T12 | 运维、测试或 Agent 复制生产字幕与学习数据用于复现。 | Store/Telemetry → Tooling | 只用合成 fixture；受控诊断 metadata；禁止真实数据进入仓库和 handoff。 |

### 5.4 Acceptance evidence

`LF-TSK-SEC-0001` 的 `PASS` 证据至少包括：

1. 一张含 Extension、API、Application、worker、PostgreSQL、Redis、Provider 与 telemetry sink 的 data-flow/trust-boundary 图，每条 crossing 标明认证、校验、最小化和 owner。
2. 上述 threat register 的逐项评审 receipt，至少给出预防、检测、安全降级和后续 owner；仅写“以后加安全”不算完成。
3. abuse fixtures 覆盖跨用户引用、重复 event/job、超长/恶意 caption、malformed Provider、stale result、cache collision、成本放大和 telemetry 泄漏。
4. secret/data-flow review，证明 Provider 凭据只在 server adapter，发送 Provider 的输入有界且无 Profile/history/session credential。
5. architecture evidence，证明 core module 不依赖 Chrome/YouTube、HTTP、Redis、PostgreSQL 或具体 Provider，且共享 store 不绕过 Domain contract。

### 5.5 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| SEC-A1 | 首版认证机制可以同时表达用户与设备归属，不需把设备提升为独立业务 owner。 | Phase 2 Identity threat review。 |
| SEC-A2 | 发送有界三句上下文足以满足主要 Semantic capability。 | Phase 4 质量/隐私对照实验。 |
| SEC-A3 | Browser L1 的有限 hard expiry 加下次鉴权足以处理长期离线设备。 | Phase 5/7 离线撤权 journey。 |
| SEC-A4 | 单体内部的模块权限与 architecture gate 在早期足以防止跨 Domain 数据访问。 | Phase 2 persistence spike；若不足，再拆数据库 role/schema 权限。 |

## 6. Observability 与 redaction 合同

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

[Lifecycle guarantees](phase-1-lifecycle-guarantees.md) 进一步区分 work/attempt/fencing、取消后的提交权限与 delivery ACK/实际显示事实；trace 的成功 span 不得代替这些 owner 的 durable 状态。Learning canonical intent order、投影 pending 及删除 generation 也只传播最小非敏感关联，不扩展为内容日志。

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

## 7. Source Adapter Port 与 conformance 合同

**对应任务：`LF-TSK-ADP-0001`**

Source Adapter 负责从具体来源获取并规范化内容。Content 接收来源无关的 canonical representation；Lexicon、Vocabulary、Learning、Semantic 和 Enrichment 不得依赖 YouTube DOM、web selector、PDF library、audio decoder 或平台 SDK 类型。

### 7.1 Canonical port semantics

Port 在概念上交付以下语义；这些是 contract value，不是实现方法签名：

| 语义 | Must invariant |
|---|---|
| Source identity | 同一来源对象在 adapter 可观察生命周期内稳定、不可由展示标题代替；source kind 只是 metadata，不允许核心据此分支平台逻辑。 |
| Source revision | 内容变化时可比较；无法得到原生 revision 时由 adapter 产生保守 fingerprint，并明确可靠度。 |
| Segment identity | 在 source revision 内稳定；重复采集同一 segment 得到同一 identity，内容变化或边界重切有新 revision/identity。 |
| Text unit | 保留原始语言、规范化文本、可选 speaker/track 信息与提取 provenance；不得伪造缺失 transcript。 |
| Source location | 表达时间范围、页/区域、文档顺序或选区中的可用部分；核心只消费通用 location capability，不解析平台私有 locator。 |
| Context window | 当前 segment 必需；previous/next 可缺失、迟到、乱序或不连续，并显式说明 completeness。 |
| Playback/reading state | 可选位置、速度、暂停/seek 或选区状态；只作为上下文/行为输入，不改变内容 identity。 |
| Adapter capabilities | 明确是否 live、timed、seekable、ordered、selectable、transcript-complete；缺少 capability 是合法状态。 |
| Provenance | 标明 adapter/source kind、contract version、采集时间、语言/track 与 normalization version；不携带页面凭据。 |

**Must**

- adapter 对同一输入确定性地产生 canonical identity、revision 和 normalization；结果顺序、重试和重复 callback 不改变 identity。
- 当前 segment 可独立交付。previous/next、时间、speaker、完整 transcript 或语言置信缺失时，不阻塞 English first，也不以空字符串冒充已知。
- source-specific metadata 留在 adapter-owned opaque reference；核心 Domain 不能导入、解析或条件判断它。
- adapter 不决定用户是否需要 hint，不读取 Vocabulary Profile，不调用具体 Semantic Provider，也不产生 Learning familiarity。
- page/document/audio 内容按不可信数据规范化，限制大小与窗口；隐藏脚本、控制指令和富标记不能越过 port 成为执行语义。
- source revision 或 segment binding 不能证明时，结果使用保守的新 identity/revision，使旧 annotation miss，而不是冒险复用。
- 无字幕/转写时明确报告 content unavailable/capability absent；不得猜测文本。

**Should**

- 支持增量片段和 backfill，使 live 来源不必等待完整 transcript。
- 将重叠、断句变化和 seek 后乱序归一成可测试事件，允许 Content/Extension 做去重和 stale reject。
- accessibility text、官方 caption 或用户明确选区优先于不可解释的页面抓取；provenance 保留来源质量等级。

**Later**

- Phase 5 实现 YouTube Extension adapter；网页、PDF 与 audio/podcast adapter 只在 ADP 后续 research task 证明产品价值后激活。
- 具体抓取 API、DOM selector、PDF parser、ASR 方案和平台权限均由各 adapter 阶段决定，不进入 core contract。

### 7.2 Conformance examples

| Fixture | Adapter 输入能力 | Canonical 输出期望 | 缺失/变化处理 |
|---|---|---|---|
| YouTube recorded video | timed caption track、player position、video identity；caption callback 可能重复或重切。 | current timed segment、稳定 source/revision/segment、可选 previous/next、`live=false`、`timed=true`。 | 无 next 时显式 incomplete；seek/track/revision 变化使旧 result stale；DOM/track 私有对象不越界。 |
| YouTube live | 逐步出现的 timed captions、窗口持续滚动。 | 增量 current segment、单调可比较的 adapter revision/provenance、有限 context window、`live=true`。 | 文本修订产生可比较 revision；不等待直播结束；迟到增量不覆盖当前 segment。 |
| Web article/selection | document identity、阅读顺序、段落或用户选区，无可靠播放时间。 | ordered text segment、document/selection location、current + 可选相邻块、`timed=false`、`selectable=true`。 | DOM 重排但文本/identity 等价时保持 canonical；内容实质变化更新 revision；脚本/导航噪声不作为文本。 |
| PDF page/selection | document fingerprint、page/order、选区或提取文本，可能多栏/扫描。 | page/order location、规范化 text segment、提取 provenance、可选相邻段。 | 无文本层时报告 capability absent 或待外部转写；不制造阅读顺序；新 PDF revision 不复用旧 context。 |
| Audio/podcast transcript | episode identity、timed transcript/ASR segment、playback position。 | timed segment、transcript provenance/quality、有限前后文、`timed=true`、`seekable` 按来源声明。 | 无 transcript 时 content unavailable；ASR 修订更新 revision；低置信文本被标记而非当作确定字幕。 |

这些 fixture 只证明同一 port 能承载来源差异，不承诺 Phase 1 实现未来来源。

### 7.3 Cross-source conformance suite

**Must**

- 每种 fixture 通过同一组 canonical contract assertions：identity 稳定、revision 可比较、current 必需、邻居可选、location capability 可缺、语言/provenance 明确、大小有界。
- suite 覆盖重复、乱序、缺 previous/next、segment overlap、seek、source revision、未知语言、无 transcript 和超长输入。
- core fixture 只使用 canonical values；任何断言若需要 YouTube video object、DOM node、CSS selector、PDF page class 或 audio decoder 类型，即判为 adapter 泄漏。
- 同一 canonical Content Context 进入 Enrichment/Learning 时，不因来源名称改变 need-hint、Profile owner 或 event 语义。
- conformance fixture 使用合成文本和身份，不含真实用户字幕、观看历史或受版权限制的完整材料。

**Should**

- contract suite 由所有 source adapter 复用，adapter 可添加自己的 acquisition tests，但不能删除共享 assertions。
- 建立 source-neutral golden context window，证明 timed 与 untimed 来源都能触发相同的 fast/slow enrichment contract。

**Later**

- `LF-TSK-ADP-0002` 在 Phase 5 用 dependency/import gate 证明 core 无 YouTube 类型；后续 adapter research 各自提交 conformance receipt。

### 7.4 Acceptance evidence

`LF-TSK-ADP-0001` 的 `PASS` 证据至少包括：

1. 一份 source-neutral port review，覆盖 identity、revision、segment、location capability、context completeness、language 与 provenance，且无平台 SDK/DOM 类型。
2. YouTube recorded/live、web、PDF 与 audio 五类合成 fixture 的期望结果，证明 timed 与 untimed、完整与部分 context 均可表达。
3. determinism/property evidence，证明重复/乱序输入可去重，source revision 或实质内容变化不会误命中旧 annotation。
4. forbidden-dependency evidence，证明 Content 以外的 core Domain 不按 source kind 分支，也不依赖 Chrome、YouTube、网页、PDF 或 audio 实现类型。
5. no-text journey，证明字幕/transcript 不可用时系统明确降级且不猜测内容，English first/原来源体验继续。

### 7.5 未决假设

| ID | Assumption | 验证时机/owner |
|---|---|---|
| ADP-A1 | `identity + revision + segment + optional location` 足以统一 timed 与 untimed 来源。 | Phase 5 YouTube 实现和后续 web/PDF spike。 |
| ADP-A2 | 三段式有界 context 是初期质量/隐私/成本的合理折中。 | Phase 4 Semantic evaluation。 |
| ADP-A3 | YouTube caption callback 可构造稳定 segment identity，即使平台重切断句。 | Phase 5 Extension spike；失败则使用保守 revision 而非放宽 stale 检查。 |
| ADP-A4 | Web/PDF/audio 只需 source-specific acquisition，核心 Learning/Enrichment 语义无需分叉。 | 每个未来 adapter 的 conformance review。 |

## 8. 横切一致性场景

以下场景用于检查六份合同是否组合后仍保持既有架构语义。

| 场景 | 必须成立的结果 |
|---|---|
| 恶意页面给出超长、含指令的 caption | Source adapter 将其视为不可信内容并有界规范化；API 再校验；Provider 输出仍需结构校验；telemetry 不记录内容；英文/原来源渲染不等待。 |
| L1 命中旧 annotation，同时 server Profile 已更新 | Extension 依据 profile/version contract 拒绝或标记 stale；server cache 回源权威 Profile；Semantic/Context 全局证据不因个人更新被无差别清空。 |
| Provider 超时后返回晚到结果 | timeout attempt 保持失败；晚到 payload 不附着旧 attempt；durable job 可按预算新建 attempt；旧 caption/revision 不接受结果。 |
| Redis flush 且 worker 重复消费 | cache 只产生 miss；PostgreSQL durable state 恢复工作和结果；worker 幂等；correlation 显示新 attempt 而业务效果唯一。 |
| 用户切换账号并长期离线 | L1 主动清除且受 hard expiry；后端拒绝旧 owner；缓存 key 不跨用户；日志与指标不暴露前用户内容。 |
| Future PDF 没有文本层 | adapter 报 capability absent，不伪造 transcript；核心不调用 Semantic；原 PDF 阅读可继续；该 outcome 以无内容类别聚合观测。 |

## 9. 总体验收与 Phase handoff

六项任务的文档完成不等于后续实现已经通过。Phase 1 评审应产生以下可验证的静态 evidence：

1. 本文中的 Must/Should/Later 与 [Phase 1 架构](phase-1.md)、ADR-003/004/005/007/008/009 及 OpenSpec 无 owner 或依赖冲突。
2. 每个 catalog task 都有独立 acceptance evidence 清单和 assumption register；未知项没有被写成已验证事实。
3. 术语 `generated`、`delivered`、`displayed`、`clicked`，以及 attempt、durable work、annotation result、profile evidence 的 owner 保持分离。
4. 所有失败、缓存 miss、来源缺失和 telemetry 缺失都维持 English first；任何模型结果都不能绕过 Enrichment Rules 决定展示。
5. 文档不包含 SQL 字段、具体 REST endpoint、Docker Compose、代码/序列化定义、Prompt 或真实用户/Provider 数据。

Phase 2–7 任务在实现这些合同前，应把对应 Must 转换为 contract test、architecture test、failure-injection journey 或 privacy/security evidence；若实现需要改变 Must，必须先更新 OpenSpec/ADR 并重新通过 Phase gate，不能由单个 adapter 或 Provider 隐式改写。
