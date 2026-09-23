# LexiFlow Harness

`harness/` 只保存静态、机器可读的仓库约束：

- `manifest.yaml`：项目类型、公开命令与文档入口。
- `agent-runtime.manifest.yaml`：客户端拥有 Session/checkout 的最小运行契约。
- `agent-policy.manifest.yaml`：隐私、Git、子任务、Qoder 调度和结果语义。
- `module-checks.yaml`：模块级检查声明。
- `java-product.manifest.yaml`：Java 产品构建与验证规则。

## 脚本归属

`scripts/` 只保存可跨阶段复用且具稳定命令或库消费者、直接测试覆盖的仓库能力。一次性导入、诊断、迁移辅助或仅为当前 OpenSpec 变更服务的脚本必须放在 ignored `tmp/phase-tools/<change-id>/`；它们不得进入公开 manifest、产品运行时或文档的长期命令。只有用户明确批准、补齐稳定合同和测试后，才可提升到 `scripts/`。

`python3 -m scripts.repository.planning_check --root .` 验证 planning catalog
的静态一致性。

### 目录结构

日常检查的唯一入口是 `scripts/check_changes.py` 与 `scripts/check_repository.py`；正式验收的唯一公开入口是 `python3 -m scripts.acceptance submit|validate|review|check|status`。`scripts/verification/` 只拥有任务中立的声明、冻结输入、固定检查执行与报告；`scripts/acceptance/` 拥有任务来源、身份消费、正式记录、批准和 hash DAG。`scripts/repository/` 拥有计划静态一致性检查、文档治理、策略投影、本机 skill 与 hook 等仓库维护能力。

`scripts/agents/` 独立拥有真实 Codex 身份适配、工作包原始执行记录和 Qoder 生命周期；`codex/` 与 `qoder/` 为其内部实现。Agent 模块不导入 acceptance；acceptance 只消费 agents 的公开 runtime facts 与 verification 的公开 API。

## Python 运行环境

Gate 使用 PyYAML；不要依赖系统 `python3` 恰好已安装。首次在本机执行：

```bash
python3 -m venv .local/lexiflow-python
.local/lexiflow-python/bin/pip install -r requirements-dev.txt
```

之后将上述解释器替换为命令前缀，例如
`.local/lexiflow-python/bin/python -m scripts.repository.planning_check --root .`。`.local/` 是忽略的本机运行环境，不是 Gate evidence。

验证报告与正式记录分别写入 ignored 的 `tmp/quality/verification-reports/`、`tmp/quality/acceptance/`；Qoder 任务写入
`tmp/qoder-tasks/<run-id>/`。Harness 不保存产品运行状态，也不把 active change 当成普通写入的权限令牌。

日常 Verify 不是变更锁或 commit 准入。完成时运行：

```bash
python3 scripts/check_changes.py
python3 scripts/check_repository.py
```

前者按最终 diff 执行 `change-targeted` checks，并输出 advisory `scope_review`；`--expected-path` 只帮助自审，不是编辑或提交权限。后者不读取 Task、身份或验收记录，执行当前 checkout 的所有 `repository-baseline` checks。缺少必需运行环境返回 `BLOCKED` 并指出具体 requirement；验证入口不安装依赖或修改环境。两类本地产物都在 ignored `tmp/quality/`，不构成正式验收记录。

`python3 -m scripts.repository.hooks install` 只安装非阻断提醒；它不自动执行 Verify，也不修改 change context。CI 必须独立调用 `scripts/check_repository.py`，不能信任 hook 是否运行。


## 执行请求与启动诊断

跨执行器调度由 `agent-policy.manifest.yaml.agent_dispatch` 统一约束：Qoder 路径明确失败或不可用后，父代理必须实际调用原生 Codex 协作工具（模型及推理参数取自该策略）；只有同轮两条路径均失败才累计连续调度失败。成功接单清连续计数但保留历史。重复回调、自动 Goal 续轮、执行失败与验收失败均不是新的调度失败；到达上限停止该工作包自动派发，不以自动 Goal 的无进展轮次替代此计数。

Qoder 派发前必须检查当前真实父任务的宿主等待兼容性。Goal 活跃或状态无法证明、且没有受支持的外部等待接入时，不启动 Qoder，转入 Codex 回退路由；不能用调用者自报能力、修改宿主数据库或暂停 Goal 绕过。没有 Goal 的回调模式仍需通过原身份、并发与写域检查。此检查是仓库的兼容性护栏，不声称修改了 Codex 调度器；检查后的宿主模式变化不在该快照的证明范围内。

Codex 回退子代理使用原生协作事件等待和续办，不要求 Qoder 的外部终态回调。父代理提交的派发结果必须绑定当前 attempt 和真实工具记录；字符串 `PASS`、任意 agent 名称或计划说明不能证明接单成功。启动状态未知或本任务已在途时，不得触发另一执行器重复工作。Qoder 运行/返工预算与跨执行器调度失败上限分别核算。

`start/resume` 返回 Codex 回退派发动作时，父代理保留原 handoff，按返回的模型和推理参数调用原生 `spawn_agent`，并在子任务说明中绑定返回的工作包与 `attempt_id`。随后只提交当前父会话中该原生调用的 `call_id`：

```bash
python3 scripts/agents/qoder_task.py record-fallback --task <task.json> --attempt-id <attempt-id> --call-id <native-call-id>
```

该接口只读取对应的真实工具记录，不接收调用者编写的成功回执。缺失、未知或不匹配的响应不能清计数或再次派发；已消费的同一事件不得重复累计。派发成功只表示接单，不表示子任务实现或验收通过。派发停止只停止该工作包，不取消已运行的其他任务，不自动改变 Goal 状态。

Codex 回退子代理接单后，该工作包保持在途，不能因启动计数清零就再次派发。收到原生完成事件后，用宿主的单次 `list_agents` 终态快照作为证据，将对应调用的 `call_id` 提交给同一 `record-fallback` 接口；只接受已记录子代理句柄的完成事实。未知或仍在运行的状态不解除占用，也不自动重试。此动作不是定时轮询，更不是交付验收。

执行请求的收尾与接手规则见 `agent-policy.manifest.yaml.execution_progress` 和
`qoder_delegation.exhausted_budget_recovery`。预计超过 10 分钟且写入范围可隔离的任务，
应优先评估 Qoder；需要加载大量新上下文、但可由少量产物或确定性命令验收的子任务也应优先派发。
`qoder_task.py preflight --task <task.json>`
同时检查静态输入与只读的 `start` 资格快照；已知阻塞输出 `BLOCKED` 和
`blocking_findings`，非零退出。预检不创建运行、不证明在线账号健康或实际启动。
`start/resume` 仍在锁内复查；派发后按明确 run id 核对启动和回调，不能用预检
或“下一步会启动”替代实际进展。`planning/workstreams.yaml` 中的 Catalog 是项目任务清单；
它存在且与 handoff 一致时会提供 owner、版本、估时和文件范围的额外核对，但不是派发前提。
核心 handoff 已具备身份、受限写入范围、验收、验证命令、agent profile 与规则上下文时，
无关 Catalog 漂移或非核心范围冲突不得阻断派发。预算耗尽与阶段出口缺收据分别处理，不通过
改 Task identity 重置预算，也不把 Qoder 无法执行误判为必须停止整个 Task。

占用拒绝统一输出 `lexiflow.qoder-busy-routing.v1` 的 caller/owner repo、session、run、
归属分类与 `next_action`，不读取或回显其他任务的 prompt、日志或配置。只有 runner 记录同时
证明当前可信 Codex session、同一父 session、`AWAITING_CALLBACK` 和匹配 session 的 live Qoder
进程时，`next_action` 才是
`end-current-turn-await-callback`；同仓其他 session、跨仓/外部 CLI、lease、陈旧记录或未知
占用均为 `BLOCKED` 且 `fallback-to-codex-subagent`。主 Agent 使用宿主协作工具完成降级，保留
原工作包、Task/version、验收与已执行记录；runner 不伪造宿主派发，降级也不绕过写域冲突或
host-wide lease 防护。

Runner 加载 Qoder 的 `user,project,local` 配置源，保留用户已配置的模型/provider；
仓库 profile、禁止递归委派和显式权限参数仍由 runner 绑定。历史账号失败不是当前
健康探测；用户确认恢复后，`preflight` 与 `start/resume` 均接受
`--runtime-recovery-confirmed`。预检确认不持久化为启动授权，实际启动须再次显式传入，
且不跳过尝试预算、并发或未知运行护栏。
Qoder 落盘后用 `qoder_task.py validate-result <run-id>` 检查结果结构再结束；
此命令只验证 schema/identity，不结束运行、不发布 Gate、不将结果中的 PASS 当作验收。
派发时 runner 会在同一 run 目录写入 `result.template.json`：它已绑定身份并保留
`task_ids` 顺序的 `outcomes` 数组。Qoder 必须复制为 `result.json` 后再填写事实；模板
本身不是 result、不构成完成或验收，也不会让未写 `result.json` 的零退出通过。

`start/resume` 以 JSON 返回 `run_id`、`AWAITING_CALLBACK` 和 continuation 路径。
主线程收到 `end-current-turn-await-callback` 后立即结束本轮，不以 sleep、时钟、
status/result 或读取日志等待；只有终态回调唤醒后才核对产物并 ack。新运行未终态时
status/result 返回交接状态并退出 3；终态未 ack 不允许在该仓库继续派发。旧重复回调
若已经 ack/superseded，直接结束，不再验证或派发。Python runner 不能代替宿主终止
LLM 回合；结束回合是主 Agent 必须执行的交接动作。

同一 OS 用户所有 checkout 共用 `~/.cache/lexiflow/qoder-cli.lock`，锁由 dispatcher
传递给 worker 和 CLI，直到真实 CLI 退出；不把仓库锁或 ps 快照当作跨 checkout 互斥。
该机制覆盖本 Harness，外部直接启动的 Qoder 仅能通过进程预检发现。

## 正式验收入口

正式验收只通过 `python3 -m scripts.acceptance` 的具名场景执行。`submit` 消费已持久化的 PASS 日常变更报告，并自动绑定 catalog Task 要求、冻结输入、快照和 producer 来源；它不接受调用者提供 actor、session、descriptor 或 Task version。委派实现只可提供 `producer-run-id`，由验收从 agents 公开事实解析并摘要绑定。`validate` 必须在不同真实 Codex task/session 中运行，调用 `scripts.verification` 公开 API，并对声明、task source、冻结 artifacts 与快照 fail closed。

持久化和读取会验证完整 report schema、当前检查声明摘要、选择/执行覆盖、配置与输入指纹及输出 artifact；Task 缺少显式 `required_check_ids` 时正式提交失败关闭，等待 catalog 集成而不是退化为 baseline-only。`review` 与 `check` 还会只读重算完整冻结闭包，但不会执行检查命令。

`review` 只消费 submission、validation 和调用者明确给出的真实审查意见；reviewer 必须独立于 producer 与 validator，且不会启动交付命令。`check` 只校验 validation/review/dependency records、显式用户批准与内容 hash DAG，也不会启动交付命令。依赖或 approval 尚未到位时返回 `BLOCKED` 且不发布终态；条件补齐后可重核同一 submission，成功记录幂等返回。每类已发布记录均在 ignored `tmp/quality/acceptance/` 使用 UUID 与原子一次性发布；没有独立生产 session 时，fixture 结果只能证明工程链路，不能声称正式 PASS。


## 共享策略与文档治理

`agent-policy.manifest.yaml` 是共享调度策略真源；`policy-projections.yaml` 只描述兼容字段的投影位置。runtime、任务模板和 catalog 的字段形状保持兼容，修改共享值后显式执行：

```bash
python3 -m scripts.repository.policy_projection --write
python3 -m scripts.repository.policy_projection --check
python3 -m scripts.repository.docs_check
```

`--write` 只更新声明的投影字段，保留其他手写内容；`--check` 不修改文件。文档检查验证链接、锚点、图源围栏、入口大小和 docs 标题的层级十进制序号；重排标题时保留仍被引用的旧锚点。它要求父页保留核心模型和边界；仅在细节确有必要时放入同名子目录，且不规定子页数量、篇幅或模板。候选方案、方案比较和 A/B/C 选项不能进入 docs，文档只陈述已确定的方案供校验。它不替代中文语义、图文一致性或视觉审查。

## 本机 skill 接入

`documentation-policy.yaml` 声明两个按需技能。只从 `CODEX_HOME/skills`（默认用户主目录下的 `.codex/skills`）引用现有安装，不复制实现、不自动下载、不修改用户配置：

```bash
python3 -m scripts.repository.local_skills check
python3 -m scripts.repository.local_skills link
```

可用 `--source` 显式指定安装根目录；绝对路径只出现在本地检查结果中，不写入共享清单。逐 skill 链接位于忽略的 `.agents/skills/`。同目标重复执行不改变内容，已有不同目标拒绝覆盖，缺源返回 `BLOCKED`。Codex 发现这些链接后按需读取 `SKILL.md`；Qoder 通过项目入口显式读取，不依赖自动发现。

修改图先读 PlantUML skill；仅维护技能本身才读治理 skill。图源最终始终在 Markdown 内，但必须按 `documentation-policy.yaml.diagram_workflow` 操作：先在 `tmp/diagrams/<document-slug>/<diagram-id>/source.puml` 调试，校验、渲染并人工查看，再将**已校验的完整内容原样复制**到指定 `plantuml` 围栏，最后核对该围栏的来源哈希。图源改变就重新从临时源码校验；不得直接把未校验草稿写入正文。

`tmp/diagrams/` 保存 PUML 草稿、brief、SVG/PNG、回执和预览，均不提交。`docs/**/diagrams/` 不再用作调试目录；其中保留的 Markdown 索引只做导航。IDEA 设置中启用 Markdown 的 PlantUML 扩展，在正文预览核对图像；独立 PUML 预览不构成正文验收。

文档只维护每个主题的最新版：直接更新正文，合并后删除旧页；不建立历史目录、日期快照或复审副本。阶段状态只在状态页维护，规则和阈值只在指定真源维护。原始运行收据仍不可变，留在忽略目录，不转换为 docs 历史页。
