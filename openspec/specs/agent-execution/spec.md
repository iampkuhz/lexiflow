# Agent Execution Spec

## Requirements

### Requirement: 子任务身份和文件所有权

Catalog Task MUST 保持单一 owner、单一可验收 outcome 和独立 evidence identity，但 MUST NOT 机械映射为一次 Codex Sub-Agent 会话。每个 Qoder run MUST 有稳定 `task_id`；每个 Codex run MUST 有稳定 `work_package_id`、精确有序的 `task_ids[]`、唯一 agent/run identity、明确读写范围和验证命令。每个被聚合的 Task MUST 分别留下 outcome evidence；并行写范围 MUST NOT 重叠。

调用者提供的 handoff、runner 绑定身份和结果字段 MUST 与 `harness/agent-policy.manifest.yaml` 的当前分层 schema 一致。Qoder 的 Task/version/acceptance 输入，以及 Codex 的 package/task/version/owner/contract/acceptance 输入，MUST 在派发前验证。调用者 MUST NOT 提供 `agent_id` 或 `run_id`；Qoder 调用者还 MUST NOT 提供 `session_id` 或 `client`。运行时 MUST 生成或绑定这些 runner identity。

#### Scenario: 多个兼容 Task 组成一个 Codex 工作包

- **Given** 至少两个连续 Task 具有相同 primary owner、contract boundary 和兼容写范围
- **And** 总预计时间不少于 120 分钟
- **When** 主 Agent派发 Codex 实现
- **Then** 一个 handoff SHALL 使用稳定 `work_package_id` 和精确有序的 `task_ids[]`
- **And** 完成产物 SHALL 为每个 Task 分别记录 outcome evidence

#### Scenario: 工作量不足两小时

- **Given** 候选工作只包含单文件修复、单命令验证、孤立只读审阅或总预计时间不足 120 分钟
- **When** 主 Agent 选择执行者
- **Then** 工作 SHALL 留在主 Agent
- **And** SHALL NOT 为制造进度启动 Codex Sub-Agent

#### Scenario: 调用者预填 runner identity

- **Given** start task JSON 包含 `agent_id`、`run_id`、`session_id` 或 `client`
- **When** runner 验证 handoff
- **Then** 派发 SHALL 在创建 run 前失败
- **And** 持久化成功的任务 SHALL 只包含 runner 生成或绑定的 identity

#### Scenario: Codex 工作包投影为独立 Task evidence

- **Given** 一个 Codex work package 含至少两个 ordered catalog Task
- **When** 任一目标 Task 进入 generic evidence materializer
- **Then** raw task SHALL 使用 `lexiflow.codex-work-package-task-projection.v1`
- **And** SHALL 精确绑定同一 `work_package_id`、原顺序 `task_ids[]`、目标 Task、完整 caller contract 与 runner identity
- **And** caller contract SHALL NOT 包含 runner identity，raw task SHALL NOT 包含 Qoder `permission_mode`、`_resume_mode` 或 `title`
- **And** 每个目标 Task SHALL 保留独立 packet、plan 和 outcome evidence

#### Scenario: Canonical Codex 完成产物

- **Given** 一个已经绑定真实 runtime parent/session 与唯一 agent/run 的 Codex work package
- **When** runner 发布结构化 per-Task outcomes
- **Then** SHALL 使用 `lexiflow.codex-work-package-result.v1` 与机器策略固定的 structured layout
- **And** SHALL 不可变发布精确 Task-keyset 的 projection/outcome/completion/signal 与 artifact hashes，再暴露 package completion
- **And** publisher SHALL NOT 启动 subprocess、解析日志推断结果或签发正式 Gate receipt
- **And** 缺失 runtime session SHALL NOT 用随机 UUID 替代，legacy/alias layout SHALL NOT 作为回退

### Requirement: Codex 工作包规模与并发

Codex Sub-Agent 同时 MUST 最多一个。一个 Codex 工作包 MUST 聚合至少两个 compatible catalog Task，总预计时间 MUST 不少于 120 分钟，目标 SHOULD 不超过 360 分钟；完整历史默认 MUST NOT fork。完成回调 MUST 只包含 status、package/task/run identity、artifact locators、validation commands 和最多三条 blocking findings，MUST NOT 返回完整源码、日志或长上下文。

#### Scenario: 已有 Codex 工作包运行

- **Given** 一个 Codex Sub-Agent 尚未终态
- **When** 另一个工作包已准备执行
- **Then** 主 Agent SHALL 保留后者等待
- **And** SHALL NOT 启动第二个 Codex Sub-Agent 或用 Qoder 绕过写入冲突

### Requirement: Qoder 单运行

Qoder 同时 MUST 最多运行一个任务；前一 run 未确认终态或状态未知时 SHALL NOT 启动下一 run。

#### Scenario: 前一 run 状态未知

- **Given** Qoder preflight 或 completion 无法证明前一 run 已结束
- **When** 队列中存在下一任务
- **Then** 调度器 SHALL 保留任务等待
- **And** SHALL NOT 通过新 session 绕过单运行约束

### Requirement: 主 Agent 无 busy wait

完成通知 SHALL 优先通过 callback；主 LLM MUST NOT 循环查询。任何兜底检查第一次不得早于 300 秒，后续间隔不得短于 600 秒。

#### Scenario: 任务仍在运行

- **Given** Qoder worker 尚未写入 completion
- **When** watchdog 到达检查时点
- **Then** 非 LLM watchdog MAY 执行一次有界检查
- **And** 主 LLM SHALL NOT 因无状态变化被反复唤醒

### Requirement: 真实验收

只有全部 required validation 完成并通过时 MAY 报告 PASS。`queued`、`ack`、退出 0、跳过、未运行和未触发 SHALL NOT 作为 PASS 证据。

#### Scenario: Qoder 退出 0

- **Given** worker 已保存 exit code 0 和 completion
- **When** 主 Agent 处理回调
- **Then** run MAY 标记 finished
- **And** 主 Agent 独立复核运行指定检查后 MAY 接受 implementation evidence
- **And** catalog Task 只有 current-input `CATALOG_DECISION` receipt 完整验证依赖与证据链后 MAY 标记 PASS

### Requirement: 可验证的 Gate 控制面

Gate MUST 从 Main Agent 显式提供的六字段 structured result evidence packet 和固定 authority verifier 证明的 trusted issuer packet 编译纯 plan；MUST NOT 从 stdout、callback、exit code、自然语言 actor/role 或 latest 文件推断结果和权限。

Generic evidence packet MUST 能以 hash-bound empty changed-files、empty snapshot 和 empty diff 表达只读 meta-receipt；三者 MUST 精确一致，且 raw identity、tests、scope/claims 与 attestation 仍完整验证。Planner MUST 对 `TASK_VALIDATION` 拒绝空 subject snapshot，但 MAY 为 `INDEPENDENT_REVIEW` 与 `CATALOG_DECISION` 冻结空 reviewer write-set。调用方自报空数组不得替代该 packet 验证。

Generic evidence identity MUST 只接受 `client=qoder|codex`。Codex raw task MUST 绑定版本化 per-Task work-package projection，并拒绝 unknown client、duplicate key、字段夹带、少于两个 Task、target 缺失和 runtime identity 漂移。Planner MUST 按 client 使用互斥的 required/optional 字段集合：Qoder initial/resume persisted shape MUST 保持 current strict contract；Codex caller 的 package Task/version/owner/output/acceptance/validation/scope MUST 与 current catalog 对账。Owner、discovery 与 file claims MUST 只由 current catalog 产生。

Main-only singleton 来源 MUST 使用显式 `lexiflow.codex-main-task-projection.v1`，由受信 runtime 证明真实 Main actor，绑定唯一 current Task 并复用相同 evidence/identity/snapshot/diff validators。该来源 MUST 与 delegated Codex projection 互斥；MUST NOT 通过 caller 自报 executor kind、伪装 Qoder 或降低 delegated package minima 获得权限。独立 issuer 与禁止自审约束不变。

Qoder issuer provenance MUST 接受真实 runner 的 pretty/noncanonical JSON whitespace，并按 locator/hash 冻结原始 task/completion bytes；materializer 与 Planner MUST 同时拒绝 duplicate key、非法 JSON number、identity/version drift、非终态 completion 与 stale authority evidence。Issuer packet、非 Qoder attestation 和 receipt 的 canonical JSON 规则不变。

`client` MUST 只表示工具类型，MUST NOT 单独作为 copied-subject identity 的判据。同客户端或共享真实宿主 session 的不同 verified actor MAY 成为 subject 与 trusted issuer；相同 actor/agent、run、instance 或 replay identity MUST 被拒绝。Actor MUST 在同一真实执行实例的连续 run/attestation 间稳定，MUST NOT 通过更换 nonce/run 伪装独立 reviewer。Session MUST 保留真实会话/路由含义，MUST NOT 随机生成来绕过独立性检查。独立 review MUST 同时排除 subject producer 与 validation issuer，并保持 current input、hash 与零 subject write-set 验证。

Gate planner MUST 零写入并只选择 versioned registry 中声明的 fixed argv。Run MUST 在任何 checker 执行前先持久化并 flush `START`，再向调用方 flush 包含唯一 `run_id` 和固定 event locator 的可见 `START`；任一 START 步骤失败时 checker MUST NOT 运行。

`TASK_VALIDATION` MUST 是唯一执行 selected delivery checks 的层。`INDEPENDENT_REVIEW` 与 `CATALOG_DECISION` 的 frozen plan MUST 声明 `checker_execution=forbidden` 且 `checks=[]`，只能消费 hash-bound immutable evidence/receipts；CLI、review handler 和 catalog handler MUST 拒绝 receipt kind、execution layer 与 check set 不一致的 plan。Java 产品源码规则 MUST 由 Gradle/Java 下的 Spotless、Checkstyle、PMD、Java source gate、ArchUnit 与 JUnit/JaCoCo 执行，Python Gate MUST NOT 重复扫描 Java 源码实现同义断言。

TASK_VALIDATION、INDEPENDENT_REVIEW 和 CATALOG_DECISION MUST 通过同一 CLI 的串行 route 写入不可覆盖 receipt。Status MUST 只按调用方提供的 UUID 读取固定 run 路径，MUST NOT 扫描目录或解析 `latest`。Review MUST 验证 reviewer 与 producer 独立，把 reviewer write-set 与 hash-bound current plan 对账，并重新读取 validation packet 绑定的 changed-file snapshot 验证当前 subject bytes/state；调用方自报 changed-files 或 plan 列表 MUST NOT 单独建立 no-write 事实。Catalog closure MUST 再次重验前序链中所有 validation subject snapshots。Hash verifier MUST 只读验证 locator/hash DAG 且拒绝 missing、alias、self-edge、back-edge 和 cycle。Catalog closure MUST 比较 current inputs 的 canonical locator 与 hash，并重新验证每份前序/依赖 receipt issuer packet 的 current authority registry/provenance；同字节 alias 或自造 issuer receipt MUST FAIL。只有 current acceptance registry、task/change/source/policy、validation/review/hash 和 required dependency receipts 全部 current、可信且为 PASS 时，CATALOG_DECISION MAY 将 catalog Task 标记 PASS。

#### Scenario: 调用方省略可信 evidence context

- **Given** 调用方请求编译或运行 Gate，但没有提供 result evidence packet 或 trusted issuer packet 的唯一 locator/hash
- **When** planner 验证输入
- **Then** 结果 SHALL 为 FAIL
- **And** planner SHALL NOT 扫描目录寻找候选 packet

#### Scenario: START 无法对调用方可见

- **Given** run identity 已生成，但磁盘 START 或调用方可见 START 无法完成并 flush
- **When** Gate 准备调用 checker
- **Then** checker SHALL NOT 运行
- **And** 现有事件 SHALL 保留失败事实而不能形成 PASS receipt

#### Scenario: 证据通过但依赖 receipt 过期

- **Given** task validation、independent review 与 hash DAG 均为 PASS
- **And** 一个 required dependency receipt 的 task/change version 不是 current catalog pin
- **When** catalog decision route 聚合证据
- **Then** catalog Task SHALL NOT 标记 PASS
- **And** 新 receipt SHALL 保留稳定的 stale-dependency 诊断

### Requirement: 显式 Git 生命周期

Harness MUST NOT 自动 stage、commit、merge、rebase、reset、stash、force 或 push；集成与发布必须来自显式用户指令。

#### Scenario: 子任务完成且有未提交修改

- **Given** 子任务实现与定向检查已经结束
- **When** worker 写入 completion 或回调父会话
- **Then** 修改 SHALL 保留在当前 checkout
- **And** worker SHALL NOT 自动改变 index、HEAD 或 remote
