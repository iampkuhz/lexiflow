# Agent 执行入口

先读 [Agent workflow](../../docs/development/agent-workflow.md)，再按当前阶段定位文件；[身份与运行事实](../../docs/development/agent-workflow/identity.md)解释 Session、callback 与 ack 的边界。

- `qoder_task.py`：Qoder 的 preflight/start/resume/result/ack 等公开操作。
- `local_codex_runtime.py`、`codex/runtime_binding.py`：真实本机身份来源。
- `codex_work_package.py`、`qoder/facts.py`：工作包原始执行事实。
- `qoder/lifecycle.py`、`qoder/callback.py`：运行状态与终态交接。

规则和模型选择只来自 [Harness policy](../../harness/agent-policy.manifest.yaml)。STARTING/未知状态不允许重复派发；Qoder 在 AWAITING_CALLBACK 时结束父任务当前轮，匹配终态后核对并 ack。Codex 子代理使用原生协作事件，不套用 Qoder 回调。

本模块不导入 Acceptance、不签发正式 validation/review。结果结构正确、exit-zero、queued 或 ack 均不能替代 Quality Gate PASS。内部文件职责见 [Scripts Reference](../../docs/development/reference/scripts.md)。
