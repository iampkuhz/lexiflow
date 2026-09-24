# 1. 独立工具：局部检查与本机维护

> 位置：[工程地图](../overview.md) → [Reference](../reference.md) → 独立工具。这里不规定 workflow 顺序；被 Verify 选中时，它们按模块声明运行。

## 1.1. 只读检查器

| 要确认的事实 | 命令 | owner 与证明边界 |
| --- | --- | --- |
| 链接、标题、图源围栏等静态结构 | `python3 -m scripts.repository.docs_check` | repository；不证明中文语义、图像布局或正式验收 |
| policy 的确定性投影未漂移 | `python3 -m scripts.repository.policy_projection --check` | repository；不证明真实派发成功 |
| Catalog 的静态一致性 | `python3 -m scripts.repository.planning_check --root .` | repository；planning-only 合法不表示可派发或已验收 |
| 本机 skill 安装来源可用 | `python3 -m scripts.repository.local_skills check` | repository；不安装或自动下载 |
| 本仓 Hook 接入状态 | `python3 -m scripts.repository.hooks doctor` | repository；不执行完整 Verify |

检查器接受仓库/文件等显式输入，不要求前一阶段的 submission。它们可以被全局 Verify 编排；从总览下沉到此处不表示取消检查。

## 1.2. 有副作用的操作不是 check

以下只在明确需要改变本机或投影时使用，不能为了检查自动执行：

- `policy_projection --write`：更新声明的派生字段，保留手写内容；之后再 `--check`。
- `local_skills link`：创建 ignored `.agents/skills/` 链接，已有不同目标拒绝覆盖，缺源 BLOCKED；不修改用户安装。
- `hooks install`：修改本仓 Git hook 接入，只安装非阻断提醒；不自动运行 Verify，不把 commit 变成验收。

skill 来源由 `CODEX_HOME/skills`（默认 `~/.codex/skills`）定位，可用 `--source` 显式指定。绝对本机路径不写入共享 manifest。

## 1.3. 哪些命令不归这里

`acceptance status`、Qoder `status/result` 需要具体的 submission/run 与生命周期上下文，见[Acceptance](../change-delivery/acceptance.md)和 [Agent workflow](../agent-workflow.md)。Java task 见[原生检查](java-checks.md)，API 启动见[本地体验](../operations/local-experience.md)。

失败时保留原 reason、位置和证据，返回[按阶段排障](../troubleshooting.md)；不要把“局部检查通过”扩大为整个交付通过。
