# 横切合同导读：让模块协作时保持同一套语义

领域边界说明“谁负责”，横切合同说明“交接时双方可以依赖什么”。**一次交接要证明输入来源、结果含义、版本资格和隐私边界，不能只证明调用成功**。

先按下面三组理解，再查完整条款：语义与来源负责证据含义；缓存与生命周期负责复用资格；信任与观测负责边界保护和诊断。六份详细合同已移至 [合同目录](contracts/README.md)，任务映射和精确条款保留。

> 状态：Proposed。受 [Product Architecture Spec](../../openspec/specs/product-architecture/spec.md)、[ADR](decisions.md) 与既有 Domain 所有权约束。本次调整阅读层次，不选择 SQL、HTTP、Prompt、Provider 或法律保留策略。

## 1. 范围与判定词

**Must** 是必须保持的正确性、安全和边界条件；缺证据不能 PASS。**Should** 是推荐演进方向，暂不实施要记录替代措施、风险和复审条件。**Later** 留给已经明确的后续 owner/阶段。它们的原有含义在详细合同中继续使用。

贯穿六份合同的底线是英文不等待模型、PostgreSQL 持有权威状态，以及网页/客户端/Provider 输入均不能直接成为可信业务事实。

## 2. Semantic Capability Port 合同

Semantic port 接收 `disambiguate`、`translateInContext`、`extractPhrase`、`explainSentence` 这类任务意图，使用有界 context 和调用方预算，返回 Provider-neutral outcome。它不是 `generate(prompt)`，调用方不能从核心合同选择 SDK、model 或 Prompt。

[能力合同](contracts/semantic-capability.md) 展示标准职责活动图，并细化四类能力、输入责任、deadline/cancellation、adapter conformance 和最小化要求。Semantic 提供证据，Enrichment 结合 Profile 和规则决定是否展示；`explainSentence` 不自动成为正常观看的整句翻译。

## 3. Semantic Result 合同

“Provider 返回了内容”还不足以形成 annotation。结果必须绑定输入 identity/context/contract，表达标准 outcome、可比较置信（confidence）和受控 provenance。abstain、timeout、refusal、malformed 等有明确含义；不能拿某厂商分数当未经校准的全局展示门槛。

[结果合同](contracts/semantic-result.md) 保留完整包络、置信和失败矩阵。Semantic 检查自己的输入绑定；Enrichment 重验 Profile/规则；Delivery/Extension 重验 caption/权限与版本，晚到结果不能延长已经结束的观看路径。

## 4. Cache 正确性、有效期与隐私合同

L1 和 Redis 都是副本，命中前仍要核对 owner、用户归属、content/profile/annotation revision、policy/contract version 和 hard expiry。PostgreSQL durable work、Learning facts、Profile 与 Enrichment result 不会因为 read-through 使用就变成 cache。

版本失效（invalidation）加快收敛，读取和显示时校验是最后防线。模型派生证据与个人展示决定的 cache key / 隐私资格也不能混在一起。[缓存合同](contracts/cache.md) 保留层级、key、TTL/freshness、用户隔离、purge 和离线有限保留的精确条件；图中的概念关系不预先选择具体 key 格式或 TTL 数字。

## 5. 信任边界与威胁合同

来源内容是数据，不能被当作控制指令；客户端可以报告观察，不能报告权威 familiarity；Provider 可以提供证据，不能改 Profile 或绕过展示规则。鉴权、大小、因果、版本和权限各由对应 owner 验证。

[信任合同](contracts/trust-boundaries.md) 按资产、信任区域、boundary crossing 和 abuse cases 展开。Provider 输出、用户相关派生状态与诊断资料跨越边界时都有最小化责任，correlation 从不承担授权。常规诊断不保存原始字幕或个人观看历史。

## 6. Observability 与 redaction 合同

可观测性要回答“卡在哪一阶段、因为什么降级、哪些版本参与”，不需要记录用户具体学了什么。常规 logs/trace 只保留受控 stage、outcome、reason、耗时/计数与最小非敏感关联；原始 caption、hint、Profile、观看明细、凭据和 Provider payload 不进入默认诊断。

[观测合同](contracts/observability.md) 保留 correlation、结构化日志、metrics、trace 和 retention/redaction 明细。generated、delivered、displayed、clicked 的分母也要分开，平均时延不能掩盖字幕场景长尾。

## 7. Source Adapter Port 与 conformance 合同

Source Adapter 提供稳定 source/segment identity、可比较 revision、规范文本、可用 location/capability 和提取 provenance。Content 及其他核心模块不认识 YouTube DOM、PDF parser 或 audio SDK。

当前片段可独立交付，previous/next、时间和完整 transcript 缺失是合法状态，不能补造内容或阻塞 English first。[来源合同](contracts/source-adapters.md) 保留点播、直播、Web、PDF、Podcast 五类概念 fixtures；它们证明合同可以容纳差异，不承诺第一阶段实现所有来源。

## 8. 横切一致性场景

把六份合同连起来时，先沿一次交接检查：来源是否可绑定 → 语义是否可信且有界 → 用户是否仍需要 → 缓存是否有效 → 投递是否仍合法 → 诊断是否泄漏内容。

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

六份合同文档存在不能代替实现、独立验收和阶段决定。每项后续证明仍按其详细合同执行；图与 prose 改写必须保留 owner，不把缺证据、未触发或 skipped 称为 PASS。

六项任务的文档完成不等于后续实现已经通过。Phase 1 评审应产生以下可验证的静态 evidence：

1. 本文中的 Must/Should/Later 与 [Phase 1 架构](phase-1.md)、ADR-003/004/005/007/008/009 及 OpenSpec 无 owner 或依赖冲突。
2. 每个 catalog task 都有独立 acceptance evidence 清单和 assumption register；未知项没有被写成已验证事实。
3. 术语 `generated`、`delivered`、`displayed`、`clicked`，以及 attempt、durable work、annotation result、profile evidence 的 owner 保持分离。
4. 所有失败、缓存 miss、来源缺失和 telemetry 缺失都维持 English first；任何模型结果都不能绕过 Enrichment Rules 决定展示。
5. 文档不包含 SQL 字段、具体 REST endpoint、Docker Compose、代码/序列化定义、Prompt 或真实用户/Provider 数据。

Phase 2–7 任务在实现这些合同前，应把对应 Must 转换为 contract test、architecture test、failure-injection journey 或 privacy/security evidence；若实现需要改变 Must，必须先更新 OpenSpec/ADR 并重新通过 Phase gate，不能由单个 adapter 或 Provider 隐式改写。

详细合同变动后，历史 receipt 的输入哈希不能用于当前 bytes；本次影响见 [重构审查](../reviews/architecture-documentation-restructure.md)。
