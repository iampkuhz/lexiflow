# 1. 校验：Harness 与派发

## 1.1. 配置改动

修改 `harness/agent-policy.manifest.yaml` 后先更新其声明的投影，再检查：

```bash
python3 -m scripts.harness.policy_projection --write
python3 -m scripts.harness.policy_projection --check
python3 -m scripts.gates.registry_profiles --root . --check
```

只改文档策略时不需要写 projection；仍应运行 `docs_check`。目录、任务、注册表或 profile 的改动，按受影响任务运行 `task_contracts`，不要把单一任务诊断泛化为全仓验收。

## 1.2. 派发与回调

派发前读取 policy 中的 `subagent_protocol`、`qoder_delegation` 与模型策略；实际工作包必须通过[派发预检](../dispatch-preflight.md)。调用方不伪造 runner 身份；Qoder 的完成记录先于回调，回调只提供待核对的信号。

收到回调后核对任务、版本、允许范围、身份、规范产物和声明的验证证据。`ack`、进程退出零或 Qoder 文本都不是验收结论；故障诊断见[故障处理](troubleshooting.md)。

## 1.3. 结论

配置漂移或合同不一致为 `FAIL`；缺少授权、快照、身份或证据为 `BLOCKED`。实际交付检查与正式收据仍分别遵循[校验手册](../validation.md)和[Gate 控制面](../gate-control-plane.md)。
