# Agent 执行入口

先读 [Agent workflow](../../docs/development/agent-workflow.md)，再按当前阶段定位文件；[身份与运行事实](../../docs/development/agent-workflow/identity.md)解释 Session、callback 与 ack 的边界。

`delegation/` 只放从当前任务调用或核对另一 Agent 工作包的公开命令；`codex/` 与 `qoder/` 保存各执行器自身的身份、事实和运行能力。CLI 不拥有正式验收权限。

- `delegation/qoder_cli.py`：Qoder 的 preflight/start/resume/result/ack 等公开入口；`qoder/cli.py` 只解析参数，`qoder/runner.py` 实现派发和生命周期，`qoder/handoff.py` 核对交接合同。
- `local_codex_runtime.py`、`codex/runtime_binding.py`：真实本机身份来源。
- `codex/work_package.py`、`qoder/facts.py`：各 Agent 的工作包原始执行事实；`delegation/codex_cli.py` 只解析跨 Agent 工作包核对命令。
- `qoder/lifecycle.py`、`qoder/callback.py`：运行状态与终态交接。
- `module_tests.py`：运行 Agent 工具模块自身测试，不评判被委派任务是否完成或通过正式验收。

规则和模型选择只来自 [Harness policy](../../harness/agent-policy.manifest.yaml)。STARTING/未知状态不允许重复派发；Qoder 在 AWAITING_CALLBACK 时结束父任务当前轮，匹配终态后核对并 ack。Codex 子代理使用原生协作事件，不套用 Qoder 回调。

本模块不导入 Delivery Gate、不签发正式 validation/review。结果结构正确、exit-zero、queued 或 ack 均不能替代 Quality Gate PASS。内部文件职责见 [Scripts Reference](../../docs/development/reference/scripts.md)。
