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

同一宿主 OS 用户的所有 checkout 通过 Harness 派发的 Qoder 同时 MUST 最多运行一个任务；从派发到 CLI 退出 SHALL 连续持有同一文件锁，worker/CLI SHALL 继承该锁。前一 run 未确认终态或回调未 ack 时 SHALL NOT 启动下一 run。外部直接启动 CLI 不受该锁强制控制，进程预检 SHALL 对已观察到的外部运行 fail closed。

#### Scenario: 前一 run 状态未知

- **Given** Qoder preflight 或 completion 无法证明前一 run 已结束
- **When** 队列中存在下一任务
- **Then** 调度器 SHALL 保留任务等待
- **And** SHALL NOT 通过新 session 绕过单运行约束

### Requirement: 主 Agent 无 busy wait

Qoder 派发后 SHALL 保存 continuation 并立即交还控制权；主 LLM MUST 结束当前回合，仅由匹配的终态 callback 续办。主 LLM MUST NOT sleep、查询时钟、轮询 status/result、进程或日志；不存在 300/600 秒后允许主 LLM 探测的特例。计时探测只属于非 LLM watchdog。新运行未终态的 status/result SHALL 返回交接状态及非零退出码，不提供活跃日志供轮询。Python runner 不能终止宿主 LLM 回合，主 Agent SHALL 遵守返回的 end-current-turn 动作。Codex 子代理 SHALL 使用宿主原生协作事件，不得等待不会产生的 Qoder 回调。

### Requirement: 宿主等待兼容性

Qoder preflight、start 与 resume MUST 通过真实当前父会话绑定的只读宿主状态检查。宿主 Goal 活跃或状态未知、且没有受支持的外部等待适配器时，仓库入口 SHALL 在创建 Qoder 进程前拒绝该执行路径并返回 Codex 降级动作。调用者提供的能力布尔值、仓库提示词或 Goal 状态修改 MUST NOT 作为门控证据。该检查只证明派发时兼容性，不证明未来宿主模式不变或宿主调度器已经修复。

#### Scenario: 活跃 Goal 无外部等待支持

- **Given** 当前父任务的真实 Goal 活跃且宿主没有受支持的等待接入
- **When** 调用任一 Qoder 派发入口
- **Then** SHALL 不创建 Qoder 运行，并请求原生 Codex 降级
- **And** SHALL NOT 以暂停目标、改宿主数据库或循环唤醒实现等待

### Requirement: 组合调度失败

跨执行器顺序、失败阈值与模型 SHALL 读取共享 policy 的 agent_dispatch。一次调度 SHALL 绑定同一父任务、仓库、工作包、任务版本与 attempt；Qoder 明确失败或不可用后 MUST 尝试 Codex 降级，只有双方均明确失败才增加连续调度失败。接单成功 SHALL 清连续计数但保留历史。回调重放、同任务在途、未知启动、执行失败与验收失败 MUST NOT 当作新一轮调度失败；身份和写域条件 MUST NOT 被降级绕过。

#### Scenario: 降级接单成功

- **Given** 本轮 Qoder 明确不可用
- **When** 当前父任务通过原生协作工具成功派发指定模型，并提交绑定 attempt 的真实工具记录
- **Then** SHALL 记录本轮成功并清连续失败计数
- **And** SHALL 保留历史；任意自报成功或其他工作包的句柄 MUST NOT 清计数

#### Scenario: 连续组合失败达到上限

- **Given** 同一工作包连续组合调度失败已达到共享 policy 上限
- **When** 再次请求自动派发
- **Then** SHALL 停止且不启动任一执行器
- **And** SHALL NOT 用自动 Goal 续轮数代替组合调度记录

#### Scenario: 任务仍在运行

- **Given** Qoder worker 尚未写入 completion
- **When** watchdog 到达检查时点
- **Then** 非 LLM watchdog MAY 执行一次有界检查
- **And** 主 LLM SHALL NOT 因无状态变化被反复唤醒

### Requirement: 执行请求与启动资格

明确的启动或继续请求 MUST 以实际派发、同步交付或可核验阻塞收尾，MUST NOT 只承诺下一步后结束。Qoder 预检 MUST 检查静态输入及只读启动资格快照；已知历史、预算、访问或进程阻塞 MUST 返回非零退出的 `BLOCKED`。预检 MUST NOT 分配 run、代表账号健康或代替派发锁内复查。具体执行与接手策略只在共享 policy 中维护。

#### Scenario: 预算耗尽且存在已知访问阻塞

- **Given** 稳定 Task 的 Qoder 尝试预算耗尽，历史终态还记录访问失败
- **When** 调用启动预检
- **Then** SHALL 同时报告预算与访问阻塞，不再建议已耗尽的 resume
- **And** SHALL 保留历史与稳定 Task identity，由 Main 复核后按共享策略选择合规接手
- **And** SHALL NOT 以新建同义 Task、改名或版本变化重置预算

#### Scenario: 只完成预检或派发

- **Given** 预检返回 PASS，或 start 只返回 run id
- **When** 主 Agent 汇报执行状态
- **Then** SHALL 区分预检通过、已派发与已启动
- **And** SHALL 只使用匹配身份的 started.json 确认 CLI 启动，不以计划或下一步说明替代启动证据

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

`client` MUST 只表示工具类型。原生子代理 MUST 可以在同一父任务和宿主 Session 下分别实现、验证和审查；本地 adapter MUST NOT 要求独立 Session 或额外独立性认证。实现者与验证者、审查者，以及验证者与审查者 MUST 是不同执行 actor。共享 Session 本身 MUST NOT 构成身份重叠；同一 actor 更换 run、名称或 Session 路由 MUST NOT 获得自验、自审许可。Session MUST 保留真实宿主含义，MUST NOT 伪造；冻结输入、hash 和零 subject write-set 约束保持不变。

本机默认 Codex authority MUST 直接读取 owned、非共享可写、非 symlink 的原生 metadata，核对 workspace、thread 与父子关系。根任务使用稳定 `codex-session-<thread-id>` actor；原生子代理使用稳定 `codex-thread-<thread-id>` actor。环境变量仅定位来源；子 thread 与宿主 Session 不同属于合法原生路由，不得机械判为冲突。来源缺失 SHALL BLOCKED，来源矛盾 SHALL FAIL。记录绑定 thread 及父元数据 hash，不绑定持续增长的整份日志。信任边界为本机用户，不是平台密码学认证。

内部委派 MUST 默认使用原生 subagent，MUST NOT 自动创建普通任务来回避身份或 Hook 问题。只有至少两轮不同的子代理验证均有当前输入 PASS 证据、对应 Hook 仍阻塞、已留下针对性修复尝试和当前任务无法解决的具体原因，并得到用户明确创建授权后，才 MAY 使用新 Session 兜底。重复读取同一 PASS、业务测试失败、单次身份错误或在途未知结果 MUST NOT 满足例外。新任务仍 MUST 执行相同门禁，显式设置模型与推理参数并核对实际运行模型：默认 Luna，只有具体复杂性或失败风险证据才可升级 Sol；不得因 create_thread 继承 Astra。

公开 `doctor` MUST 只读报告 runtime readiness；`run --evidence-packet` 在没有显式 issuer 时 MUST 从当前原生任务或子代理来源创建新鲜 authority evidence 与 issuer，再交给原有纯 planner。历史 attestation/receipt MUST NOT 改写、更新时间或自动升级为 current。显式 issuer 仍须严格验证，MUST NOT 隐式替换失效输入；缺少 subject evidence MUST 给出可操作错误而不是要求用户构造 identity JSON。`plan` MUST 保持零写入并要求显式 issuer。

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
