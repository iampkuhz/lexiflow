# G1 / Phase 1 架构决策包

> Change：`establish-lexiflow-foundation`；目录任务：`LF-TSK-ARCH-0008`；评审项：`LF-DECISION-P1-001`。
> 截止 2026-09-16：**待可执行 Gate 与用户决定**。本文是可评审的提案，不是 G1 `PASS` receipt；十条 ADR 仍为 `Proposed`。

## 决策背景与推荐

LexiFlow 的首个闭环是 Chrome Extension 在 YouTube 英文字幕上提供少量语境化中文提示。英文必须先显示，不能被整句中文翻译、网络请求或模型等待取代。服务端持有跨设备 Vocabulary Profile；YouTube 只是第一个来源。[产品 Brief](../product/product-brief.md) 和 [Phase 1 架构](../architecture/phase-1.md) 已把这条产品边界、七个 Bounded Context、两条完整流程与同步/异步边界写成提案；[用户需求追踪表](phase-1-user-requirement-traceability.md) 把附件中的六项 Phase 1 输出及主要产品约束逐项映射到当前证据；[Phase 1 横切合同](../architecture/phase-1-cross-cutting-contracts.md) 进一步固定 Semantic port/result、缓存、威胁、可观测性和来源适配的架构语义；[Product Architecture Spec](../../openspec/specs/product-architecture/spec.md) 固定长期行为约束。

推荐整体接受下列架构方向，并把本页数值目标仅作为下一阶段 spike 的初始实验阈值。若有一项不接受，应指出要替换的 ADR、原因和必须保持的产品不变量，再修订对应设计与 Gate 输入；不能用口头例外绕过英文优先、权威状态和真实验收。每条方案的完整理由、后果与复审触发器见 [ADR 记录](../architecture/decisions.md)。

| 要确认的选择 | 推荐项 | 主要替代方案与取舍 |
|---|---|---|
| **ADR-001 / ADR-002：系统与运行入口** | 强 Domain 边界的 Modular Monolith；同一代码库、同一业务版本的 `api` 与 `worker` 两个组合根。 | 简单分层单体启动更快，却难阻止横向读表与 owner 漂移；微服务或独立 worker 服务可独立发布，却提前引入网络故障和分布式运维。两个入口增加 durable job/版本协调，但隔离模型长尾与 API 请求线程。 |
| **ADR-003：字幕增强** | 客户端立即显示英文；规则 fast lane 决定是否提示，semantic slow lane 仅处理语境难例，完成后增量投递。 | 每句同步调用 LLM 直观但把 provider P99 和成本压到观看路径；纯词典快而廉价，却难处理短语、多义词和术语。Hybrid 必须处理版本、晚到和双阶段测试。 |
| **ADR-004：内容来源** | `Content Context` 来源无关，YouTube DOM/播放器细节停在 Extension source adapter。 | YouTube 特化核心可更快做首个 demo，但 PDF、Podcast 等后续来源要复制或重构 Enrichment/Learning；统一核心需从首期处理缺失和乱序上下文。 |
| **ADR-005 / ADR-006：学习事实与一致性** | Learning 保存不可变行为事实并生成版本化 evidence；Vocabulary Profile 是可解释投影。显式 known/unknown 在同一 use case 中尝试同步投影，隐式信号异步处理。 | 直接改 familiarity 最简单，却丢失因果和重算能力；全系统 Event Sourcing 成本过高。全同步增加高频行为写入 P99；全异步使用户刚标记“认识”后仍可能立即看到提示。 |
| **ADR-007：模型边界** | 业务只依赖任务级 Semantic port；具体 Provider 由 adapter 实现。 | 直连单一 SDK 首次调用更快，但测试、错误和替换成本散落 Domain；通用文本输入输出接口又丢失任务、deadline 与可比较结果语义。 |
| **ADR-008：事实与异步交接** | PostgreSQL 保存事实及 durable outbox/job；Redis、浏览器 L1 与投影可重建。Fast result 只在 handoff commit 后声称 `pending`，失败时明确 `no-pending`。 | 内存或 Redis 唯一队列实现更轻/低延迟，却丢任务或跨 PostgreSQL commit 形成双写风险；Kafka 提供高吞吐，但当前没有 workload 支撑其运维成本。PostgreSQL claim/清理负担需要后续压测。 |
| **ADR-009：工程边界** | 机器可读 owner/依赖清单、单一显式 Gate、不可覆盖的 run evidence 与分层 Agent handoff。 | 纯文档成本低但无法重复捕获跨 Context import、写范围冲突和跳检；多客户端复制规则会产生多真源。清单与 Gate 有维护成本，应以捕获的真实缺陷复审。 |
| **ADR-010：技术栈与构建** | Java 25 LTS + Spring Boot 4.x + Gradle 9.7.x 后端；Manifest V3 + TypeScript + Node 24 LTS Extension；Python 3.12 工具链。 | 全 TypeScript 可共享语言但后端模块/事务边界需要更多治理；Kotlin + Spring 建模简洁但增加第三种语言语境。推荐方案可复用参考仓库的 JVM 模块与 Architecture Test 经验，代价是双产品语言。仓库现已锁定 Temurin 25；确定性 launcher 只接受产品 Java 25。 |

**P0 技术选择及批准后复现计划已经形成提案并通过独立文档复核，但还没有完成目录验收。** [ADR-010](../architecture/decisions.md#adr-010采用-jvm-后端typescript-extension-与独立-python-工具链) 已为 [目录任务 `LF-TSK-OPS-0001`](../../planning/workstreams.yaml) 比较后端/Extension runtime、构建工具、依赖锁与仓库布局，并给出推荐；[批准后复现清单](../development/post-approval-toolchain-verification.md) 定义多项目严格锁、精确 runtime pin、错误 runtime/缺 lock 负例、干净双环境重建及证据产物。仓库现已具备固定到 Temurin 25 的确定性 launcher、Gradle Wrapper、严格依赖锁、Java 质量工具和 Architecture Test，本机 clean build 已通过；current-input Gate receipt 仍未完成；独立干净环境复现属于批准后的后续验收。它仍是 `LF-TSK-ARCH-0008` 的 hard prerequisite：在 `OPS-0001` 获得正式目录 receipt 前，G1 不能直接 `PASS`，用户对系统形态的认可也不能自动代表工具链已完成目录验收。

上述选择还包含三个需要一起接受的 owner 规则：Global Lexicon 保存共享 lexical facts，Vocabulary Profile 保存个人状态，Enrichment 使用二者和当前语境生成 durable annotation result；`Client Delivery / Sync` 只拥有投递状态；`generated`、`delivered`、`displayed`、`clicked` 是不同事实，只有当前可见渲染才能上报 `HintDisplayed`。跨 Context 仅通过公开 contract，Domain 不依赖 HTTP、PostgreSQL、Redis、Chrome 或具体模型 Provider。[模块边界清单](../../harness/module-boundaries.yaml) 已表达这条推荐依赖方向，生产代码中的执行证据仍属于后续阶段。

## 初始 SLO 与预算假设

下表摘录 [Phase 1 检查表](phase-1-checklist.md) 中的初始阈值，**尚未测量、尚未承诺**。评审应确认这些阈值是否适合作为首个 spike 的目标，以及应在哪类机器、网络、字幕样本和标注集上测量。平均值不能代替 P95/P99；客户端首屏与后端、queue、Provider、投递必须分段统计。

| 指标 | 初始目标 / 预算 | 验证与调整依据 |
|---|---|---|
| 英文 caption 到可见 | 本地路径 P95 ≤ 50 ms；不包含后端/模型等待。 | Extension spike 测播放器字幕、overlay 和长视频样本；即使全部增强失败，英文仍可见。 |
| 浏览器 L1 annotation | P95 ≤ 20 ms。 | 测版本校验、缓存命中与实际渲染，不能把命中 payload 当作 displayed。 |
| 后端确定性 annotation | P95 ≤ 250 ms、P99 ≤ 500 ms。 | 在 cache hit/miss、Profile 查询与 Redis 故障样本中分段测量；Provider cache miss 不进入必需 fast path。 |
| 异步语义 annotation | P95 ≤ 2.5 s；超过 deadline 静默降级。 | 分开测 durable handoff、queue wait、Provider、结果持久化及投递；延迟结果必须拒绝 stale caption/profile。 |
| 跨设备学习收敛 | 显式 known/unknown 在其他活动设备 5 s 内生效；被动事件 60 s 内影响 Profile。 | 显式动作同会话 read-your-writes 优先；多设备/离线/重复与 projection failure 需独立验证。 |
| 提示质量 | MVP 标注集 hint span precision ≥ 85%；contextual hint 人工可接受率 ≥ 80%。 | 固定人工标注集并同时观察 recall、提示密度、闪烁和打断感；不能只优化 precision。 |

成本与交互预算目前只有约束，**没有可证实的数值上限**：模型调用不得随每句 caption 无条件发生；规则、Lexicon、Profile 与兼容缓存先过滤候选；每次任务有 deadline，客户端在超时后不等待；默认日志不保存完整字幕、模型输入或个人词汇历史。单位观看小时的 Provider 成本、cache hit、每句提示数量和长期事件保留费用须由真实观看样本测量，再由产品 owner 设上限。现在给出金额或每句固定 hint 数会制造未经验证的承诺。[Product Brief 的待验证证据](../product/product-brief.md#product-evidence-to-establish) 已列出相关实验。

实施调度的容量预算是规划包络而非 G1 通过条件：[任务目录](../../planning/workstreams.yaml) 当前有 18 个 workstream、41 个 capability、113 个原子 task；8 个 Gate control-plane Task 已从评审通过的 JIT draft 激活，fully-expanded 目标仍是 375 个原子任务，剩余容量 262 个，约 1,500–3,000 次任务级设计、实现、测试、复核、Gate 或返工证据 run。这不是 Sub-Agent 会话数；同一 owner/contract/写入边界内的连续 Task 应合并为 2–6 小时工作包。单 Qoder run、完成回调优先、首次 300 秒/后续 600 秒兜底限制会约束吞吐；任务数量不能代替独立验收。[Agent Execution Spec](../../openspec/specs/agent-execution/spec.md) 固定了这些调度与结果语义。

## 当前证据与尚未完成的 Gate

| 已有证据 | 它能证明什么 | 它不能证明什么 |
|---|---|---|
| [Phase 1 架构](../architecture/phase-1.md) 与 [ADR](../architecture/decisions.md) | Domain owner、模块/依赖提案、caption→annotation 与 behavior→Profile 流、失败与一致性边界已可评审。 | 没有产品代码和端到端测量，不能证明后续行为测试或 SLO 达标。 |
| [Phase 1 横切合同](../architecture/phase-1-cross-cutting-contracts.md) | 四类 Semantic capability 与标准结果、各缓存层的正确性/隐私、信任边界、telemetry redaction 和跨来源 adapter conformance 已通过独立文档复核。 | 当前仍是 `Proposed` 文档证据；没有后续 schema、代码、fixture 和 Gate receipt，不能把对应目录任务直接记为 `PASS`。 |
| [Phase 1 当前状态](../roadmap/phase-1-status.md) 记录的独立语义复核、规划交叉验证、runner 合成测试与 Gate bootstrap 实现证据 | 架构文档与 task/handoff 底座经过当前阶段静态复核；planning validator 对 113 个 Task 执行 11 项检查并通过。Runner 保持单 Qoder 约束并 ACK 匹配的终态 run。前三轮完整集成复核依次发现 formal-root/PATH/source、UUIDv7 provenance 与 19 个 profile Task 的 read-only review plan 不可达。Main Agent 已完成前两组修复，并让 generic evidence packet 以严格 empty snapshot/diff/changed-files 表达零写入；planner 对 validation 仍要求非空，对 review/catalog 才允许空 write-set。 | 正式 CLI 在缺少可信 evidence/issuer context 时按设计 fail closed；零写入修复后的 frozen-input re-review 与 current-input receipt chain 尚未完成。这些仍是 non-READY bootstrap evidence，测试、退出 0、回调或 ACK 都不能替代 integrated review、catalog receipt 和 G1 用户决定。 |
| [OpenSpec change checklist](../../openspec/changes/establish-lexiflow-foundation/tasks.md) 与 [G1 规划](../../planning/workstreams.yaml) | `LF-GATE-001` 覆盖 `LF-TSK-QLT-0002` 的 change-local implementation slice；`LF-REVIEW-P1-001` 映射已激活的 Gate control-plane chain；`LF-DECISION-P1-001` 保留用户决定。 | checklist 勾选仅覆盖注明的切片，目录 Task 终态仍须以 current-input receipt 和独立复核确定。 |
| [G1 Catalog Task Evidence Map](g1-task-evidence-map.md) | 展开 `LF-TSK-ARCH-0008@5/2.2.0` 当前 30 个 blocking-closure task、60 条 blocking edge、已有证据、缺口和 receipt issuance DAG。 | 这是准备索引；既有 receipts 均非 current，激活任务也尚未因进入目录而自动成为 READY 或 PASS。 |
| [G1 Bootstrap Variance](g1-bootstrap-variance.md) | 记录 QLT-0001/OPS-0001 的 bootstrap validation 与 QLT-0002 在 Gate 控制面建立前派发的事实，并明确不可回填、不可冒充 catalog receipt。 | bootstrap `PASS` 只描述当时的局部验证，不能满足任务依赖、Gate 或用户批准。 |

当前**待办顺序**：分层 Gate 的唯一 delivery execution 与 review/catalog 零检查边界已经在前一次版本切片上复核通过。当前 `LF-WP-QLT-CANONICAL-EXECUTION-EVIDENCE-019` 已完成 canonical Codex artifact publisher 与 instance-based issuer separation 的实现，Main 补强 verifier 后通过 60 项定向测试；实际子代理共享父 session 且原 outcome 未记录源码改动，因此该 package 只保留为诊断证据，见 [current execution evidence](../development/current-execution-evidence.md)。实现集成后冻结 current catalog、registry 与 control-plane bytes，再从依赖根发行 current-input receipts；旧 14 张 catalog receipts 保留历史且非 current。只有在当前 packet/issuer 上完成 29 个 prerequisite Task 的 validation/review/catalog receipts 后，才提交完整架构/技术栈/SLO 给用户做 G1 决定；需要用户决定的 ARCH-0008 退出收据在明确批准后签发，不能提前要求它 PASS。缺 context 的历史 `FAIL` 不回填；新的 artifact verification PASS 也不代表 formal receipt PASS。

## 用户决定的边界与 Phase 2 入口

评审时需要对十条 ADR 的推荐方向，包括 `LF-TSK-OPS-0001` 对应的 ADR-010，以及本页初始时延、学习收敛、质量和成本测量方式给出接受、修改或拒绝的决定。可附条件接受某个目标，但必须把条件写入相应 ADR/验收任务；本页不会提前把 `Proposed` 改成 `Accepted`。G1 的可执行证据、技术栈任务验收和显式用户决定均完成后，目录的 `LF-TSK-ARCH-0008` 才能成为 Phase 2 entry task 的有效前置 receipt。[G1/G2 phase gate](../../planning/workstreams.yaml) 要求上一 Gate exit task 为 `PASS` 且有 `APPROVED` 用户确认，不允许仅凭文档已写或任务已派发开启 P2。

确认后 Phase 2 只进入 **Data Model / Data Contract**：按既定 owner 定义 Identity、Content、Lexicon、Vocabulary、Annotation、Learning Event 的聚合与不变量；事件幂等、乱序、重放与 provenance；迁移、访问模式、保留/删除边界及 G2 证据。阶段仍须先比较方案与取舍，再设计具体数据结构。具体 API、Enrichment 业务实现、Chrome Extension 行为、Learning 算法和部署编排分别由后续 Gate 激活；本页不预设 SQL 字段、REST 路径、Compose、Prompt 或具体 Provider。

审批顺序与工具链阶段边界见 [Phase 1 acceptance order](../development/phase-1-acceptance-order.md)。
