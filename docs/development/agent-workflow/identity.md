# 1. 身份与运行事实：为什么不能手填 PASS

> 位置：[工程地图](../overview.md) → [Agent workflow](../agent-workflow.md) → 身份与事实。本页解释执行来源、生命周期和验收消费者，不改变 policy 的身份或回调规则。

## 1.1. 身份从哪里来

[local_codex_runtime.py](../../../scripts/agents/local_codex_runtime.py) 从可信本机 Session 来源发现身份，[runtime_binding.py](../../../scripts/agents/codex/runtime_binding.py) 绑定来源。actor、Session 和 run 不可由调用者自报。这里是可信本机用户边界，不冒充平台密码学证明。

[codex/work_package.py](../../../scripts/agents/codex/work_package.py) 发布工作包原始记录；[delegation/codex_cli.py](../../../scripts/agents/delegation/codex_cli.py) 只接收精确 run ID 并调用核对器。Qoder 的 [facts.py](../../../scripts/agents/qoder/facts.py) 核对 task/completion/result。Delivery Gate 通过 producer 解析这些事实；执行模块不导入 Delivery Gate，也不签发 validation/review。

## 1.2. Qoder 的生命周期边界

[lifecycle.py](../../../scripts/agents/qoder/lifecycle.py) 管理 started、终态、watchdog 和 ack；[callback.py](../../../scripts/agents/qoder/callback.py) 投递终态信号。完成记录先于 callback，匹配真实 run/Session 后才能消费。

同一 OS 用户的所有 checkout 共用 host lock，由 dispatcher 传递给 worker/CLI 直到真实退出。仅有 PID、旧文件或 repo-local lock 不证明本次占用归属。新运行未终态时 status/result 返回交接状态；终态未 ack 时不能继续派发。

runner 使用用户既有配置源和 Provider，不因仓库 profile 覆盖个人模型配置。历史账号故障不是当前健康证据；用户确认恢复时可显式提供 runtime-recovery-confirmed，但 preflight 确认不自动成为 start 的授权，运行预算和未知状态护栏仍保留。

## 1.3. fallback 与原生工具证据

[dispatch_fallback.py](../../../scripts/agents/dispatch_fallback.py) 绑定 attempt、工作包、父 Session 与真实宿主工具记录。父任务收到 fallback 动作后按返回模型/推理参数调用原生协作工具，再将实际 call_id 提交给同一 attempt；不能用“已接单”字符串或编造 agent 名称代替。

原生终态通过相应 agent 的真实终态快照核对，仍在运行或未知状态不解除在途占用。原生等待不是 Qoder callback 轮询，也不生成正式 receipt。调度停止只停止该工作包，不取消其他正在运行的任务或更改宿主 Goal。

## 1.4. 记录在哪里、怎样恢复

Qoder 原始产物在 ignored `tmp/qoder-tasks/<run-id>/`，Codex 工作包产物在 ignored `tmp/quality/codex-work-packages/`。result.template.json 只是绑定身份的填写模板，不能当完成结果；必须提供符合 schema 的 result.json，exit-zero 不等于 PASS。

保持原 Task 与历史证据，按[主流程](../agent-workflow.md)处理明确终态和接手。不能通过修改 identity、删除记录、改阈值或自动循环重试消除阻塞；问题无法确定时进入[按阶段排障](../troubleshooting.md)。
