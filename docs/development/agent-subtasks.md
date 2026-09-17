# Agent 子任务协议

LexiFlow 用稳定 Task id 管理工作，用 run id 管理一次具体执行。Task 可以经历设计、实现、审查、返工和验证多个 run；run 的结束不改变 Task 的验收状态。

## 分工

- 主 Agent：维护架构、DAG、文件 ownership、决策与最终验收。
- Explorer/Designer：只读或只写设计范围，给出来源和未决假设。
- Codex Coder：一次只实现一个边界完整的多 Task Work Package，并提交逐 Task outcome evidence 与关联测试。
- Qoder：一次实现一个 180–360 分钟、至少包含两个同边界原子 Task 的 Work Package；全局同时最多一个 run，不能递归委派。
- Reviewer：从验收条件反查缺陷，不接受实现者的成功声明作为证据。
- Verifier：运行定向和增量 Gate，保存不可覆盖的证据。

仓库在 `.codex/agents/` 提供五个可复用角色入口：`architecture-reviewer`、`domain-designer`、`backend-implementer`、`extension-implementer`、`qa-verifier`。领域规则仍以 `AGENTS.md`、OpenSpec 与 Harness 为准，角色文件只说明任务类型和输出格式。

`.qoder/agents/` 提供 `backend-implementer`、`extension-implementer` 与只读 `quality-verifier`；实际 Qoder 委派仍统一通过非阻塞 runner，以保证单 run、持久 completion 和精确父 Session 回调。

Qoder 只有一个活动 run。主 Agent 可并行处理没有读写冲突的工作；接口集成和最终 Gate 串行执行。

Codex Sub-Agent 默认使用 `gpt-5.6-luna`，每次 dispatch 显式传递 model、reasoning 与 `fork_turns=none`。routine 使用 low，其他有界工作包使用 medium；复杂、混合或未分类不自动转 Terra。升级到 Terra 必须由父 Agent 记录具体失败或未覆盖风险证据，先确认失败是否来自 harness/上下文；Sol/Astra 的限制以共享策略为准。权威模型选择规则只在 `harness/agent-policy.manifest.yaml` 维护，角色入口不固定模型。该策略不改变 Qoder runner 的模型控制。

为控制上下文与 token，Qoder 每个 Task 最多一个初始 run 和一个修正 run，完整 prompt 上限为 8,000 字符；长设计通过文件和章节定位传递。Codex Sub-Agent 同时最多 1 个，默认 `fork_turns=none`，通过精确文件和章节定位承接边界完整的 2–6 小时工作包。完整历史继承必须作为显式例外记录。

Catalog 的 20–90 分钟 Task 是 DAG、owner 与验收的原子单位，不再机械映射成一个 Sub-Agent。实际委派把同一 owner、同一 contract 边界且写入范围兼容的至少 2 个连续 Task 合并为工作包；总预计时间至少 120 分钟，目标不超过 360 分钟，一次交付设计或实现、直接测试和验收证据。不足 120 分钟、单文件修补、单命令验证、孤立只读审阅留给主 Agent。调用次数、turn 数或 token 数不作为完成证据；最终仍以产物和主 Agent 独立验证为准。

Codex 与 Qoder 工作包都使用稳定 `work_package_id` 与精确有序的 `task_ids[]`，每个 Task 仍分别留下 outcome evidence。回调只传 `status`、`work_package_id`、`task_ids`、runner 生成的 `run_id`、产物 locator、验证命令和最多 3 条阻断发现。不回传完整文件、测试日志或大段上下文。Codex 完成产物只使用 canonical result schema；主 Agent 用固定 verify 命令核对精确 identity、current projection 与 artifact hashes，再按风险选择必要功能验证；signal 本身不是 Task acceptance PASS 证据。

## Qoder Task handoff

Qoder handoff 除原有目标、anchor Task、范围和验收字段外，还必须提供 `work_package_id`、`task_ids`、`task_versions`、`change_versions`、`estimated_minutes`、`primary_owner`、`contract_boundary`、`agent_profile` 与 `harness_manifest`。`task_ids` 至少两个且顺序固定，`estimated_minutes` 必须精确等于当前 catalog Task 估时之和并落在 180–360 分钟；全部 Task 必须具有相同 owner、allowed/forbidden scope 和 file claims。Harness manifest 使用 `lexiflow.qoder-harness.v1`，哈希绑定根/Qoder AGENTS、agent profile、当前 catalog、机器策略、适用 OpenSpec 和实现上下文，并声明启动前必须可用的工具与无 shell validation argv。

Runner 只接受 `parent_client=codex`。在 dispatch 时，`parent_session_id` 可由调用者提供或从 `CODEX_THREAD_ID` 注入，落盘前必须存在并通过 UUID 校验；`agent_id` 与 `run_id` 由 runner 自动生成，调用者不得在 start task JSON 预填；`session_id` 由 start 生成或从 resume record 复用；`client` 强制为 `qoder`。因此 `run_id` 是 start 的返回值，不是 start 的输入。

每个 Catalog Task 继续控制在 20–90 分钟，用于 DAG、owner 和独立验收。Qoder 不再为每个原子 Task 建 session；调度器把至少两个同 owner、同 contract、完全相同写域与 file claims 的连续 Task 聚合为 180–360 分钟 package。实现与直接测试保留在同一 package；独立复核仍使用不同身份和 run。

结果必须报告 `result_required_output`：`status`、`work_package_id`、`task_ids`、`task_versions`、`change_versions`、`run_id`、`outcomes`、`changed_files`、`validation`、`acceptance_evidence`、`effect_checks`、`risks`。`PASS` 只表示任务要求的检查完整通过；`BLOCKED` 表示检查执行后发现阻断；`FAIL` 表示检查未完成或无法判定。`queued`、`ack`、退出 0 和 `NOT_TRIGGERED` 都不是 PASS。

`scripts/harness/qoder_task.py` 已强制 23 个 caller field、精确 Task/change 版本、catalog 估时与写域、非空验收列表，并拒绝调用者预填 `agent_id`、`run_id`、`session_id`、`client`。Runner 在落盘前绑定身份、项目 Agent 和哈希上下文，在 prompt 中要求 12 个结构化结果字段；`tests/harness/test_qoder_runner.py` 是静态契约的 conformance check。主 Agent 仍须验证实际输出和 diff，runner conformance 不替代任务验收。

## Codex Work Package handoff

Canonical 产物入口、布局与身份隔离见 [`current-execution-evidence.md`](current-execution-evidence.md)；机器规则以 `harness/agent-policy.manifest.yaml` 与其 result schema 为准。Publisher 不自动执行 subprocess 或 Gate checks；Main 不能通过 publisher 的结构完整性 PASS 推断业务或 G1 已完成。

Codex handoff 必须提供 `goal`、稳定 `work_package_id`、精确有序的 `task_ids`、对应 `task_versions` 与 `change_versions`、总 `estimated_minutes`、`primary_owner`、`contract_boundary`、`allowed_files`、`forbidden_files`、`required_context`、`expected_outputs_by_task`、`acceptance_by_task`、`validation_commands`、`failure_policy` 和 `parent_client`。`task_ids` 至少包含两个 catalog Task，总预计时间不得少于 120 分钟；owner、contract 边界和写范围不兼容时必须拆成串行工作包。

`work_package_id` 是调用者稳定的计划身份，不是运行身份。Codex runtime 绑定 `agent_id`；Sub-Agent 在启动后只生成一次唯一 `run_id`，调用者不得预填两者。完成产物必须逐 Task 记录 outcome、文件与验收证据；单一 package `PASS` 不能抹平某个 Task 的 `BLOCKED` 或 `FAIL`。

## 完成与兜底

Worker 先原子保存 completion，再向精确父 Session 回调。主 LLM 不用 `sleep`、进程查询或 `status/result` 循环维持回合。LLM 兜底首查必须在派发后 **至少 300 秒**，仍无新证据时后续两次 LLM 查询之间必须 **至少 600 秒**；回调到达可立即处理，不受该间隔限制。

后台 watchdog 是独立的 non-LLM 机制：首次在 300 秒后检查，之后每 600 秒一次，运行中无变化时保持静默。watchdog 的检查不能被计作或触发一次 LLM 轮询。

## Qoder 入口

任务 JSON 放在 ignored 的临时位置，然后执行：

```bash
python3 scripts/harness/qoder_task.py preflight --task <task.json>
python3 scripts/harness/qoder_task.py start --task <task.json>
```

`preflight` 不创建 run，先验证当前 catalog、估时、写域、Agent profile、上下文哈希、工具 probe 和 validation argv；通过后 `start` 才进入单 Qoder 派发窗口并立即返回 run id。最终验收由主 Agent重新运行任务指定检查和仓库增量 Gate。提示里的允许路径是协作契约，不是 OS 沙箱，因此复核时必须检查真实 diff。
