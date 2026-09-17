# LexiFlow Phase 1 架构决策记录

> Task id: `LF-ARCH-P1-001`
>
> 状态说明：本页决策均为 `Proposed`，待 Phase 1 人工评审后改为 `Accepted`、`Rejected` 或 `Superseded`。
>
> 范围：只记录架构方向和 trade-off；实现细节由后续 Phase 决定。

## 决策索引

| ID | 决策 | 推荐 | 状态 |
|---|---|---|---|
| ADR-001 | 系统形态 | Modular Monolith + 强 Domain 边界 | Proposed |
| ADR-002 | 运行时切分 | 同代码库的 `api` 与 `worker` 两入口 | Proposed |
| ADR-003 | Enrichment 形态 | 规则 fast lane + semantic slow lane | Proposed |
| ADR-004 | Content 抽象 | 来源无关核心 + YouTube adapter | Proposed |
| ADR-005 | Learning 状态 | immutable behavior facts + profile projection | Proposed |
| ADR-006 | Learning 一致性 | 显式意图同步、隐式信号异步 | Proposed |
| ADR-007 | AI 依赖 | provider-independent Semantic port | Proposed |
| ADR-008 | 异步基础设施 | PostgreSQL durable handoff；Redis 仅 cache/hot state | Proposed |
| ADR-009 | 边界治理 | 机器可读依赖清单 + Architecture/Contract Gate | Proposed |
| ADR-010 | 工程技术栈 | Java 25 LTS + Gradle 9.7.x 后端；TypeScript + Node 24 LTS Extension；Python 3.12 工具链 | Proposed |

## ADR-001：采用 Modular Monolith

**状态：Proposed**

### Context

产品从一个 Chrome Extension 和一个后端开始，但 Domain 已包含 Identity、Content、Lexicon、Vocabulary Profile、Learning、Semantic 与 Enrichment。未来来源和客户端会扩展；当前没有足够吞吐、独立团队或隔离数据证明需要微服务。

### Decision

采用一个代码库、一个版本体系内的 Modular Monolith。每个 Bounded Context 拥有公开 API、内部 Domain/Application 与 outbound port。跨 Context 只能使用公开 API；数据库、缓存和 provider 通过 adapter 接入。物理构建模块可渐进拆分，但逻辑依赖规则和测试从第一天生效。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 简单分层单体 | 初始化最快、文件少。 | Domain owner 容易退化为 controller/service/repository 横向调用，数百任务并行后冲突和隐式耦合难以控制。 |
| **Modular Monolith** | 单进程事务和调试简单；Domain 清晰；未来可按真实热点拆分。 | 需要维护公开面、依赖清单和架构测试；部署仍有共同版本节奏。 |
| 微服务 | 独立发布和故障隔离强。 | 过早引入网络契约、分布式事务、服务发现和运维成本，当前 workload 无法证明收益。 |

### Consequences

- Must：模块依赖无环；Domain 禁止基础设施 import；只有 bootstrap 装配实现。
- Should：每个 Context 的公开能力可通过 contract fixture 独立验证。
- Later：当吞吐、数据隔离、发布频率或团队 owner 连续显示边界压力时，优先抽出已有公开 API 最稳定的 Context。

### Revisit trigger

单个 Context 需要独立扩容且长期占主要资源、发布耦合持续阻塞多个 owner，或故障隔离 SLO 无法由 `api`/`worker` 进程切分满足。

## ADR-002：同代码库提供 `api` 与 `worker` 两个运行入口

**状态：Proposed**

### Context

字幕 fast path 需要稳定低延迟；模型调用、预取、Learning projection 与 replay 具有高延迟、批处理和可重试特征。把它们都放在 API 请求线程会把 provider 长尾传给用户。

### Decision

构建 `bootstrap-api` 与 `bootstrap-worker` 两个 composition root。两者共享相同 Domain/Application 版本，独立运行、扩容和重启；通过 durable handoff 交接异步工作。Worker 经 Enrichment 公开能力写入 Enrichment-owned durable annotation result，`Client Delivery / Sync` 应用模块再关联投递；Worker 不回调原始 API 请求对象。两个入口是 deployable runtime，不是独立业务服务。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 单进程内线程池 | 本地启动最简单，调用开销低。 | worker 堵塞、内存和崩溃会影响 API；扩容比例绑定。 |
| **两个入口、同一单体** | 延迟/故障边界清晰；不增加内部网络 API；共享代码和事务语义。 | 需要 durable job 协调、版本兼容和两个 runtime 的运维。 |
| 独立微服务 | 可独立技术栈和发布。 | 增加网络失败、重复契约和分布式诊断，初期收益不足。 |

### Consequences

API 不等待 worker completion；worker 不拥有独立 Domain 模型；composition root 中禁止业务条件分支。结果生成归 Enrichment，结果投递归 `Client Delivery / Sync`，两个 owner 不得通过直接读写对方表耦合。

### Revisit trigger

worker 与 API 必须使用不同发布周期或技术栈，且公共 Application 模块已无法承载兼容边界。

## ADR-003：采用规则 fast lane 与 semantic slow lane

**状态：Proposed**

### Context

用户要求英文立即显示、提示少而准、翻译结合语境。纯 LLM 字幕翻译会阻塞并产生过量提示；纯词典又无法可靠处理多义词、短语和术语。

### Decision

Enrichment 分两段：

1. fast lane 同步执行 normalization、phrase/candidate detection、Lexicon/Profile/cache 查询和 need-hint rules；形成确定性或已缓存 annotation 与 slow work intent。
2. 存在 slow work 时，Workflow Application 先提交 durable handoff。API 只有在 commit 确认后才暴露 fast result + pending；enqueue 失败时返回 fast result + no-pending 安全降级，不承诺虚构工作。
3. 已提交的 semantic work 由 Worker 执行，调用 Semantic Provider 后经 Enrichment 再次执行展示策略并持久化 annotation result；`Client Delivery / Sync` 通过公开 contract 关联投递增量结果。

English first paint 始终位于客户端，并早于两段后端结果。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 每句阻塞调用 LLM | 实现直观，语境能力集中。 | P95/P99 受 provider 控制；成本高；无模型即无字幕辅助；容易变成整句翻译。 |
| 纯本地规则/词典 | 快、稳定、便宜。 | 多义词、idiom、技术语境和自然中文质量受限。 |
| **Hybrid fast/slow lane** | 英文和 fast hint 稳定；模型只处理难例；可预取和缓存。 | 要处理增量结果、late result、版本和双阶段测试。 |

### Consequences

- 模型只贡献语义证据，不能单独决定展示。
- pending 是 durable handoff 承诺，不是候选生成或内存 enqueue 状态。
- Enrichment 拥有 generated/persisted annotation result，`Client Delivery / Sync` 拥有 delivered/status；客户端实际可见渲染后才产生 `HintDisplayed`，用户交互后才产生 `HintClicked`。
- 客户端必须支持 correlation、revision、去重和 stale result 拒绝。
- 必须按阶段统计延迟和降级原因，不能只报一次总耗时。

### Revisit trigger

受控压测证明某类小模型在目标部署下有稳定、可负担的严格 deadline，且同步接入不会影响 fast path P99；届时可只为该任务增加有界同步路由。

## ADR-004：Content 核心来源无关，YouTube 是 adapter

**状态：Proposed**

### Context

YouTube 是第一个入口，未来还要支持网页、PDF、Podcast 与其他视频平台。如果核心模型持有 DOM selector、player API 或平台 caption 格式，后续来源会复制全部 Learning/Enrichment 流程。

### Decision

Content Context 只表达来源无关的内容、片段、caption、时间范围、播放位置和有界上下文。YouTube DOM/API 解析保留在 Extension source adapter；后端 source adapter 只负责映射为 Content 的公开语义。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| YouTube-first 核心模型 | 首个 demo 更快。 | 平台概念渗透缓存、事件和 profile；第二来源会造成大规模重构。 |
| **Canonical Content + adapter** | 一条 Enrichment/Learning pipeline 服务全部来源；测试可用通用 fixture。 | 首期需定义规范化边界，并处理来源信息缺失。 |
| 每个来源独立 pipeline | 可完全定制。 | 重复规则、profile 和语义逻辑，跨来源学习状态难共享。 |

### Consequences

Content Context 接受缺失和乱序；YouTube 私有类型不得越过 adapter。来源特殊能力通过可选 capability 表达，不能进入共享核心的必填假设。

### Revisit trigger

至少两个来源证明无法用同一 Content/Segment/Context 语义表达，并且差异会改变核心学习含义，而不只是 adapter 获取方式。

## ADR-005：Learning 保存不可变事实，Vocabulary Profile 是投影

**状态：Proposed**

### Context

用户掌握度会随实际展示、点击、暂停、重播和显式标记变化。生成但未交付、交付但 stale、缓存但未显示的 hint 不算用户暴露。算法会迭代；只保存最终 familiarity 无法解释、回放或重新计算。对整个系统使用 Event Sourcing 又超出必要范围。

### Decision

Learning append-first 保存不可变行为事实，使用版本化规则生成 evidence；Vocabulary Profile 幂等应用 evidence，形成当前可查询投影。Identity、Content、Lexicon 等其他 Context 继续使用普通事务状态。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 直接 CRUD familiarity | 简单、读取快。 | 丢失因果与算法版本，无法可信重算；客户端容易成为权威。 |
| **Learning facts + profile projection** | 保留解释和重放能力；查询仍高效；复杂度局限在学习链路。 | 需要幂等、checkpoint、projection 版本和 eventual consistency。 |
| 全系统 Event Sourcing | 所有状态可重放。 | Identity/Content 等无必要 Context 也承担事件版本、投影和迁移成本。 |

### Consequences

Learning 是原始行为 owner，Vocabulary 是当前掌握状态 owner；Profile 不能反向成为原始历史。`HintDisplayed` 只在客户端把 annotation 实际提交到可见 overlay 后上报，`HintClicked` 必须引用对应 displayed/result identity。generated/delivered 生命周期状态不能替代 Learning 的 displayed/clicked 事实。事件 schema 和重放策略在 Phase 2 设计。

### Revisit trigger

事件量、保留成本或隐私要求使完整历史不可持续；届时设计可验证的压缩/保留策略，而不是静默丢弃解释性。

## ADR-006：显式意图同步投影，隐式信号异步投影

**状态：Proposed**

### Context

用户点击“认识”后希望立即不再提示；`HintDisplayed`、暂停、重播等高频弱信号可以稍后批量处理。全部同步会放大写延迟，全部异步会违背强用户意图。

### Decision

所有事件先 durable commit。显式 `MarkedKnown/MarkedUnknown` 在同一 use case 中同步产生 evidence 并推进 profile version；只有事件提交和投影成功后，单次最终响应才确认 accepted 与新 version。若投影失败，事件保持 pending 由 worker 修复，同一最终响应明确说明 event accepted + projection pending，不能先发送 durable ACK 再发送 version ACK。隐式事件由 worker 批量归约并最终一致更新 profile。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 全同步 projection | 一致性简单。 | 高频行为增加 API P99 和锁竞争，失败耦合更大。 |
| **按意图强度分层** | 强意图 read-your-writes；弱信号可批处理。 | 两条路径需要共享同一 reducer/idempotency 语义并验证结果等价。 |
| 全异步 projection | API 最轻。 | 用户刚标记认识仍可能马上看到提示，体验不可信。 |

### Consequences

同步与异步路径不能复制 scoring 逻辑；worker 重放显式事件时必须识别已应用 evidence。API 对“事件已保存但 projection pending”需要明确状态语义，并只给出一次最终响应；pending 不是 read-your-writes 成功。

[Lifecycle guarantees](phase-1-lifecycle-guarantees.md#learning-顺序与显式冲突) 比较客户端时钟、纯服务端接收顺序和意图 revision 条件接受三种冲突方案，推荐第三种。Learning-owned accepted-intent revision 独立于 projection version；陈旧动作不能静默反转新意图。该补充仍为 Proposed，精确 schema、事务和评分属于后续阶段。

### Revisit trigger

真实交互证明短暂 eventual consistency 可接受，或同步 projection 无法达到 API SLO；也可考虑以严格 version overlay 实现相同 read-your-writes。

## ADR-007：业务只依赖 Semantic port

**状态：Proposed**

### Context

OpenAI、Gemini、Qwen、本地模型和未来 provider 在能力、价格、延迟和接口上都不同。phrase detection、简单消歧与困难解释也未必使用同一模型。

### Decision

由 `semantic-api` 定义任务级能力、标准错误、deadline、置信与可观察元数据。`semantic-routing` 根据任务和策略选择实现；provider SDK 只存在于 `semantic-providers` adapter。Domain 不接收 provider 原始响应。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 业务代码直接调用一家 SDK | 首个调用最快。 | 错误、缓存、测试和数据模型被供应商锁定；替换成本散落全仓。 |
| 通用“文本进、文本出”接口 | 表面统一。 | 丢失任务语义、结构校验、deadline 和可比较指标，容易形成新的泄漏抽象。 |
| **任务级 Semantic port** | 可按任务路由和测试；标准化失败与结果；隔离 SDK。 | 需要维护能力交集和 provider-specific adapter。 |

### Consequences

port 表达 `disambiguate`、`translateInContext`、`extractPhrase`、`explainSentence` 等业务能力，但本阶段不定义具体方法签名或 Prompt。fake provider 是 Contract/Journey 测试的一等实现。

### Revisit trigger

多个 provider 长期无法映射到稳定任务语义；此时按 capability 拆 port，而不是把 provider 类型暴露给 Enrichment。

## ADR-008：初期使用 PostgreSQL durable handoff，Redis 只做可丢弃状态

**状态：Proposed**

### Context

系统需要在 API commit 后可靠交给 worker，且必须支持至少一次投递、重试和诊断。初期没有 Kafka 级吞吐证据；Redis 被明确定位为 cache/hot state，不能因 flush 丢失 Learning 或 semantic work。

### Decision

使用与权威业务写入一致的 PostgreSQL outbox/job 边界承载异步 handoff，worker 幂等消费。caption fast result 的 pending 仅在 handoff durable commit 后返回；enqueue 失败明确 no-pending。Worker 完成后由 Enrichment 持久化结果，再经 `Client Delivery / Sync` 投递；Redis 只承担 cache、短期去抖和可恢复协调，不是唯一工作队列。具体 schema、claim 与 retry 算法在 Phase 2/7 决定。

[Lifecycle guarantees](phase-1-lifecycle-guarantees.md#durable-work取消与租约) 固定总 deadline/有限预算、durable 提交资格、fencing、取消与完成竞争、提交确认未知及投递 ACK 的架构保证。Provider 晚到不承诺停止计算，也不能恢复已终止工作。具体 lease/退避数值仍由 Phase 2/7 选择，ADR 状态仍为 Proposed。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 内存队列 | 零外部依赖。 | 进程退出即丢任务，无法支持可靠 Learning。 |
| Redis queue/stream 作为唯一来源 | 延迟低、实现成熟。 | 与 PostgreSQL 业务 commit 存在双写边界；cache 运维动作可能影响 durable work。 |
| **PostgreSQL durable handoff** | 事务一致、组件少、可审计，适合初期规模。 | 高频 claim 会给数据库增加负载，需要索引、批处理和清理。 |
| Kafka 等专用平台 | 高吞吐、分区和 replay 强。 | 运维、协议和本地开发成本高，当前无 workload 依据。 |

### Consequences

API 只在 durable commit 后承诺 semantic pending 或确认 Learning event intake；显式行为还须等待同步 projection 成功或 pending 结论，形成单次最终响应。Worker 以 at-least-once 假设实现。Redis 全量丢失必须进入 resilience Gate。

### Revisit trigger

可测量的数据库 contention、queue lag 或保留需求达到 PostgreSQL 方案上限，且优化索引/批量/分区后仍无法满足 SLO。

## ADR-009：用机器可读清单和 Gate 固定边界

**状态：Proposed**

### Context

项目预计拆成数百个子功能和大量 Sub-Agent 调用。只写人类文档无法防止跨模块 import、跳过验证、写范围冲突和 adapter 泄漏。参考仓库已经证明依赖 allowlist、forbidden import、统一 Gate 和任务 handoff 能把边界变成可验收事实。

### Decision

工程初始化时建立：

- 唯一机器可读模块清单，声明 package owner、allowed project dependencies 与 forbidden imports；
- Architecture、Contract、Journey 和 Failure Injection 验收目录；
- 单一 Gate CLI/脚本入口，显式选择 incremental/full；
- 不可覆盖的每次运行证据；
- Sub-Agent handoff schema 与 `PASS/FAIL/BLOCKED` 结果语义。

客户端特定 Agent 配置只引用共享规则，不复制规则正文。主 Agent 不把 Sub-Agent 退出成功直接视作质量 `PASS`。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| 仅文档约定 | 初期成本最低。 | 规则随 Agent/开发者理解漂移，无法自动定位违例。 |
| **Manifest + executable Gates** | 依赖和验收可重复；适合并行委派；失败有证据。 | 需要维护清单、trigger 和测试速度。 |
| 每个平台复制完整规则 | 平台内读取方便。 | 多真源漂移，修改需跨目录同步，难判断权威版本。 |

### Consequences

- required Gate 未运行、skipped 或 unavailable 均不能报告为 `PASS`。
- Java 格式、静态分析、注释、模块和类依赖、测试及覆盖率规则只由 Gradle/Java 工具链执行；Python Gate 只做选择、编排、证据和收据治理，不实现同义 Java 源码扫描。
- `TASK_VALIDATION` 是交付命令的唯一执行层；`INDEPENDENT_REVIEW` 只复核冻结产物和 validation receipt，`CATALOG_DECISION` 只验证 receipt/dependency/hash DAG。后两层不得再次启动交付 checker。
- 原子 Task 保留独立 DAG/outcome evidence；Codex 委派以稳定 `work_package_id` 和精确 `task_ids[]` 聚合同 owner、同 contract/写入边界且总预计至少 120 分钟的工作，避免为小步骤重复启动 Agent。
- 并行实现任务必须使用不重叠写范围；接口/manifest/composition root 集成由唯一 owner 串行完成。
- 主 Session 优先接收精简完成回调，不做高频状态轮询；首次兜底检查不得早于五分钟，后续不得频于十分钟。

### Revisit trigger

只有当清单或 Gate 的维护成本持续高于它捕获的真实缺陷，并有替代的同等可执行控制时才简化；不能退回纯口头约定。

## ADR-010：采用 JVM 后端、TypeScript Extension 与独立 Python 工具链

**状态：Proposed**

### Context

LexiFlow 后端要维护清晰的模块公开面、事务内 durable handoff、可重放 Learning 流与长期数据迁移；Chrome Extension 必须直接面对 DOM、Manifest V3 与浏览器生命周期。参考仓库的 Java 多模块、Gradle Wrapper、依赖锁和 Architecture Test 路径已经可复用，但不能因此把浏览器代码或工程工具也强行放进 JVM。仓库已按用户指定的 Java 方向建立产品构建基础；`LF-TSK-OPS-0001` 仍须通过 current-input 目录验收，才能正式冻结 runtime、build、lock 和物理目录方向。

### Decision

若用户接受 G1，Phase 2 的 data-model 工程骨架按以下技术基线展开；具体 patch 版本在创建 wrapper/lockfile 当天通过官方稳定版本重新核验并固定：

- 后端：Java 25 LTS；Spring Boot 4.x 作为 HTTP、configuration 与 runtime adapter；核心 Domain 保持 plain Java，不依赖 Spring annotation。只有测量证明需要 reactive backpressure 的 adapter 才引入 Reactor，普通 API 默认使用同步 servlet/virtual-thread 友好的调用模型。
- 构建：Gradle 9.7.x Wrapper、Kotlin DSL、Java Toolchain 25、version catalog 与 dependency locking。多项目物理模块对应稳定逻辑 owner；build logic 集中管理质量规则，不由每个模块复制。
- Extension：Manifest V3 + TypeScript；Node 24 LTS 仅用于构建、lint、test 和打包，不作为后端运行时。首次创建 Extension lockfile 时使用当日稳定的 TypeScript 6.x patch 并精确锁定；content script、service worker、overlay UI 和共享 contract 分包，并按 MV3 的可终止 service worker 设计可恢复状态。
- 工程工具：Python 3.12，继续只承载 Harness、Gate、生成器和审计脚本；不承载产品 Domain。3.12 已进入 security-only，本选择只用于在 G1 期间保持现有 Harness 的已验证解释器基线，并不承诺把它沿用到 EOL；升级阈值见下文版本治理。
- 目录：`backend/`、`clients/chrome-extension/`、`contracts/`、`tests/`、`scripts/`、`harness/`、`openspec/`、`planning/`、`docs/`。后续 Phase 再创建具体模块，不在本 ADR 提前生成代码或 API。

### Alternatives and trade-off

| 方案 | 优点 | 代价/风险 |
|---|---|---|
| **Java 25 + Spring Boot + Gradle；Extension TypeScript** | 强类型模块边界、成熟事务/数据库生态；可直接继承参考仓库的 Gradle/Architecture Test 经验；浏览器端保持原生生态。 | 两套产品语言与构建工具；Spring 需要通过 package/module tests 防止进入 Domain；系统默认 Java 26 与产品 Temurin 25 必须由统一 launcher 隔离。 |
| 后端与 Extension 全 TypeScript | 共享语言、DTO 工具和上手速度好；Node 异步 I/O 适合 API。 | 后端 Domain 边界更依赖 lint/约定；事务、projection 与大量 agent 并行修改时，需要额外工程规则才能获得同等级隔离。 |
| Kotlin + Spring Boot；Extension TypeScript | JVM 生态与简洁建模兼得，null-safety 强。 | 引入 Java/Kotlin/TypeScript 三种语境；编译和 build logic 更复杂；与参考仓库直接复用的源级模式较少。 |

### Current evidence for version selection

- Oracle 当前支持路线把 Java 25 列为 LTS；Gradle 兼容矩阵证明 Java 25 的 toolchain 与运行支持起点都是 9.1.0，这只回答“最低兼容版本”，不等于实施版本。本次评审时 Gradle 官方 releases 将 9.7.1 列为最新稳定版，因此 ADR 选择 9.7.x release line；批准实施时再次选择当日稳定 patch，并用 Gradle 官方 checksum 固定 distribution ZIP 和 Wrapper JAR，而不是跟随开发机的非 LTS Java 26。[Oracle Java SE Support Roadmap](https://www.oracle.com/ae/java/technologies/java-se-support-roadmap.html)；[Gradle compatibility matrix](https://docs.gradle.org/current/userguide/compatibility.html)；[Gradle releases](https://gradle.org/releases/)；[Gradle release checksums](https://gradle.org/release-checksums/)
- Gradle 的 multi-project dependency 会同时约束 classpath 与 build order，dependency locking 会校验解析结果，适合把逻辑依赖清单落实到物理构建。[Gradle multi-project builds](https://docs.gradle.org/current/userguide/multi_project_builds.html)；[Gradle dependency locking](https://docs.gradle.org/current/userguide/dependency_locking.html)
- Spring Boot 4.1 当前支持 Java 17–26 和 Gradle 8.14+/9.x；Spring Modulith 提供模块结构验证，但先保留为 Later，避免框架 annotation 成为 Domain 边界真源。[Spring Boot system requirements](https://docs.spring.io/spring-boot/system-requirements.html)；[Spring Modulith fundamentals](https://docs.spring.io/spring-modulith/reference/fundamentals.html)
- Node 官方建议生产应用选择 Active/Maintenance LTS；截至本次评审 Node 24 为 LTS。Chrome Manifest V3 使用按需 service worker，并禁止远程托管代码，因此 Extension 必须持久化可恢复状态并把可执行代码纳入包内。[Node.js releases](https://nodejs.org/en/about/previous-releases)；[Chrome Manifest V3](https://developer.chrome.com/docs/extensions/develop/migrate/what-is-mv3)
- TypeScript 6.0 是本次评审时的稳定发布线，并明确包含面向 7.0 的 breaking-change/deprecation 迁移说明；因此首次 lock 精确固定 6.x patch，7.x 不得作为自动依赖更新进入。[TypeScript 6.0 release notes](https://www.typescriptlang.org/docs/handbook/release-notes/typescript-6-0.html)
- Python 官方生命周期表显示 3.12 仅接收安全修复并于 2028-10 EOL；3.12 的后续安全发布为不定期 source-only，官方已停止提供 binary installer。LexiFlow 暂留 3.12 是为了保持现有 Phase 1 Harness 的解释器基线、缩小 G1 同时变更面，且 Python 不承载产品 Domain；这也意味着干净机器安装能力必须成为批准后复现检查，而不能假定本机 pyenv 缓存存在。[Python version status](https://devguide.python.org/versions/)；[Python 3.12 security-only release note](https://www.python.org/downloads/release/python-31214/)

### Version review and drift policy

- `LF-WS-OPS` 是 runtime、Wrapper 和 lockfile 的唯一 owner；`LF-WS-QLT` 独立执行 Gate 并保存 receipt。每次 Phase Gate、每次 release candidate，以及距上次复核 30 天时，重新检查上述官方生命周期和兼容矩阵。
- 创建工程骨架当天只从仍受支持的 release line 选择稳定 patch，并把精确版本写入 Gradle Wrapper、version catalog/dependency lock、Node runtime pin、`package.json`/lockfile、Python runtime pin 与 Python dependency lock。范围符号或开发机全局版本不得替代这些真源。
- 安全公告要求升级时立即建立独立 OPS 任务；同 release line 的 patch 仍须通过 clean build、产品测试和 Architecture Test。Java、Gradle、Spring Boot、Node、TypeScript 或 Python 的 major/minor 变化必须先建立 OpenSpec change，重新核对兼容矩阵、breaking changes、部署环境和回滚证据。
- Python 3.12 在以下任一条件先到时升级到当时仍处于 bugfix support 的稳定版本：无法在受支持平台从声明的来源建立干净环境；关键依赖停止支持 3.12；安全修复无法及时获得；进入 EOL 前 12 个月；或 Python 工具开始成为对外发布产物。升级不得晚于 2027-10 的复核窗口。
- 升级失败时回退到上一组已通过 Gate 的 Wrapper、runtime pin 和完整 lockfiles，并从上一可重现构建产物恢复；不得只回退单个直接依赖而保留未知的 transitive graph。

### Consequences

- Must：产品依赖使用 wrapper 与锁文件；不得依赖开发机全局 Gradle/Node 包。Java toolchain 固定为 25；确定性 launcher 必须拒绝 Java 26，系统默认 Java 不能成为产品构建证据。
- Must：Spring、PostgreSQL、Redis、HTTP、provider SDK 只进入 adapter/composition root；Domain/Application 的边界由 Gradle project dependency 与 Architecture Test 双重约束。
- Must：Extension 打包不包含远程托管代码；content script 输入按不可信网页数据处理；service worker 不保存无法恢复的唯一状态。
- Should：后端和 Extension 各自有快速定向测试，跨端 contract fixture 由 `contracts/` owner 串行维护；仓库 Gate 统一聚合，但不把两个构建系统耦合成隐式魔法。
- Later：是否采用 Spring Modulith、jOOQ/Flyway、具体测试库、前端 bundler 和 monorepo package manager，在相应 Phase 用 workload 与维护成本决定；本 ADR 不提前冻结。

批准后的具体创建顺序、拟执行命令和证据产物见 [Post-approval toolchain verification](../development/post-approval-toolchain-verification.md)。该清单目前是计划，未执行，也不构成 Wrapper、lockfile、clean build 或 Architecture Test 已通过的证据。

### Revisit trigger

Java 25/Gradle/Spring Boot 组合无法在目标部署和团队环境稳定复现；后端交付速度长期被跨语言 contract 成本主导；或真实 profiling 表明所选 runtime 无法满足延迟、内存或冷启动目标。发生时先保留 Domain contract 与数据所有权，再替换 runtime/adapter。

## 评审记录模板

人工评审后，在本页只更新对应 ADR 的状态和以下记录，不在历史段落中静默改写已接受的理由：

| 项目 | 内容 |
|---|---|
| Decision ID | `ADR-...` |
| Review date | 待填写 |
| Reviewer | 待填写 |
| Result | Accepted / Rejected / Superseded |
| Conditions | 若有，写进入下一 Phase 前的约束 |
| Supersedes / Superseded by | 若有，链接新 ADR |

完整 Domain、数据流、同步/异步边界和验收合同见 [phase-1.md](phase-1.md)。
