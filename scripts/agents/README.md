# Agent 执行入口

流程见 [Agent workflow](../../docs/development/agent-workflow.md)，Session/callback/ack 见[身份与运行事实](../../docs/development/agent-workflow/identity.md)。

`delegation/` 只放从当前任务调用或核对另一 Agent 工作包的公开命令；`codex/` 与 `qoder/` 保存各执行器自身的身份、事实和运行能力。

- `delegation/qoder_cli.py`：Qoder 的 preflight/start/resume/result/ack 等公开入口；`qoder/cli.py` 只解析参数，`qoder/runner.py` 实现派发和生命周期，`qoder/handoff.py` 核对交接合同。
- `local_codex_runtime.py`、`codex/runtime_binding.py`：真实本机身份来源。
- `codex/work_package.py`、`qoder/facts.py`：各 Agent 的工作包原始执行事实；`delegation/codex_cli.py` 只解析跨 Agent 工作包核对命令。
- `qoder/lifecycle.py`、`qoder/callback.py`：运行状态与终态交接。
- `module_tests.py`：运行 Agent 工具模块自身测试，不评判被委派任务是否完成或通过正式验收。

调度、模型和等待规则以 [Harness policy](../../harness/agent-policy.manifest.yaml) 为准。本模块不导入 Delivery Gate，不签发 validation/review；执行完成不等于验收。更多文件职责见 [Scripts Reference](../../docs/development/reference/scripts.md)。
