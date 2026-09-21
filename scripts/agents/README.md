# Agent execution

`qoder_task.py` 是唯一 Qoder 生命周期入口；Codex identity、工作包原始完成记录和 Qoder 运行都由本目录拥有。共享调度、规模、身份、验证与回调规则只在
[`harness/README.md`](../../harness/README.md)、
[`agent-policy.manifest.yaml`](../../harness/agent-policy.manifest.yaml) 维护。

```bash
python3 scripts/agents/qoder_task.py preflight --task <task.json>
python3 scripts/agents/qoder_task.py start --task <task.json>
```

`start/resume` 返回 JSON（含 `run_id`、一次有界 `startup_handshake` 与 `next_action`）；`STARTING` 仅表示握手时间内尚未观察到 `started.json`，不是失败或重试许可。，调用者必须结束当前回合，
等终态 callback 后才调用以下命令，不要串接在派发命令后，也不要定时查询：

```bash
python3 scripts/agents/qoder_task.py result <run-id>
python3 scripts/agents/qoder_task.py ack <run-id> --parent-session-id <uuid>
```

原始身份与工作包完成事实由本目录发布；`scripts/acceptance/submit.py` 消费经过核对的事实，将其绑定到送验输入。本目录不生成正式验收材料或发布验收结论。Qoder 写入结果后使用 `validate-result <run-id>` 只读检查结构，再结束自身执行。
退出零、结构正确、queued 或 ack 均不是 Gate PASS。允许/禁止路径是协作合同，
不是操作系统沙箱；产物验收遵守共享的三层职责，review/catalog 不重跑交付命令。
