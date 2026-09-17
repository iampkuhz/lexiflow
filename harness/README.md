# LexiFlow Harness

`harness/` 只保存静态、机器可读的仓库约束：

- `manifest.yaml`：项目类型、公开命令与文档入口。
- `agent-runtime.manifest.yaml`：客户端拥有 Session/checkout 的最小运行契约。
- `agent-policy.manifest.yaml`：隐私、Git、子任务、Qoder 调度和结果语义。
- `gate-check-registry.yaml`：按 catalog 顺序冻结的 fixed-command 检查清单。
- `g1-task-contract-profiles.yaml`：G1 文档型任务的封闭输入与语义断言；不包含自由命令。

`python3 -m scripts.gates.registry_profiles --root . --check` 验证 profile、catalog 与
registry 的确定性投影。`python3 -m scripts.gates.task_contracts --task-id <exact-id>`
只接受 profile 中的稳定 Task；`LF-TSK-ARCH-0008` 在缺少精确用户批准标记时返回
`BLOCKED`。

运行证据写入 ignored 的 `tmp/quality/runs/<run-id>/`；Qoder 任务写入
`tmp/qoder-tasks/<run-id>/`。Harness 不保存产品运行状态，也不把 active change 当成普通写入的权限令牌。
