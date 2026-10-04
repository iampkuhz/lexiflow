# Agent Execution Spec

## Requirements

### Requirement: 子任务身份和文件所有权

Catalog Task MUST 保持单一 owner、单一可验收 outcome 和独立 evidence identity，但 MUST NOT 机械映射为一次 Codex Sub-Agent 会话。当前 Catalog 是 owner、discovery、file claims、Task/version、acceptance 与 validation contract 的权威来源，不可由 caller 自报覆盖。每个 Qoder run MUST 有稳定 `task_id`；每个 Codex run MUST 有稳定 `work_package_id`、精确有序的 `task_ids[]`、唯一 agent/run identity、明确读写范围和验证命令。每个被聚合的 Task MUST 分别留下 outcome evidence；并行写范围 MUST NOT 重叠。

调用者提供的 handoff、runner 绑定身份和结果字段 MUST 与 `harness/agent-policy.manifest.yaml` 的当前分层 schema 一致。Qoder 的 Task/version/acceptance 输入，以及 Codex 的 package/task/version/owner/contract/acceptance 输入，MUST 在派发前验证。调用者 MUST NOT 提供 `agent_id` 或 `run_id`；Qoder 调用者还 MUST NOT 提供 `session_id` 或 `client`。运行时 MUST 生成或绑定这些 runner identity。

#### Scenario: 单个有界 Task 组成 Codex 工作包

- **Given** 一个 Task 有冻结 contract、明确 owner 和兼容写范围，预计工作量至少 10 分钟
- **When** 主 Agent派发 Codex 实现
- **Then** 一个 handoff SHALL 使用稳定 `work_package_id` 和恰含该 Task 的有序 `task_ids[]`
- **And** 完成产物 SHALL 为该 Task 记录 outcome evidence

#### Scenario: 确定性微任务留在主 Agent

- **Given** 候选工作是确定性单命令任务或预计不超过 10 分钟的小修复
- **When** 主 Agent 选择执行者
- **Then** 工作 SHALL 留在主 Agent
- **And** SHALL NOT 为制造进度启动子代理

#### Scenario: 调用者预填 runner identity

- **Given** start task JSON 包含 `agent_id`、`run_id`、`session_id` 或 `client`
- **When** runner 验证 handoff
- **Then** 派发 SHALL 在创建 run 前失败
- **And** 持久化成功的任务 SHALL 只包含 runner 生成或绑定的 identity

#### Scenario: Codex 工作包发布独立 Task evidence

- **Given** 一个 Codex work package 含一个或多个 ordered catalog Task
- **When** runner 为 package 发布 per-Task 投影
- **Then** 每个 raw task SHALL 使用 `lexiflow.codex-work-package-task-projection.v1`
- **And** SHALL 精确绑定同一 `work_package_id`、原顺序 `task_ids[]`、目标 Task、完整 caller contract 与 runner identity
- **And** caller contract SHALL NOT 包含 runner identity，raw task SHALL NOT 包含 Qoder `permission_mode`、`_resume_mode` 或 `title`
- **And** 每个目标 Task SHALL 保留独立 packet、plan 和 outcome evidence

Qoder/Codex `client` MUST 只标识工具类型并符合当前枚举。Qoder producer fact MUST 从 runner-owned task/completion/result 文件读取并按原始 locator/hash 绑定；接受合法 JSON 空白格式但按原始字节绑定 hash；重复键、Task/run/version 不一致、非终态或不完整结果 MUST 拒绝。Codex 的 caller contract、per-Task projection 与产物 MUST 精确对账当前 Catalog；Main-only 与 delegated 来源不得互相冒充。

#### Scenario: Main-only Codex Task 使用独立投影

- **Given** 当前真实 runtime actor 是 Main Codex Task 且未委派实现
- **When** runner 为该 Main-only singleton 构造 evidence
- **Then** SHALL 使用 `lexiflow.codex-main-task-projection.v1` 并绑定唯一 current Task 与同一 snapshot/diff/identity validators
- **And** MUST 与 delegated package projection 互斥，caller 自报 executor kind、伪装 Qoder 或放宽 delegated package 最小数不得取得 Main-only 权限

#### Scenario: Canonical Codex 完成产物

- **Given** 一个已经绑定真实 runtime parent/session 与唯一 agent/run 的 Codex work package
- **When** runner 发布结构化 per-Task outcomes
- **Then** SHALL 使用 `lexiflow.codex-work-package-result.v1` 与机器策略固定的 structured layout
- **And** SHALL 不可变发布精确 Task-keyset 的 projection/outcome/completion/signal 与 artifact hashes，再暴露 package completion
- **And** publisher SHALL NOT 启动 subprocess、解析日志推断结果或签发正式 Gate receipt
- **And** 缺失 runtime session SHALL NOT 用随机 UUID 替代，legacy/alias layout SHALL NOT 作为回退

### Requirement: Codex 工作包规模与并发

Codex 原生子代理同时最多 MUST 遵守 `harness/agent-policy.manifest.yaml` 的并发上限；不同子代理仅在写域隔离时可并行，集成验证 MUST 串行。Codex 工作包可包含一个或多个兼容 Task，规模按 policy 的工作包边界确定；确定性单命令或不超过 10 分钟的小修复留在主 Agent。内部委派默认使用原生 subagent，不得以创建普通任务绕过身份或 Hook；仅在 policy 要求的多轮验证、Hook 阻塞、定向修复/能力限制证据与用户明确授权齐备时，才允许开新任务兜底，且必须显式选择并核对实际模型/推理参数。Qoder 仍受宿主 OS 用户范围的单运行约束，不得与 Codex 并发规则混淆。完整历史默认 MUST NOT fork。实现者 MAY 执行静态、编译和直接测试作为自检，但 MUST NOT 签发 Formal validation 或 independent review；验证与审查必须由不同真实 actor 完成。完成回调 MUST 只包含 status、package/task/run identity、artifact locators、validation commands 和最多三条 blocking findings，MUST NOT 返回完整源码、日志或长上下文。

#### Scenario: 兼容写域可并行，集成验证串行

- **Given** 多个 Codex 子代理均处于 policy 并发上限内且写域互不重叠
- **When** 主 Agent 安排执行与集成验证
- **Then** 实现工作 MAY 并行
- **And** 集成验证 SHALL 串行，Qoder 宿主单运行不因此放宽

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

只有当前完整 Delivery Gate 链中 plan 所需 validation、可选 review、dependencies 与 approvals 全部核验为 PASS 时 MAY 报告 Task PASS。`queued`、`ack`、退出 0、跳过、未运行和未触发 SHALL NOT 作为 PASS 证据；实现者自检不是 Formal validation。

#### Scenario: Qoder 退出 0

- **Given** worker 已保存 exit code 0 和 completion
- **When** 主 Agent 处理回调
- **Then** run MAY 标记 finished
- **And** completion 与 exit code 只构成 implementation evidence，不得替代独立 `validate`
- **And** Task 只有 current-input Delivery Gate `check` receipt 验证所需 validation、风险要求的 review、依赖与批准后 MAY 标记 PASS

### Requirement: 风险计划驱动的 Delivery Gate

Delivery Gate 的公开交付入口 MUST 是 `python3 -m scripts.delivery_gate submit|validate|review|check|status`，另提供只读 `consume-existing` 与 `consume-candidate`。调用者 MUST NOT 自报执行者身份或审批权限；MUST NOT 从 stdout、callback、exit code、Task 名称或 latest 文件推断 PASS、授权或真实身份。

`submit` MUST 读取当前 Catalog Task 的 scope、版本、required checks、dependencies 和 approval requirement，接收精确的 PASS `change-targeted` 或 `development-change` 报告及 scope 确认，并核验真实 producer。它 MUST 从真实 diff、changed-file 内容与当前 policy 重算风险及唯一 acceptance plan，并冻结完整检查闭包、输入、风险 assessment 和完整 review patch（包含 untracked bytes）；正式发行仅在 Task `required_check_ids` 命中当前 CI formal-check 集合时成立，且必须使用完整 `repository-baseline`。机械/局部风险使用 `development-change`，高风险工程使用 `development-baseline`；普通开发与高风险工程均不得冒充完整发行验收。任一风险主体、Task 要求、policy、附件或闭包变化都 MUST 使后续阶段拒绝旧结论。

`validate` MUST 由不同于 submitter 和真实 producer 的 runtime-bound actor 执行，并通过 Verification 公共 API 对同一冻结计划运行必需 checks；执行前后均 MUST 重核 producer 来源、当前 Task/policy、完整冻结输入及报告。实现者可做静态、编译和直接测试作为自检，但自检 MUST NOT 充当 Formal validation，也不得签发正式 receipt。

只有 acceptance plan 要求时，`review` 才 MUST 由同时不同于 submitter、producer 与 validator 的真实 reviewer 消费冻结 diff、附件和 validation evidence，记录明确 findings；它 MUST NOT 运行交付 checks。high-risk-engineering 与 formal-release 要求 review；mechanical 与 local-function 不要求 review，不生成伪 review record，check 链的 `review_id`/hash MUST 为 null。`check` MUST 只核对已发布 validation、计划要求的 review、当前依赖、哈希绑定与 Task 所需的精确用户批准，不重跑测试。`status` 只观察指定 submission。

单 Task 主体 MUST 从当前完整真实 diff 与当前 Catalog allowed_files/file_claims 机械派生且非空，MUST 拒绝主体范围内 forbidden/claim 违规；不得由调用者提供过滤路径。风险主体 MUST 包含所选检查声明输入和模块依赖内的实际变化，递归求稳定闭包。提交时来源报告 MUST 匹配完整真实 diff；后续 MUST 重新派生 Task 主体与变化依赖，识别相关新增、删除和字节变化，但闭包外无关写入不单独使局部验证失效。局部 receipt 不代表其他任务或完整工作区验收。

一次 validate 调用 MAY 处理最多 16 个唯一 submission。它 MUST 在任何检查前验证全部身份和冻结，且执行者 MUST 与每一个 producer/submitter 不同；每 Task MUST 保留独立计划、报告和 validation record。等价 Check MAY 复用同次真实窗口与 transaction 的显式允许执行；不得复用历史自检或整份其他 Task 报告。运行中各任务仍须执行前后及发布前新鲜度检查；首次 FAIL/BLOCKED MUST 停止后续昂贵视图，不将未执行项标 PASS。

每份记录和外部附件 MUST 绑定规范 locator、内容 hash 与所需 identity/version/scope；读取 MUST 拒绝重复 JSON key、路径穿越、symlink、非普通文件、重复或损坏 receipt、subject bytes 漂移及不可信 authority 来源。Review patch 和 changed-file snapshot MUST 覆盖冻结主体。Runtime-bound actor MUST 来自实际本机原生 metadata，不得由 caller、actor 字符串或 role 文本伪造。原生子代理可共享父 Session，但身份独立以真实 actor 为准；同 actor 不得通过更换 run、名称或 Session route 获得自验、自审许可。本机默认 Codex authority MUST 核对 owned、非共享可写、非 symlink 的 native metadata 及 workspace/thread/parent 关系；缺失来源 BLOCKED，矛盾来源 FAIL。保持既有本机信任边界：这是本地来源证明而非平台密码学认证，不声称已解决同 OS 恶意冒用。

依赖闭包 MUST 校验 Task/change 版本、唯一完整 PASS 链、嵌套 receipt hash 与环路；missing、ambiguous、重复、损坏、循环或 stale 链 MUST 阻断。用户批准 MUST 由精确绑定 Gate、Task/version 与对应依赖 check hash 的批准记录证明，系统不得自行生成。`consume-existing` MUST 只读重验唯一完整 PASS 链；`consume-candidate` 还 MUST 要求完整 repository-baseline 报告、真实候选 bytes 与候选运行证据。Review/check/两种 consume MUST NOT 执行交付测试；调用退出 0、ack、跳过或缺失都不是 PASS。

Receipt 按不可覆盖记录发布；`status` MUST 只依据调用者指定的 submission UUID 返回该链状态，不推断 latest 或发布新结论。Java 产品检查由 Gradle/Java 工具执行：JUnit 执行测试，JaCoCo 产出覆盖率报告；Python Gate MUST NOT 重复扫描 Java 源码实现同义断言。

#### Scenario: 单 Task Codex package

- **Given** 一个有界 Catalog Task 具有冻结 owner、contract、scope 和 acceptance
- **When** Codex runner 构造 raw task 与完成产物
- **Then** package MUST 允许一个 Task，并精确绑定有序唯一 `task_ids[]`、Task projection、真实 runtime identity 和各自 outcome evidence
- **And** 少于当前 policy 最低数量、重复 Task、缺 target 或 identity 漂移 MUST 在派发/发布前失败

#### Scenario: 低风险正式链不伪造 review

- **Given** 机械或局部 Task 的真实主体和当前 policy 形成无需 review 的 acceptance plan
- **When** 独立 validator 通过冻结计划执行并运行 `check`
- **Then** validation MUST 独立于 submitter 与 producer，且无需 review record
- **And** check 中 `review_id` 与 review hash MUST 为 null，review/check MUST 不执行测试

#### Scenario: 风险升级与正式发行要求

- **Given** 实际 diff/内容构成高风险工程，或 Task 的 required checks 命中 CI formal-check 集合
- **When** submit 冻结计划并由 validator 执行
- **Then** 高风险工程 MUST 使用 `development-baseline` 并要求独立 review；正式发行 MUST 使用完整 `repository-baseline` 并要求独立 review
- **And** 计划 MUST 来自当前 policy 与真实 Task checks，而非 Task 名称或调用者自报风险

#### Scenario: 哈希或依赖链不完整

- **Given** submission 附件或任一依赖 receipt 缺失、重复、哈希漂移、版本不匹配、存在循环或有多个完整 PASS 候选
- **When** Gate 执行 `validate`、`review`、`check` 或只读消费
- **Then** 该阶段 MUST 返回 BLOCKED/FAIL，且不得从 stdout、exit code 或 latest 记录补造 PASS
- **And** 缺少 Task 要求的精确用户批准也 MUST 阻止 check PASS

#### Scenario: 检查层不重跑测试

- **Given** 已存在符合计划的 validation evidence，后续需要 review、check 或消费 PASS 链
- **When** 任一后续阶段执行
- **Then** 只有 `validate` MAY 运行计划中的 checks
- **And** review/check/consume MUST 只复核绑定证据与当前条件

### Requirement: 显式 Git 生命周期

Harness MUST NOT 自动 stage、commit、merge、rebase、reset、stash、force 或 push；集成与发布必须来自显式用户指令。

#### Scenario: 子任务完成且有未提交修改

- **Given** 子任务实现与定向检查已经结束
- **When** worker 写入 completion 或回调父会话
- **Then** 修改 SHALL 保留在当前 checkout
- **And** worker SHALL NOT 自动改变 index、HEAD 或 remote
