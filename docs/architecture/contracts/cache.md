# 缓存正确性与隐私合同

> Proposed。先读 [横切合同导读](../phase-1-cross-cutting-contracts.md)。

缓存命中只提高速度；有效性、归属、版本和 expiry 仍是必要条件。

## 精确语义与后续证据

下面保留原合同的任务映射、Must/Should/Later、conformance、验收和未决假设。它们约束后续实现，不能从文档存在推断产品通过。

<!-- retained-contract:start -->
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
<!-- retained-contract:end -->
