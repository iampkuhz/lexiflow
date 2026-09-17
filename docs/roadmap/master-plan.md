# LexiFlow 长期交付总计划

> Program：`LF-PRG-001`  
> 当前阶段：`P1 Architecture`  
> 计划状态：`ACTIVE`  
> 机器目录：[`planning/workstreams.yaml`](../../planning/workstreams.yaml)  
> 单任务契约：[`planning/task-template.yaml`](../../planning/task-template.yaml)

## 1. 目标与交付边界

LexiFlow 的首个产品入口是 YouTube Chrome Extension。英文字幕必须立即显示，系统只对用户可能不熟悉的词、短语和专业术语增量补充结合语境的中文提示。后端维护跨设备共享的 Vocabulary Profile，并通过可回放的 Learning Events 持续更新熟悉度。

项目采用 **Modular Monolith**。部署可以是单体，代码和数据所有权必须按 Domain 分开。第一版允许 PostgreSQL、Redis、API、worker 和 Chrome Extension；在真实负载证明必要前，不引入 Kubernetes、Kafka、Service Mesh、Vector DB 或微服务拆分。

当前只授权推进 `P1 Architecture`。`P2` 到 `P7` 在本计划中用于建立依赖、容量和长期拆分边界；它们在 `G1` 获得用户明确认可前不得进入实现。P1 的输出停留在 Domain、职责、依赖方向、核心数据流和同步/异步边界，不提前冻结表字段、REST endpoint、Compose 文件、Prompt 或实现代码。

## 2. 设计原则

1. **英文优先且不阻塞播放**：英文 caption 走同步快路径；annotation 可异步增量到达。
2. **后端是个人学习状态真源**：Extension 只采集、渲染、缓存和上报，不拥有最终 Vocabulary Profile。
3. **规则决定是否帮助，模型决定如何解释**：规则、词典和用户画像先筛选；模型只处理消歧、术语、短语和中文表达。
4. **事件先于派生状态**：Learning Event 不被最终 familiarity 覆盖；画像可按新算法重放。
5. **抽象提供者**：业务代码依赖 `SemanticProvider` 能力，不依赖某个厂商 SDK。
6. **证据决定完成**：进程退出、Agent 声称完成或回调成功都不等于验收通过。
7. **逐层冻结**：每个阶段先呈现总体方案、核心决策、2–3 个替代方案、trade-off 和推荐，再由阶段 Gate 决定是否展开下一层。
8. **扩展入口复用核心**：YouTube 是第一个 Content Adapter；网页、PDF、Podcast 和其他视频平台复用 Content、Enrichment、Learning 与 Vocabulary。

### 2.1 从参考仓库继承的工程约束

本计划复用 `/Users/zhehan/Documents/tools/llm/feipi-session-browser-java` 中已经证明可维护的结构原则：业务核心、application、port/SPI、adapter、Web/CLI 入口分层；逻辑模块 ID 与物理目录解耦；architecture、contract、quality-gate 测试分层；`harness` 只保存静态机器契约；Gate 先编译唯一 plan，再执行 owner，最后保存不可覆盖 receipt；平台配置只做入口，不保存项目运行状态。

这些原则会映射为 LexiFlow 的 Domain 模块、Content/Semantic ports、API/worker/Extension adapters、`planning`/`harness` 真源和显式增量 Gate。参考仓库的 session-specific 模块、SQLite 选择和 JDK 版本不会直接复制；LexiFlow 的语言、构建工具、PostgreSQL/Redis 适配和部署形式仍由 `G1` 的技术栈 ADR 根据本产品负载决定。

模块依赖必须按下列方向实现：composition root 同时组装 application 与 concrete inbound/outbound adapters；inbound adapters 调用 application；outbound adapters 实现 domain ports；application 只依赖 domain/ports。`application -> concrete adapter`、`domain -> framework/database/cache/provider` 和 inbound adapter 直接调用 outbound adapter 均为禁止边。组合根是唯一知道具体实现并完成装配的位置。

## 3. WBS 与稳定身份

### 3.1 五层结构

| 层级 | 含义 | ID 格式 | 是否可直接派发 |
|---|---|---|---|
| Program | 一个长期产品投资 | `LF-PRG-001` | 否 |
| Epic | 一个稳定业务域或横切能力 | `LF-EP-{DOMAIN}-{NNN}` | 否 |
| Capability | 可验收的用户/系统能力 | `LF-CP-{DOMAIN}-{NNN}` | 否 |
| Task | 单 owner、单目标、可独立验收的最小计划单元 | `LF-TSK-{DOMAIN}-{NNNN}` | 是 |
| Run | 某个 Task 的一次设计、实现、复核或返工尝试 | UUID，展示别名 `{task_id}-R{NNN}` | 是 |

`DOMAIN` 使用 `planning/workstreams.yaml` 中登记的代码。ID 一旦分配不得改义、复用或因阶段迁移而重编号。Phase、优先级、里程碑和状态都是可变属性，不编码进 ID。删除的 ID 保留为 `retired`；替代关系使用 `supersedes`/`superseded_by`。

### 3.2 规模扩展方法

`workstreams.yaml` 是 seed catalog，不为未来几百个子功能创建空文件。扩展时按以下方式增长：

- Capability 表示业务能力，不等于实现 Task。一个 Capability 通常激活为 3–12 个可独立验收的原子 Task；每个 Task 可产生设计、实现、定向测试、独立复核、Gate 验证和返工 Run，但这些 Run 不得被计作新的子功能。
- 目录中的 seed task 提供主干。进入某阶段前，对该阶段 P0/P1 Capability 做 just-in-time 分解，并把新 Task 写回同一 catalog 或后续机器目录。
- 当前 catalog 已登记 18 个 Workstream、41 个 Capability 和 113 个真实原子 Task，其中 8 个 Gate control-plane Task 是经过设计与独立评审后激活的 JIT Task。后续按 Capability 的实际缺口新增 262 个原子 Task，形成 375 个 fully-expanded 容量目标；300 个 Task 是项目规模基线。基线公式为 **300 Task × 5 Run/Task = 1,500 Run**；上界公式为 **375 Task × 8 Run/Task = 3,000 Run**。这里的 Run 是任务级设计、实现、测试、复核、Gate 或返工证据，不是 Sub-Agent 会话；一次 2–6 小时工作包通常覆盖多个 Task 和多个 Run。
- 只有真实目标、owner、依赖、交付物和验收均已明确时才创建 Task。Run 是执行历史，不能用复制空 Task 的方式制造规模。

按 Phase 的 fully-expanded 容量 envelope 由机器目录校验：

| Phase | 当前 seed | JIT 可新增 | Fully-expanded 目标 | 主要容量来源 |
|---|---:|---:|---:|---|
| P1 | 32 | 13 | 45 | 架构、边界、Harness、Gate 控制面、威胁与性能预算 |
| P2 | 14 | 36 | 50 | 数据模型、事件、Lexicon、Profile 与 migration |
| P3 | 13 | 37 | 50 | API、Auth、Sync、事件入口与 contract tests |
| P4 | 23 | 57 | 80 | Enrichment pipeline、Semantic Provider、缓存与质量基准 |
| P5 | 11 | 49 | 60 | YouTube adapter、overlay、离线恢复、浏览器 E2E 与可访问性 |
| P6 | 8 | 37 | 45 | Learning scoring、replay、projection、跨设备闭环与对比验证 |
| P7 | 12 | 33 | 45 | 安全、观测、容量、灾备、发布与未来 adapter 验证 |
| **合计** | **113** | **262** | **375** | `planning/workstreams.yaml.phase_capacity_budget` |

容量 envelope 用于证明项目能够稳定扩展，不是 Gate 的机械最小 Task 数。Gate 只检查当前 activated scope 完整、没有空壳、P0/P1 和风险覆盖充分；低于 envelope 是正常的 JIT 分解状态，只需记录 variance、理由和 coverage evidence，不需要用户为正常差异额外批准。不得为追平 375 机械生成空壳 ID。

## 4. 拆分与调度规则

### 4.1 Task 的准入条件

一个 Task 同时满足以下条件才进入 `READY`：

- 一个可验证目标，完成标准能在 1–5 条断言中表达；
- 一个 primary owner 和一个写入范围；实现与其直接测试同任务交付；
- hard dependencies 均为 `PASS`，所消费的 contract version 已固定；
- 每个独立 Task 预计 20–90 分钟；
- 包含真实 validation command 或明确的人工证据格式；
- 风险、回滚/降级方式、禁止修改范围已声明；
- 没有与运行中 Task 重叠的文件 claim。

出现任一情况必须继续拆分：跨两个 bounded context、跨两个文件 owner、同时改变 provider contract 与两个以上 consumer、超过 8 个主要生产文件、需要互不相关的验收环境、或预估单 Task 超过 90 分钟。纯重命名/格式化可以批量处理，但不得借机混入行为变化。

同一 Domain 内的紧密步骤可保留为 Run checklist；发现独立目标、不同 owner 或新的风险边界时，分配新的 Task ID，并用 `discovered_from` 和 DAG 边连接。不得在 Run 内暗中扩大 Task scope。

原子 Task 不直接等同 Sub-Agent 调用。实际 Codex 委派把同一 owner、contract 边界和兼容写入范围内的至少两个连续 Task 组成总预计 120–360 分钟的工作包，一次完成实现、直接测试与逐 Task outcome evidence；工作包使用稳定 `work_package_id`、精确 `task_ids[]` 和独立 `run_id`。不足 120 分钟、单文件修补、单命令验证和孤立只读审阅由主 Agent 处理。工作包不能跨越需要独立 current-input PASS 才能继续的依赖边，也不能把共享文件的串行修改伪装成并行工作；Qoder 由 runner 承接 180–360 分钟、至少两个同 owner/contract/写入边界的 Work Package，且全局同时最多一个 run。Codex Sub-Agent 默认显式使用 `gpt-5.6-terra`，高风险升级必须记录理由。

### 4.2 依赖 DAG

- 只允许 `Task -> Task`、`Capability -> Capability` 的 `depends_on`；DAG 必须无环。
- 每条 seed dependency 都是对象，至少固定 `task_id`、`type`、`required_task_version` 与 `required_change_version`。`hard` 还要求 `required_result=PASS`；`soft` 只影响排期；`contract` 额外固定 `contract_name` 与 `required_contract_version`。
- 公共 schema/API/event/provider contract 先由 owner Task 冻结，consumer Task 才能并行。
- 集成 Task 依赖所有贡献 Task；发布 Gate 依赖集成与独立验证 Task。
- 禁止用 Phase 序号隐式表达依赖。跨阶段提前做风险验证时，必须显式挂在已满足的 Task 上。
- P2–P7 的每个入口 Task 必须直接 hard-depend 于上一 Gate exit task，并声明上一 Gate 为 `PASS`、exit task/change version 精确匹配、用户批准为 `APPROVED`。Validator 还必须证明同阶段其他 Task 的 blocking ancestry 可追溯到合法入口，且阶段 exit task 覆盖全部 P0 入口。

### 4.3 文件 owner 与并行写入

`workstreams.yaml.path_ownership` 是 owner 真源。Architecture Gate 前使用 logical scope；Gate 通过后绑定为实际路径。解析 owner 时使用“最具体 scope 优先”。

Owner 表必须覆盖产品路径，也覆盖当前治理入口：`docs/development/**`、`docs/reviews/**`、`docs/references/**`、`openspec/**`、`.codex/**`、`.qoder/**`、`AGENTS.md`、`README.md`、`.gitignore`、`docs/README.md` 与 `scripts/README.md`。Chrome Extension 的唯一 protected/owner 路径为 `clients/chrome-extension/**`。

- 一个文件在同一时刻只能被一个 Task claim。
- 并行修改型 Agent 的 `allowed_files` 不得重叠；共享 contract 由 contract owner 串行落地。
- 其他 owner 通过 consumer Task 适配，不直接越界编辑。
- 生成物和运行凭证写入每 Run 唯一目录；不得共用可覆盖的 `latest` 作为验收真相。
- 集成、migration 顺序、release manifest 和最终 Gate 串行执行。

## 5. Agent 执行模型

### 5.1 Main Agent 的职责

Main Agent 负责阶段决策、DAG 编译、文件 claim、任务派发、跨域集成、独立复核与最终验收。它不把 Agent 的自然语言结论直接升级为 `PASS`，也不汇总不同 parent session 的匿名结果。

简单、独立且文件范围不重叠的研究、实现或验证 Task 可派发给 Codex Sub-Agent。功能、修复、重构、测试、脚本、配置和文档实现默认可交给 Qoder（用户所称 Coder Sub-Agent）；设计决策、跨域集成和最终 Gate 由 Main Agent 保留。

### 5.2 禁止 busy wait

以下约束同时适用于 Qoder 与 Codex Sub-Agent：

1. 派发后优先依赖完成回调；Main Agent 继续做不冲突的本地工作，若无工作则结束当前回合。
2. Main Agent 不调用 `sleep`、进程轮询、状态循环或短间隔 wait 来维持主 Session。
3. **LLM 兜底首次状态查询不得早于派发后 300 秒。**
4. **若仍无新证据，后续 LLM 状态查询间隔不得小于 600 秒。**
5. 回调是事件，不受 300/600 秒限制；收到回调可立即复核。
6. 允许每 Run 一个不调用 LLM 的 watchdog，在 300 秒首次检查、以后每 600 秒检查；运行无变化时保持静默。
7. 用户主动询问状态时可做一次只读快照，但不得由此启动轮询循环。

### 5.3 Qoder 与 Codex Sub-Agent 约束

| 项目 | Qoder | Codex Sub-Agent |
|---|---|---|
| 并发 | 全 Program 最多 1 个 active run；状态未知时禁止补开 | 受平台并发槽位约束；仅派发无文件/contract 冲突的 Task |
| 递归 | 禁止 Qoder 再派发 Qoder | 可为更小的独立只读或不重叠任务再拆分，但必须传递 parent/task 身份 |
| 回调 | 精确 parent session；先落 completion record 再通知 | Agent 完成消息回调 parent |
| 返工 | 同 session 最多 1 个修正 run；总计最多 2 个实现 run | 默认 `fork_turns=none`，最多 1 个修正 run；仍失败由 Main Agent 缩小或拆分 Task |
| 验收 | Main Agent 独立运行 validation | Main Agent 独立运行 validation |

Handoff 使用分层契约。调用者必须提供 `caller_required_input`：`goal`、`task_id`、`task_source`、`task_version`、`change_version`、`allowed_files`、`forbidden_files`、`required_context`、`expected_output`、`acceptance_criteria`、`acceptance_evidence`、`validation_command`、`failure_policy`、`parent_client`。Runner 在 dispatch 绑定 `runner_bound_identity`：`parent_session_id` 可由调用者提供或从 `CODEX_THREAD_ID` 注入；`agent_id`、`run_id` 由 runner 自动生成；`session_id` 由 start 生成或 resume 复用；`client` 强制为 `qoder`。`run_id` 绝不是 start task JSON 的调用者输入。结果必须提供 `status`、`changed_files`、`validation`、`acceptance_evidence`、`effect_checks`、`risks`。

Runner 已实现该分层 schema：强制 14 个 caller field，拒绝 caller 预填 runner identity，在持久任务与 completion 中固定 Task/change/run identity，并把六个结果字段写入 prompt。`tests/harness/test_qoder_runner.py` 验证静态 conformance；具体任务仍只能在主 Agent 独立复核输出、diff 和 required validation 后得到 PASS。

## 6. 状态、证据与质量 Gate

### 6.1 计划状态与验收状态

计划生命周期使用 `DRAFT -> READY -> DISPATCHED -> IN_PROGRESS -> REVIEW -> DONE`，并允许 `BLOCKED`、`SUPERSEDED`、`CANCELLED`。它与验收结果分开：

| 验收状态 | 含义 |
|---|---|
| `PASS` | required validation 完整执行且全部通过，证据可定位 |
| `BLOCKED` | validation 完整执行并发现产品、规则或测试阻断 |
| `FAIL` | 输入、依赖、环境、超时、中断或不可判定导致 validation 未有效完成 |

`skipped`、`not-run`、`unavailable`、`excluded`、Agent exit 0、任务已回调或通知 queued 都不得描述为 `PASS`。

### 6.2 每个 Run 的最小证据

- 精确 task/version、agent/client/parent session、开始结束时间；
- changed files 与文件 claim 对账；
- validation command、exit code、stdout/stderr 摘要和不可覆盖日志路径；
- acceptance criterion 到自动化测试、人工检查或观测指标的映射；
- effect checks：预期行为、回归面、性能、安全、隐私和数据迁移影响；
- 风险、遗留限制、回滚或降级入口；
- Main Agent 的独立复核结论。

Gate 的机器计划必须先冻结输入，再给出 `file -> trigger -> gate` 原因；执行产生唯一 receipt。Gate 聚合顺序是 `FAIL > BLOCKED > PASS`。

## 7. 版本变化与返工

- `task_id` 稳定，`task_version` 使用整数递增；Run 永远引用精确版本。
- `change_version` 使用 SemVer：文字澄清且不改验收为 patch；新增兼容范围或验收为 minor；改变目标、contract 或破坏兼容为 major。
- 已 `DISPATCHED` 的任务不得原地改 scope。先将旧版本标为 `SUPERSEDED`，保存原因，再派发新版本。
- 返工保持 task id 和 Qoder session，创建新 run id；follow-up 只描述未通过证据与所需修正。
- 同一实现者最多返工一轮，总计最多两个实现 Run。仍不收敛时由 Main Agent 选择缩小并重新拆 Task、交给专项 owner，或以 `BLOCKED` 返回阶段 Gate；不得继续堆叠第三个 Qoder Run。
- Qoder handoff 完整 prompt 不得超过 8,000 字符；长设计只给出有界文件定位和目标章节，不重复粘贴正文。
- Codex Sub-Agent 同时最多 1 个，默认 `fork_turns=none`；只有显式例外才允许继承完整会话历史。
- contract 变更必须列出受影响 consumer、兼容期、迁移 Task 和回滚策略；依赖边随版本更新。

## 8. Phase 1–7 与阶段 Gate

Phase 不是简单瀑布：风险验证可以提前，但任何执行都必须遵循 DAG。P2–P7 的入口由上一 Gate exit receipt 与明确的 user approval receipt 双重控制；缺一项时 Task 不能进入 `READY`。每个 Gate 均要求：总体方案、核心决策、2–3 个备选、trade-off、推荐、未决 Assumption 和用户明确结论。

### P1 — Architecture

交付 Domain map、模块职责、依赖方向、两条端到端数据流、同步/异步边界、可靠性预算、逻辑文件 owner 和关键 ADR。重点决策包括 modular monolith 边界、事件保留方式、provider abstraction、Extension/Backend 真源边界和缓存一致性。

`G1 Architecture Accepted` 通过条件：

- 六项架构交付均有可审查文档；
- 依赖图无环，Domain 不反向依赖 adapter/infrastructure；
- annotation 快路径与 learning event 路径标明同步/异步、timeout 和 fallback；
- 关键选择均有 2–3 个备选与 trade-off；
- 用户明确认可方向。

### P2 — Data Model

在 G1 后设计 PostgreSQL schema、event model、lexicon、Vocabulary Profile、Content、Annotation、索引、retention 与 migration。先冻结概念和不变量，再进入字段与迁移脚本。

`G2 Data Contract Accepted` 要求 schema/versioning、事件幂等、重放、tenant/user isolation、索引假设和 migration rollback 均有证据。

### P3 — API

设计 Enrichment、Vocabulary、Event、Auth、Client Sync、error model、idempotency 与 versioning。contract 测试先于 consumer 并行实现。

`G3 API Contract Accepted` 要求 OpenAPI/等价契约、兼容策略、授权矩阵、幂等与错误语义通过 contract tests。

### P4 — Enrichment Engine

按 Normalization、Token/Phrase Detection、Dictionary/Vocabulary Lookup、Candidate Generation、Need-Hint、Disambiguation、Translation、Annotation 分层实现。规则与模型的责任不得混合为单次整句翻译调用。

`G4 Enrichment Quality Accepted` 要求离线 gold set、context disambiguation、提示稀疏度、provider fallback、cache key 正确性及 P95/P99 预算达标。

### P5 — Chrome Extension

实现 YouTube caption acquisition、player context、overlay renderer、API integration、L1 cache、event collection、reconnect/fallback 与权限最小化。

`G5 End-to-End Experience Accepted` 要求英文零等待显示、annotation 增量渲染、播放器状态适配、断网降级与浏览器自动化用例通过。

### P6 — Learning Model

先交付可解释 rules/scoring，再引入概率 familiarity。Event 到 Profile 的推导必须版本化、可重放、可对比和可回滚。

`G6 Learning Loop Accepted` 要求跨设备事件合并、画像更新、算法重放、解释输出与用户 known/unknown 反馈闭环通过。

### P7 — Productionization

完善 deployment、observability、security、backup/restore、rate limiting、model cost、load test、performance tuning 和 release/rollback。生产能力按 slice 提前嵌入各 Phase，P7 完成系统级收口。

`G7 Release Ready` 要求安全与隐私审查、灾备演练、成本上限、SLO、容量和 release rollback 全部有不可覆盖证据。

## 9. MVP、Alpha、Beta

| Milestone | 用户可见范围 | 必须通过的最小 Gate | 非目标 |
|---|---|---|---|
| MVP | 单用户在 Chrome/YouTube 上看到英文字幕与少量语境中文提示；行为可上报，Profile 可跨浏览器同步 | G1–G5；G6 的解释型 baseline slice；G7 的本地/单环境部署、安全、日志、备份最小 slice | 概率模型、多内容源、团队协作、高可用 |
| Alpha | 邀请用户使用；Profile 跨设备稳定；缓存、provider fallback、事件重放和成本观测可用 | 全部 P0 与目标 P1 Task；G6；G7 的告警、限流、restore drill | 大规模增长和自动 ML 训练 |
| Beta | 可控外部用户；隐私、可恢复性、兼容升级、容量与支持流程达到发布标准 | G1–G7；所有 release-blocking 风险关闭；全量 regression/load/security Gate | 微服务/K8s 等无负载证据的扩展 |

Milestone 是 Capability slice 的集合，不复制 Task。Task 用 `milestone_slices` 标明归属，一个 Task 可服务多个里程碑。

## 10. 优先级与风险

| 优先级 | 定义 | 调度规则 |
|---|---|---|
| P0 | 当前 Gate 或里程碑不可缺少，涉及真源、核心路径、安全/数据完整性 | hard dependency 满足后优先；阻断则 Gate 为 BLOCKED |
| P1 | 显著影响质量、性能、可运维性或主要体验 | 目标里程碑前完成 |
| P2 | 改善效率或边缘体验，有明确降级 | 不阻断 MVP/Alpha，按容量调度 |
| P3 | 探索性或未来入口 | 只有证据触发，默认不展开 |

Requirement level 映射为 `MUST`、`SHOULD`、`CAN_EVOLVE`，不代替优先级。风险按 impact × likelihood 分为 low/medium/high/critical；high/critical Task 必须有独立 reviewer、失败降级和专门验证 Run。

## 11. 立即执行顺序

1. 保留 `LF-TSK-QLT-0002` 三次 Qoder 失败与 Codex takeover 的 implementation `PASS` 证据；catalog result 继续 pending。
2. 保留一次性 non-READY bootstrap 边界：`QLT-0007/0014/0008/0009/0010/0011/0012/0013` 与 `QLT-0005` 的控制面实现已经落地。首轮 current-bytes 集成复核发现 formal-root activation、可执行文件绑定和 canonical task-source 三项缺陷；Main Agent 已修复，并以 21 个封闭 profile、registry version 2 的 30 个条目和全 30 Task 编译矩阵激活 G1 closure。当前先完成新的冻结输入集成复核；共享 CLI 与证据写入继续串行。
3. 用新控制面对 current inputs 从依赖根开始签发真实 receipt；先闭合 `QLT-0001`、`QLT-0006`、`QLT-0002`、`QLT-0003`，再按 DAG 闭合 `QLT-0004` 与控制面任务。所有 bootstrap run 仅作 provenance，禁止回填。
4. 在 `QLT-0002` current receipt 后实现并验收 `QLT-0005` dispatch preflight；write claim、owner 与 contract-writer 冲突必须有 fail-closed fixture。
5. 把已复核的 Architecture、横切合同与 `OPS-0001@2/1.1.0` 接入同一 current-input validation/review/hash/catalog chain，确保 `ARCH-0008@2` 的六个直接 blocking prerequisites 都可验证。
6. 独立运行唯一 incremental G1 Gate，输出 `PASS/FAIL/BLOCKED` 与不可覆盖证据索引；之后再向用户提交 P1 架构、技术栈和初始 SLO 决策。只有用户明确认可后，P2 entry tasks 才能从 `DRAFT` 提升到 `READY`。

`planning/workstreams.yaml` 已覆盖所有主要功能域及 seed DAG；进入下一阶段时应动态展开真实 Task，而不是预建几百个占位文件。
