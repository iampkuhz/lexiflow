# 1. 校验：Harness 与派发

## 1.1. 配置改动

修改 `harness/agent-policy.manifest.yaml` 后先更新其声明的投影，再检查：

```bash
python3 -m scripts.repository.policy_projection --write
python3 -m scripts.repository.policy_projection --check
python3 -m scripts.repository.planning_check --root .
```

只改文档策略时不需要写 projection；仍应运行 `docs_check`。目录、Task 或模块检查声明变化时，
运行 `python3 -m scripts.repository.planning_check --root .` 与受影响模块的直接质量入口；不要把单一模块
诊断泛化为全仓验收。

## 1.2. 派发与回调

派发前读取 policy 中的 `subagent_protocol`、`qoder_delegation` 与模型策略；使用 Qoder 时由 `scripts/agents/qoder_task.py preflight` 和 `start/resume` 的锁内复查核对当前工作包。调用方不伪造 runner 身份；`start/resume` 只做一次有界握手：`STARTED` 表示已观察到真实 `started.json`，`STARTING` 表示握手时间内尚未观察到，`FAILED` 只表示已落盘的启动失败。Qoder 的完成记录先于回调，回调只提供待核对的信号。

`status <run-id>` 是单次、只读的记录诊断接口：在 `AWAITING_CALLBACK` 时仍返回 continuation 与 `next_action`，不等待、不看日志、不授权主任务主动轮询或继续派发。收到匹配终态回调后才核对任务、版本、允许范围、身份、规范产物和声明的验证证据。`ack`、进程退出零或 Qoder 文本都不是验收结论；故障诊断见[故障处理](troubleshooting.md)。

## 1.3. 宿主交接边界

已核对的 Codex 本机能力是显式派发、协作消息和事件等待；它没有仓库可调用的“在外部 Qoder callback 到达后自动暂停当前目标并自动续轮”接口。因而 runner 只返回 `end-current-turn-await-callback`，不把 JSON、提示词、`paused`/`blocked` 或 watchdog 记录称为宿主暂停/恢复。主任务必须实际结束当前轮，匹配终态 callback 后由宿主事件唤醒并核对、ack；若需要真正自动续轮，前置条件是宿主公开一个有界 callback-to-resume API，并可证明 callback target 与恢复目标的真实 session 绑定。

## 1.4. 结论

配置漂移或合同不一致为 `FAIL`；缺少授权、快照、身份或证据为 `BLOCKED`。实际交付检查与正式收据仍分别遵循[校验手册](../validation.md)和[Gate 控制面](../gate-control-plane.md)。
