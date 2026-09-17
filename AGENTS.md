# LexiFlow Agent 工程规则

先定边界，再做最小闭环。

## 首要护栏

- 中文沟通；标识符、命令、路径与 API 使用英文。
- 先搜索定位，只读取必要上下文；不覆盖或回滚他人改动。
- 不提交密钥、token、真实用户字幕、观看历史、Vocabulary Profile、模型输入输出或本地运行数据。
- 非平凡产品、agent、harness、gate 或跨模块变更先创建或复用 `openspec/changes/<id>/`。
- 共享规则只在 `AGENTS.md` 与 `harness/` 维护；客户端配置只保留入口。

## 架构约束

- 产品后端统一使用 Java 25；Spring Boot 只进入 `backend/apps/`、`backend/platform/` 与组合根。Python 只用于 `scripts/`、`harness/`、Gate、生成器和审计，不得承载产品 Domain、Application、API 或 worker 业务逻辑。
- 采用 Modular Monolith；部署可为 `api` 与 `worker` 两个进程，业务边界不得因此拆成微服务。
- 依赖由组合根指向 application，再指向 domain/ports；domain 不依赖 HTTP、数据库、缓存或具体模型 Provider。
- 模块不得直接读写其他 Domain 拥有的数据；跨模块只通过公开 contract。
- Chrome Extension 保持薄，服务端 Vocabulary Profile 是跨设备事实来源。
- 英文字幕渲染不得等待后端或模型；Rules 决定是否提示，Models 决定语境含义。
- PostgreSQL 是事实存储；Redis、浏览器缓存和投影均可重建。

## 子任务与调度

- 每个 handoff 必须遵守 `harness/agent-policy.manifest.yaml` 的分层契约：调用者提供稳定 Task/change 版本、范围、验收和失败策略；runner 绑定 parent/session/agent/run/client 身份。
- 调用者不得预填 runner identity；每个实例使用唯一 `agent_id` 与 `run_id`。并行写入范围不得重叠；集成验证串行。
- Qoder 同时最多 1 个 run；状态不明不得补开。每个 Task 最多 1 个初始 run 和 1 个修正 run；完整 prompt 不得超过 8,000 字符。Qoder 不得递归委派。
- Codex Sub-Agent 同时最多 1 个，默认 `fork_turns=none`。每次新派发必须显式传 `model=gpt-5.6-luna`；routine 工作包按共享策略使用 `reasoning_effort=low`，复杂、混合或未分类的有界工作包默认仍用 Luna 与 `reasoning_effort=medium`。Terra 只作为父 Agent 依据具体失败或未覆盖风险证据决定的升级，不得因角色或任务类别自动选择。先定位失败层，修复 harness/上下文问题不能冒充 Luna 能力不足。选择模型、原因与证据记录在现有 dispatch note，不扩展 frozen handoff schema；完整策略仅在 `harness/agent-policy.manifest.yaml`。Sol 仍需 Terra 不足证据或预声明高风险理由，Astra 仅接受用户明确要求；完整历史继承必须记录为例外。
- Codex Sub-Agent 的模型策略不改变 Qoder；Qoder 仍由独立 runner、agent profile 和 hash-bound harness manifest 控制。两者都必须只回调紧凑 signal，不回传中间脚本、日志或完整上下文。
- Catalog 中 20–90 分钟的原子 Task 用于依赖和验收追踪，不直接等于一次委派。Codex 工作包至少 120 分钟；Qoder 工作包必须为 180–360 分钟，且估时必须精确等于当前 catalog 内全部 `task_ids` 的估时之和。两者都必须在同一 owner/contract/写入边界内合并至少 2 个连续 Task；Qoder Task 还必须具有完全相同的 allowed/forbidden scope 与 file claims。工作包交付完整实现、直接测试和逐 Task 证据；小修补、单命令验证或孤立只读审阅由主 Agent 完成。
- Codex 与 Qoder 工作包都使用稳定 `work_package_id`、精确有序的 `task_ids[]` 与 runner 生成的唯一 `run_id`，并为每个原子 Task 留下 outcome evidence。Qoder 还必须绑定 `agent_profile` 与 hash-bound `harness_manifest`，并在 `start` 前通过 catalog、上下文哈希、工具 probe 与 validation argv preflight。Sub-Agent 完成后只回调这些身份、状态、产物 locator、验证命令及最多 3 条阻断发现；不要回传全文、日志或长上下文。主 Agent 用固定命令检查 canonical artifacts 与 hash，再按风险选择必要功能验证，不无条件重跑全部交付测试。
- Codex 工作包进入 Gate 时，必须为每个目标 Task 保存一个 `lexiflow.codex-work-package-task-projection.v1` raw task：完整绑定同一 caller contract、稳定 `work_package_id`、精确有序且至少两项的 `task_ids[]`、目标 Task 和 runner identity；不得包含 Qoder 的 `permission_mode`、`_resume_mode` 或 `title`。Qoder initial/resume 的持久化形状保持独立。
- Main 的孤立单 Task 使用显式 `lexiflow.codex-main-task-projection.v1` 来源，须由受信 runtime 证明实际 Main actor并按 current catalog 绑定唯一 Task；它不得放宽 Sub-Agent handoff 的至少两 Task 规模要求，不得跨 owner 合包或伪装 Qoder。Main producer 仍不能自签 validation/review。
- Codex 完成产物只使用 `harness/agent-policy.manifest.yaml` 的 canonical result schema 和固定 structured layout。`client` 是工具类型，不能单独证明 identity 复制；session 是真实宿主会话与路由身份，共享 session 不等于共享 actor。可信 issuer 必须具有与 subject 不同且可认证的 actor/agent 与 run/instance；独立 review 还必须排除 validation issuer，同一 actor 换 run 或 attestation 仍不得自审。禁止随机捏造缺失的 runtime parent/session。
- 完成记录先落盘，再回调精确父会话。主 LLM 禁止循环轮询；首次兜底检查不得早于 300 秒，后续不得频于 600 秒。
- `queued`、`ack`、进程退出 0、未触发或未运行均不等于验收通过。

## 验证与 Git

- Java 源码格式、静态分析、注释、架构和测试规则由 Gradle 下的 Spotless、Checkstyle、PMD、Java quality gate、ArchUnit、JUnit 与 JaCoCo 唯一执行；Python Gate 只负责规划、编排、证据、收据和跨产物治理，不得重复扫描 Java 源码实现同义规则。
- 功能交付只在 `TASK_VALIDATION` 执行确定性检查和业务测试；`INDEPENDENT_REVIEW` 只复核冻结 diff 与 validation evidence；`CATALOG_DECISION` 只验证 validation/review/dependency receipt 和 hash DAG。后两层不得重跑交付命令。
- 结果只使用 `PASS`、`BLOCKED`、`FAIL`；必需检查未运行或跳过不得称 `PASS`。
- 交接前显式运行 `python3 scripts/gates/cli.py run --mode incremental`；业务代码还需运行其产品测试。
- 禁止自动 stage、commit、merge、rebase、reset、stash、force 或 push。Git 集成与发布只按用户明确指令执行。

长期架构与阶段真相在 `openspec/specs/`；当前变更与验收任务在 `openspec/changes/`；机器契约在 `harness/`。
