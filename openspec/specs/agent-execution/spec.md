# Agent Execution Spec

## Requirements

### Requirement: 原生派发与手工客户端边界

实现、验证与审查 MUST 按 `harness/agent-policy.md` 的协作边界 使用原生 Codex 协作；模型与推理参数 MUST 从 `codex_model_policy` 显式选择。Qoder 仅保留手工客户端入口、权限 profile 与 Stop 适配，MUST NOT 提供仓库委派或运行证据消费链。已退役链的 ignored 原始记录 SHALL 保留原始字节，不再消费。

### Requirement: 子任务身份和文件所有权

Catalog Task MUST 保持单一 owner、单一可验收 outcome 和独立 evidence identity，但 MUST NOT 机械映射为一次 Codex Sub-Agent 会话。当前 Catalog 是 owner、discovery、file claims、Task/version、acceptance 与 validation contract 的权威来源，不可由 caller 自报覆盖。每个 Codex run MUST 有稳定 `work_package_id`、精确有序的 `task_ids[]`、唯一 agent/run identity、明确读写范围和验证命令。每个被聚合的 Task MUST 分别留下 outcome evidence；并行写范围 MUST NOT 重叠。

调用者输入、runner 身份绑定和结果 MUST 通过 `scripts/agents/` 的实际接口校验；人类协作边界以 `harness/agent-policy.md` 为准。Codex 的 package/task/version/owner/contract/acceptance 输入 MUST 在派发前验证。调用者 MUST NOT 提供 `parent_session_id`、`agent_id`、`run_id`、`session_id`、`client` 或 `parent_client`；运行时 MUST 一次派生并认证这些 identity。

#### Scenario: 单个有界 Task 组成 Codex 工作包

- **Given** 一个 Task 有冻结 contract、明确 owner 和兼容写范围，预计工作量至少 10 分钟
- **When** 主 Agent派发 Codex 实现
- **Then** 一个 caller contract SHALL 使用稳定 `work_package_id` 和恰含该 Task 的有序 `task_ids[]`
- **And** 完成产物 SHALL 为该 Task 记录 outcome evidence

#### Scenario: 确定性微任务留在主 Agent

- **Given** 候选工作是确定性单命令任务或预计不超过 10 分钟的小修复
- **When** 主 Agent 选择执行者
- **Then** 工作 SHALL 留在主 Agent
- **And** SHALL NOT 为制造进度启动子代理

#### Scenario: 调用者预填 runner identity

- **Given** caller contract JSON 包含任一 runner identity 字段
- **When** 包级入口验证 caller contract
- **Then** 派发 SHALL 在创建 run 前失败
- **And** 持久化成功的任务 SHALL 只包含 runner 生成或绑定的 identity

#### Scenario: Codex 工作包一次冻结并发布 Task evidence

- **Given** 一个 Codex work package 含一个或多个有序 Catalog Task
- **When** 包级 `scripts.agents.identity.codex_actor_identity.bind_work_package(repo_root, caller_contract)` 接收当前实现约束的 caller contract
- **Then** SHALL 一次从当前 Catalog 派生并不可变冻结完整包合同，包括精确逐 Task versions、outputs、acceptance、validation commands
- **And** runtime SHALL 在内部一次派生并认证真实身份，返回 run ID 与 frozen/runtime descriptors；caller MUST NOT 注入身份
- **And** CURRENT Catalog SHALL 只用于完整 drift 检查，不得补填冻结字段

#### Scenario: 每 Task 只引用不可变父来源

- **Given** frozen caller 与 runtime binding 已独立认证并发布
- **When** `CodexWorkPackagePublisher(root).publish(run_id, outcomes)` 发布精确 Task outcome map
- **Then** 每个 projection SHALL 引用不可变父来源，不复制完整 caller；schema 与字段校验以 `scripts/agents/work_packages/work_package_contract_checks.py` 为准
- **And** 每 Task SHALL 只保存 `task-projection.json` 与 `outcome.json`，不得保存重复 packet、plan、completion 或 signal
- **And** 唯一 package completion SHALL 在父来源与全部 Task evidence 重新核对后最后不可变发布，原生 compact signal 随后发出而不落重复镜像
- **And** publisher SHALL NOT 启动 subprocess、解析日志推断结果或签发正式 Gate receipt

`client` MUST 精确为 `codex`。父来源 JSON MUST 拒绝重复键，允许合法空白但按原始字节绑定 hash；不完整、身份或版本冲突 MUST 拒绝。Codex `verify(run_id)` MUST 重读并认证父来源、外部 artifact raw hashes 与当前 Catalog drift，完整派生内存 `producer_view`；数据结构合规或自报 actor 不构成身份认证。Main 来源由 Gate 的实际 runtime authority 核对，不另建 Main projection 或 bind-task CLI；Main 与 delegated 不得互相冒充。缺失 runtime MUST 返回 BLOCKED，不得用随机 Session UUID 或 legacy layout 回退。

### Requirement: Codex 工作包规模与并发

Codex 原生子代理同时最多 MUST 遵守 `harness/agent-policy.md` 的并发上限；不同子代理仅在写域隔离时可并行，集成验证 MUST 串行。Codex 工作包可包含一个或多个兼容 Task，规模按 policy 的工作包边界确定；确定性单命令或不超过 10 分钟的小修复留在主 Agent。内部委派 MUST 使用当前父任务下原生 subagent，不得以 `create_thread` 新聊天兜底或绕过身份、Hook。完整历史默认 MUST NOT fork。实现者 MAY 执行静态、编译和直接测试作为自检，但 MUST NOT 签发 Formal validation 或 independent review；验证与审查必须由不同真实 actor 完成。完成回调 MUST 只包含 status、package/task/run identity、artifact locators、validation commands 和最多三条 blocking findings，MUST NOT 返回完整源码、日志或长上下文。

#### Scenario: 兼容写域可并行，集成验证串行

- **Given** 多个 Codex 子代理均处于 policy 并发上限内且写域互不重叠
- **When** 主 Agent 安排执行与集成验证
- **Then** 实现工作 MAY 并行
- **And** 集成验证 SHALL 串行

### Requirement: 原生事件等待与真实启动

明确的启动或继续请求 MUST 以实际原生派发、同步交付或可核验阻塞收尾，MUST NOT 只承诺下一步后结束。父任务 SHALL 使用原生长事件等待，不轮询进程、日志或重复短间隔查询；等待中可执行无写域冲突的工作。未知运行、等待超时和重复事件 MUST NOT 授予重复执行资格；失败按原工作包定位，不以新同义 Task 或改名重置身份与预算。

#### Scenario: 工具已受理但尚未完成

- **Given** 原生协作工具返回真实 agent 标识
- **When** 父任务报告进展并等待原生终态
- **Then** SHALL 将受理或启动与完成分开，不把计划、预检或预计动作当真实派发
- **And** SHALL 核对匹配工作包的终态、来源与产物，不以超时推断完成

### Requirement: 真实验收

只有当前完整 Delivery Gate 链中 plan 所需 validation、可选 review、dependencies 与 approvals 全部核验为 PASS 时 MAY 报告 Task PASS。`queued`、终态通知、退出 0、跳过、未运行和未触发 SHALL NOT 作为 PASS 证据；实现者自检不是 Formal validation。

#### Scenario: 原生终态不是验收

- **Given** 原生子代理已发出完成事件并发布工作包产物
- **When** 主 Agent 核对真实来源和完成产物
- **Then** completion 只构成 implementation evidence，不得替代独立 `validate`
- **And** Task 只有 current-input Delivery Gate `check` receipt 验证所需 validation、风险要求的 review、依赖与批准后 MAY 标记 PASS

### Requirement: 日常开发检查按消费闭包选择

日常 `development-change` SHALL 根据完整真实 diff 中显式声明的 `triggers` 与模块消费依赖闭包选择 checks，不得因 `high-risk-engineering` 风险等级单独追加全仓 `development-baseline`。风险分类仍 MUST 保留；未知覆盖、依赖、依赖环及相关输入漂移 MUST 失败关闭，同工作区未提交变化 MUST 累计纳入。此日常选择规则 MUST NOT 改变完整 Repository Verify 或 Formal 计划：独立 Formal 高风险验收仍 MUST 使用 `development-baseline` 并要求独立 review，正式发行 MUST 使用完整 `repository-baseline`。

#### Scenario: 高风险日常选择与 Formal 计划区分

- **Given** 完整真实 diff 被分类为 `high-risk-engineering`，且消费依赖闭包可确定
- **When** 日常开发入口选择 `development-change` checks
- **Then** checks SHALL 仅由显式触发与模块依赖闭包决定，不因风险等级单独追加全仓 `development-baseline`
- **And** 未知覆盖、依赖、环或相关漂移 SHALL 阻断；独立 Formal 高风险计划仍使用 `development-baseline` 并要求 review
- **And** 正式发行与显式完整 Repository Verify SHALL 保持完整 `repository-baseline`

### Requirement: 风险计划驱动的 Delivery Gate

Delivery Gate 的公开交付入口 MUST 是 `python3 -m scripts.acceptance submit|validate|review|check|status`，另提供只读 `consume-existing` 与 `consume-candidate`。调用者 MUST NOT 自报执行者身份或审批权限；MUST NOT 从 stdout、callback、exit code、Task 名称或 latest 文件推断 PASS、授权或真实身份。

`submit` MUST 读取当前 Catalog Task 的 scope、版本、required checks、dependencies 和 approval requirement，只接收精确的完整真实 diff PASS `development-change` 报告，不接收等值 scope 确认，并核验真实 producer。它 MUST 从真实 diff、changed-file 内容与当前 policy 重算风险及唯一 acceptance plan，并冻结完整检查闭包、输入、风险 assessment 和完整 review patch（包含 untracked bytes）；正式发行仅在 Task `required_check_ids` 命中当前 Registry `scenario_policy.ci_quick.formal_only_check_ids` 时成立，且必须使用完整 `repository-baseline`。机械/局部风险使用 `development-change`，高风险工程使用 `development-baseline`；普通开发与高风险工程均不得冒充完整发行验收。任一风险主体、Task 要求、policy、附件或闭包变化都 MUST 使后续阶段拒绝旧结论。

`validate` MUST 由不同于 submitter 和真实 producer 的 runtime-bound actor 执行，并通过 Verification 公共 API 对同一冻结计划运行必需 checks；执行前后均 MUST 重核 producer 来源、当前 Task/policy、完整冻结输入及报告。实现者可做静态、编译和直接测试作为自检，但自检 MUST NOT 充当 Formal validation，也不得签发正式 receipt。

只有 acceptance plan 要求时，`review` 才 MUST 由同时不同于 submitter、producer 与 validator 的真实 reviewer 消费冻结 diff、附件和 validation evidence，记录明确 findings；它 MUST NOT 运行交付 checks。high-risk-engineering 与 formal-release 要求 review；mechanical 与 local-function 不要求 review，不生成伪 review record，check 链的 `review_id`/hash MUST 为 null。`check` MUST 只核对已发布 validation、计划要求的 review、当前依赖、哈希绑定与 Task 所需的精确用户批准，不重跑测试。`status` 只观察指定 submission。

单 Task 主体 MUST 从当前完整真实 diff 与当前 Catalog allowed_files/file_claims 机械派生且非空，MUST 拒绝主体范围内 forbidden/claim 违规；不得由调用者提供过滤路径。风险主体 MUST 包含所选检查声明输入和模块依赖内的实际变化，递归求稳定闭包。提交时来源报告 MUST 匹配完整真实 diff；后续 MUST 重新派生 Task 主体与变化依赖，识别相关新增、删除和字节变化，但闭包外无关写入不单独使局部验证失效。局部 receipt 不代表其他任务或完整工作区验收。

一次 validate 调用 MAY 处理最多 16 个唯一 submission。它 MUST 在任何检查前验证全部身份和冻结，且执行者 MUST 与每一个 producer/submitter 不同；每 Task MUST 保留独立计划、报告和 validation record。等价 Check MAY 复用同次真实窗口与 transaction 的显式允许执行；不得复用历史自检或整份其他 Task 报告。运行中各任务仍须执行前后及发布前新鲜度检查；首次 FAIL/BLOCKED MUST 停止后续昂贵视图，不将未执行项标 PASS。

当前活动记录 MUST 统一保存在 `tmp/quality/delivery-gate/current/`，schema 为 submission.v6 与 validation/review/check.v5；原 ignored 旧记录 MUST 保留原始字节，但不得自动消费或通过 flags 开启兼容。Review 状态 MUST 由 findings 派生，不接受 caller decision；review 与 check MUST 只读核对而不重跑命令。同一冻结父内完整可派生子 hash 与镜像不重复存储，外部 runtime metadata 和附件 raw hashes 仍 MUST 独立核验。

每份记录和外部附件 MUST 绑定规范 locator、内容 hash 与所需 identity/version/scope；读取 MUST 拒绝重复 JSON key、路径穿越、symlink、非普通文件、重复或损坏 receipt、subject bytes 漂移及不可信 authority 来源。Review patch 和 changed-file snapshot MUST 覆盖冻结主体。Runtime-bound actor MUST 来自实际本机原生 metadata，不得由 caller、actor 字符串或 role 文本伪造。原生子代理可共享父 Session，但身份独立以真实 actor 为准；同 actor 不得通过更换 run、名称或 Session route 获得自验、自审许可。本机默认 Codex authority MUST 核对 owned、非共享可写、非 symlink 的 native metadata 及 workspace/thread/parent 关系；缺失来源 BLOCKED，矛盾来源 FAIL。保持既有本机信任边界：这是本地来源证明而非平台密码学认证，不声称已解决同 OS 恶意冒用。

依赖闭包 MUST 校验 Task/change 版本、唯一完整 PASS 链、嵌套 receipt hash 与环路；missing、ambiguous、重复、损坏、循环或 stale 链 MUST 阻断。用户批准 MUST 由精确绑定 Gate、Task/version 与对应依赖 check hash 的批准记录证明，系统不得自行生成。`consume-existing` MUST 只读重验唯一完整 PASS 链；`consume-candidate` 还 MUST 要求完整 repository-baseline 报告、真实候选 bytes 与候选运行证据。Review/check/两种 consume MUST NOT 执行交付测试；调用退出 0、终态通知、跳过或缺失都不是 PASS。

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

- **Given** 实际 diff/内容构成高风险工程，或 Task 的 required checks 命中 Registry formal-check 集合
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

### Requirement: Canonical 检查与受控执行

Registry MUST 由 `config/engineering/module-checks.yaml` 提供，固定结构由实际加载器校验；每个稳定 `check_id` MUST 直接拥有执行内容，MUST NOT 维护 execution 外键或 `module` 展示身份。`scenarios` MUST 是非空资格列表；开发与 CI 的变更选择 MUST 只消费每 Check 唯一的有效触发范围（默认输入范围，必要差异才显式声明），完整基线 MUST 按资格执行所有 Check；baseline-only 的 Docker contract 因真实 CI 消费 SHALL 保留唯一触发声明。无依赖默认空，只有真实依赖 SHALL 声明 `module_dependencies`；依赖、身份、完整性与覆盖的失败关闭仍由真实实现校验。MUST NOT 保留旧 schema、场景 override/fallback 或旧 ID alias。 CI quick 的常驻与 formal-only 集合 MUST 来自同一 Registry 的 `scenario_policy.ci_quick`。

唯一开发 diff 证明 MUST 是一个 frozen `development-change` profile 与一个 report。完整 Repository Verify 及 Formal `verify_profiles` MUST 保留，Formal 同次 1..16 submission 的完整等价复用 MUST 保留。诊断 MUST 只接受精确 `--check-id` 并派生完整依赖闭包与自动环境 lease，MUST NOT 签发正式资格。子进程 MUST 只消费固定 run/check/snapshot，重派生声明后精确比对；scope 资格 MUST 在父端证明，progress observer MUST NOT 读 env/stdin/raw stdout、返回进程结果或决定 PASS。

`CheckEnvironment.values` MUST 仅供事务内私有执行，`.diagnostic` 才可公开；不得序列化七类私有能力值。报告消费 MUST 重新解析，不跨事务缓存资格。PostgreSQL 唯一容量 MUST 是 512 MiB memory 与 256 MiB data tmpfs。Apple 系统 Git MUST 认证实际 payload，不靠 prepared shim。Stop 超时与心跳 MUST 由实现固定，不维护配置副本或覆盖接口；紧急停用仍不产生 PASS，真正中间回合标记 MUST NOT 用于普通交付。

### Requirement: 有界仓库与发行支撑

Catalog 任务入口 MUST 仅在 `Gate.entry_tasks`；ownership MUST 从共享 Git 源码候选与冻结共享 ignore 派生，不读取私有树，也不得让个人 ignore 隐藏新增文件。文档检查 MUST 覆盖固定 mandatory roots，以一次 DocIndex 复用上下文；本机 skill 只从 `CODEX_HOME/skills` 接入固定 `.agents/skills/`。Python 质量入口 MUST 自身执行 Ruff check、Ruff format、Pylint 三项；机器 zero-exit JSON transport MUST 保留，不能用人工 CLI 的退出零替代结构化结果。仓库 MUST NOT 安装 Git printf 提醒 Hook，客户端 Stop MUST 保留。

发行 synthetic bridge MUST 固定 Node 与 ARM64 daemon，保留一次真实 images、两代各一次 assemble 与 verify、真实 Java/Extension build 和两代合成生命周期证明，不提供 target-platform 选择。ECDICT MUST 仅消费锁定已提取 CSV，并要求显式 `--generation-code-version`，不得 tar 或自动 Node fallback。候选完整性消费 MUST 只运行一次稳定 Node proof，但末尾 Gate/附件重读与每次 publish 前新鲜度认证 MUST 保留；共享纯环境合同不得合并不同信任边界的真实认证。

Promotion 默认 MUST 只读 precheck，不构造归档或计算归档 hash；只有显式 publish 授权后，才在首个远端写入前构造和核验。预检不再发现归档构造类故障，故障发现时点后移但仍不得有远端写入；每个实际远端写入前 MUST 重验最新候选证据。

### Requirement: 规则、机器配置与固定实现分离

AGENTS.md SHALL 仅通过 harness/README.md 按任务加载相关 Markdown 规则。真正项目可变的检查 Registry、风险路径与 Ruff 选择 MUST 位于 config/engineering/；固定 schema、接口、独立身份要求与工具运行布局 MUST 以实现为唯一来源，MUST NOT 维护说明字段副本或兼容旧路径。新配置路径 MUST 纳入 ownership、高风险分类和实际检查输入冻结；集合新增、删除、内容修改 MUST 被检测。仅验证规则配置副本字段的 policy 门禁 MUST NOT 存在。
