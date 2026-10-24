# Agent 执行入口

流程、身份来源与交接见 [Agent workflow](../../docs/scenarios/delegate-work.md)。

`identity/` 保存可信原生身份绑定；`work_packages/` 核对冻结合同、发布与重核完成事实。

- 新派发使用当前父任务下、运行时实际支持的原生协作工具，不通过仓库 CLI 启动子代理。
- `identity/execution_identity.py`：共享 `discover(execution_target)` / `verify_proof(execution_target, proof, context)` 接口；目标是实际 Git 根，身份从受支持的原生适配器取得。当前仅注册 Codex 原生适配器，缺少适配器返回 `BLOCKED`，不接受自报身份或任意模块路径。
- `identity/codex_actor_identity.py`：当前 Codex 适配器及其唯一 `bind_work_package(repo_root, caller_contract)` 包级入口，内部认证原生身份并一次冻结由当前 Catalog 派生的完整包合同；`identity/codex_identity_binding.py` 不接受 caller 身份注入。
- `work_packages/codex_work_package_publisher.py` 的 `CodexWorkPackagePublisher(repo_root).publish(run_id, outcomes)` 发布每 Task projection/outcome 和最后唯一 completion；`verify(run_id)` 重核来源并完整派生内存 producer_view，不保存 Main projection、Task packet/plan 或 signal 镜像。`work_packages/verify_codex_work_package.py` 只解析跨 Agent 工作包核对命令。
- `../checks/tool_tests/run_agent_tool_tests.py`：运行 Agent 工具模块自身测试，不评判被委派任务是否完成或通过正式验收。

调度、模型和等待规则以 [Harness policy](../../harness/agent-policy.md) 为准。本模块不导入 Delivery Gate，不签发 validation/review；执行完成不等于验收。更多文件职责见 [Scripts Reference](../../docs/reference/scripts.md)。
