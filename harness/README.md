# LexiFlow Harness

`harness/` 只保存静态、机器可读的仓库约束：

- `manifest.yaml`：项目类型、公开命令与文档入口。
- `agent-runtime.manifest.yaml`：客户端拥有 Session/checkout 的最小运行契约。
- `agent-policy.manifest.yaml`：隐私、Git、子任务、Qoder 调度和结果语义。
- `gate-check-registry.yaml`：按 catalog 顺序冻结的 fixed-command 检查清单。
- `phase-task-contract-profiles.yaml`：阶段文档型任务的封闭输入与语义断言；不包含自由命令。

`python3 -m scripts.gates.registry_profiles --root . --check` 验证 profile、catalog 与
registry 的确定性投影。`python3 -m scripts.gates.task_contracts --task-id <exact-id>`
只接受 profile 中的稳定 Task；`LF-TSK-ARCH-0008` 在缺少精确用户批准标记时返回
`BLOCKED`。

## Python 运行环境

Gate 使用 PyYAML；不要依赖系统 `python3` 恰好已安装。首次在本机执行：

```bash
python3 -m venv .local/lexiflow-python
.local/lexiflow-python/bin/pip install -r requirements-dev.txt
```

之后将上述解释器替换为命令前缀，例如
`.local/lexiflow-python/bin/python -m scripts.gates.planning --root .`。`.local/` 是忽略的本机运行环境，不是 Gate evidence。

运行证据写入 ignored 的 `tmp/quality/runs/<run-id>/`；Qoder 任务写入
`tmp/qoder-tasks/<run-id>/`。Harness 不保存产品运行状态，也不把 active change 当成普通写入的权限令牌。

## 执行请求与启动诊断

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

## 本机 Gate 身份与收据入口

先在当前仓库的 Codex 任务中运行 `python3 scripts/gates/cli.py doctor`。它只核对本机
runtime 与 authority 配置，不运行交付检查、不签发验收。默认 adapter 读取 Codex
session metadata 首行并核对实际 session、用户和 workspace；环境变量仅用于定位。
这是**信任本机用户的来源校验**，不是平台加密认证，不能抵御同一用户恶意改写记录。
同一 session 始终是同一 actor；同宿主无独立 session 的子代理不能充当独立签发者。

1. 产物执行者先完成当前 Task，保存真实 diff、snapshot、测试记录及完整结果。
   Main singleton 可用 `python3 -m scripts.harness.local_codex_runtime --task-id <exact-id>`
   绑定真实身份和当前合同；该命令只生成 binding/projection，不声称工作完成。
2. 执行者使用 `scripts/harness/codex_work_package.py` / `scripts/gates/evidence_packet.py`
   现有 materializer 冻结完整来源，明确交付唯一 evidence packet locator。
   `LEXIFLOW_GATE_EVIDENCE_PACKET` 只是这个路径的传递方式，不是用户编写的 identity JSON。
3. 在**不同真实 Codex 任务会话**执行
   `python3 scripts/gates/cli.py run --mode incremental --evidence-packet <locator>`。
   未传 issuer 时，由 adapter 自动生成一次新鲜 authority evidence 与 issuer；显式传入的
   过期 issuer 不会偷偷替换，旧文件也不会更新时间。该命令实际执行 TASK_VALIDATION。
4. 独立 reviewer 消费 validation receipt、冻结 diff 和只读 review evidence；随后按原有
   INDEPENDENT_REVIEW、CATALOG_DECISION 合同串行发布收据，后两层不得重跑交付命令。
   Reviewer 必须同时独立于产物执行者和 validation issuer，不能只换 actor 名称。
   review/catalog 的 typed evidence 先用
   `python3 scripts/gates/evidence_packet.py publish-layer-evidence --input <canonical-json> --publication-id <uuid-v4>`
   发布成不可变 descriptor，再由 `prepare-layer` 绑定到本层 packet；该命令只保存 reviewer
   已明确给出的 decision/findings/receipt descriptors，绝不默认 PASS 或签发 Gate。
5. G1 闭包及出口的三层收据全部 current/PASS，且用户批准有效，才可更新阶段状态。
   `doctor`、绑定成功或静态检查 PASS 均不能替代该条件。

需要先审阅纯 plan 时，先显式运行
`python3 scripts/gates/cli.py prepare-issuer --evidence-packet <locator> --receipt-kind TASK_VALIDATION`，
再把返回的 issuer locator 传给 `plan --issuer-packet`。`plan` 本身仍然零写入。
缺 subject evidence、真实来源失效、输入漂移或自审均应停止并报告具体缺项；不再要求
用户提供本仓库未接入的 Desktop 受保护 attestation 服务，也不扫描旧目录猜测本次 evidence。

`planning/workstreams.yaml` 是全量 catalog。Task receipt 的 `task_source.sha256` 是目标 Task
加所属 workstream 的 canonical projection hash，而不是整份 YAML 的 hash；因此其他 Task 的
新增、版本更新或状态改动不会使该 Task 的 receipt 过期，目标 Task 自身、owner 或依赖改动仍会。

## 共享策略与文档治理

`agent-policy.manifest.yaml` 是共享调度策略真源；`policy-projections.yaml` 只描述兼容字段的投影位置。runtime、任务模板和 catalog 的字段形状保持兼容，修改共享值后显式执行：

```bash
python3 -m scripts.harness.policy_projection --write
python3 -m scripts.harness.policy_projection --check
python3 -m scripts.harness.docs_check
```

`--write` 只更新声明的投影字段，保留其他手写内容；`--check` 不修改文件。文档检查验证链接、锚点、图源围栏、入口大小和 docs 标题的层级十进制序号；重排标题时保留仍被引用的旧锚点。它要求父页保留核心模型和边界；仅在细节确有必要时放入同名子目录，且不规定子页数量、篇幅或模板。候选方案、方案比较和 A/B/C 选项不能进入 docs，文档只陈述已确定的方案供校验。它不替代中文语义、图文一致性或视觉审查。

## 本机 skill 接入

`documentation-policy.yaml` 声明两个按需技能。只从 `CODEX_HOME/skills`（默认用户主目录下的 `.codex/skills`）引用现有安装，不复制实现、不自动下载、不修改用户配置：

```bash
python3 -m scripts.harness.local_skills check
python3 -m scripts.harness.local_skills link
```

可用 `--source` 显式指定安装根目录；绝对路径只出现在本地检查结果中，不写入共享清单。逐 skill 链接位于忽略的 `.agents/skills/`。同目标重复执行不改变内容，已有不同目标拒绝覆盖，缺源返回 `BLOCKED`。Codex 发现这些链接后按需读取 `SKILL.md`；Qoder 通过项目入口显式读取，不依赖自动发现。

修改图先读 PlantUML skill；仅维护技能本身才读治理 skill。图源最终始终在 Markdown 内，但必须按 `documentation-policy.yaml.diagram_workflow` 操作：先在 `tmp/diagrams/<document-slug>/<diagram-id>/source.puml` 调试，校验、渲染并人工查看，再将**已校验的完整内容原样复制**到指定 `plantuml` 围栏，最后核对该围栏的来源哈希。图源改变就重新从临时源码校验；不得直接把未校验草稿写入正文。

`tmp/diagrams/` 保存 PUML 草稿、brief、SVG/PNG、回执和预览，均不提交。`docs/**/diagrams/` 不再用作调试目录；其中保留的 Markdown 索引只做导航。IDEA 设置中启用 Markdown 的 PlantUML 扩展，在正文预览核对图像；独立 PUML 预览不构成正文验收。

文档只维护每个主题的最新版：直接更新正文，合并后删除旧页；不建立历史目录、日期快照或复审副本。阶段状态只在状态页维护，规则和阈值只在指定真源维护。原始运行收据仍不可变，留在忽略目录，不转换为 docs 历史页。
