# 1. 独立工具：局部检查与本机维护

> 位置：[工程地图](../README.md) → [Reference](../reference.md) → 独立工具。这里仅列无持久运行状态的只读能力；被 Verify 选中时，它们按模块声明运行。带报告／submission 状态的命令仍归对应 workflow，见[Verify](verification.md)与[Delivery Gate](delivery-gate.md)。

## 1.1. 只读检查器

| 要确认的事实 | 命令 | 源码与证明边界 |
| --- | --- | --- |
| 链接、标题、图源围栏等静态结构 | `python3 -m scripts.repository.docs_check` | [docs_check.py](../../scripts/repository/docs_check.py)；不证明中文语义、图像布局或正式验收 |
| 唯一策略合同可读取 | `python3 -m scripts.repository.quality --mode policy` | [quality.py](../../scripts/repository/quality.py)；不证明真实派发成功 |
| Catalog 的静态一致性 | `python3 -m scripts.repository.planning_check` | [planning_check.py](../../scripts/repository/planning_check.py)；planning-only 合法不表示可派发或已验收 |
| 本机 skill 安装来源可用 | `python3 -m scripts.repository.local_skills check` | [local_skills.py](../../scripts/repository/local_skills.py)；不安装或自动下载 |
| scripts 风格、格式、模块文档字符串 | `python3 -m scripts.repository.python_quality` | [python_quality.py](../../scripts/repository/python_quality.py)；规则在 Harness，不自动 fix 或安装 |
| 本仓 Git Hook 接入状态 | `python3 -m scripts.repository.hooks doctor` | [hooks.py](../../scripts/repository/hooks.py)；不执行完整 Verify |

日常检查器从仓库入口定位规则与文件，不接受 root 覆盖；隔离测试在函数层注入 root。它们可以被全局 Verify 编排；从总览下沉到此处不表示取消检查。

`planning_check.py` 是零参数命令壳，负责输出与退出码；`planning_validator.py` 是加载和校验 Catalog 的可复用实现，供命令壳与 Repository 模块检查共同调用。两者不是两个不同的规划门禁，也不应各自维护一套规则。

## 1.2. 有副作用的操作不是 check

以下只在明确需要改变本机状态时使用，不能为了检查自动执行：

- `local_skills link`：创建 ignored `.agents/skills/` 链接，已有不同目标拒绝覆盖，缺源 BLOCKED；不修改用户安装。
- `hooks install`：修改本仓 Git hook 接入，只安装非阻断提醒；不自动运行 Verify，不把 commit 变成验收。
- `start_api`：启动本机 API，是运行环境操作而非 checker；准备、启动和停止见[本地体验](../scenarios/local-development.md)。

skill 来源由 `CODEX_HOME/skills`（默认 `~/.codex/skills`）定位。绝对本机路径不写入共享 manifest。

## 1.3. 哪些命令不归这里

`delivery_gate status`、Qoder `status/result` 需要具体的 submission/run 与生命周期上下文，见[Delivery Gate](delivery-gate.md)和 [Agent workflow](../scenarios/delegate-work.md)。Java task 见[原生检查](java-checks.md)，API 启动见[本地体验](../scenarios/local-development.md)。

失败时保留原 reason、位置和证据，返回[按阶段排障](troubleshooting.md)；不要把“局部检查通过”扩大为整个交付通过。

Codex / Qoder Stop 配置、加载和安全停用见[交付 Hook](hooks.md)。Git 的 doctor 不证明客户端 Hook 已受信任或触发。
