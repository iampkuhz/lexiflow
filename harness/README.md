# LexiFlow Harness

Harness 保存静态、机器可读的工程约束；运行状态、报告与 receipt 留在 ignored 的本机目录。想先理解整体，请从[工程地图](../docs/development/overview.md)进入；想执行一次交付，请读[交付主干](../docs/development/change-delivery.md)。本页是规则真源导航，不是完整操作手册。

## 配置与 owner

| 真源 | 负责什么 |
| --- | --- |
| [manifest.yaml](manifest.yaml) | 项目类型、公开命令、模块与文档入口 |
| [agent-policy.manifest.yaml](agent-policy.manifest.yaml) | 隐私、Git、工作包、模型路由、身份、调度与结果语义 |
| [agent-runtime.manifest.yaml](agent-runtime.manifest.yaml) | 客户端 Session/checkout 运行边界，共享字段由 policy 投影 |
| [policy-projections.yaml](policy-projections.yaml) | 真源到 runtime、模板、Catalog 派生字段的映射 |
| [module-checks.yaml](module-checks.yaml) | 模块 Check 的入口、触发、依赖、环境、输入与结果 contract |
| [module-boundaries.yaml](module-boundaries.yaml) | Domain owner、依赖方向与跨模块边界 |
| [java-product.manifest.yaml](java-product.manifest.yaml) | Java 25 原生构建与检查入口 |
| [documentation-policy.yaml](documentation-policy.yaml) | 中文说明、英文术语、文档结构与图源工作流 |

## 执行边界

日常 Verify 不是编辑锁或 commit 准入。实现完成后分别运行 `python3 scripts/check_changes.py` 与 `python3 scripts/check_repository.py`；前者按 diff 自查，后者检查完整 baseline。缺必需环境返回 BLOCKED，不自动安装依赖。Java 检查只由 Gradle 执行，Python 只编排证据。

正式验收只有 `python3 -m scripts.acceptance submit|validate|review|check|status`。submit 绑定报告与真实 producer；validate 在独立 Session 执行冻结输入的检查；review 只审 frozen diff/evidence；check 只核对 receipt、依赖和批准。review/check 不重跑交付命令，调用者不伪造身份；详细交接见 [Acceptance](../docs/development/change-delivery/acceptance.md)。

Qoder 与 Codex 调度以 policy 为准。先核对真实宿主等待兼容性；Goal 活跃或未知且没有受支持等待适配时，不启动 Qoder。Qoder 交接后父任务结束当前轮，终态 callback 后才核对与 ack；Codex 原生子代理使用原生协作事件。未知运行不重派，不通过修改 Goal、身份或预算绕过。细节和安全恢复见 [Agent workflow](../docs/development/agent-workflow.md)。

Hook 若显式安装，只做非阻断提醒，不自动执行 Verify 或修改 change context。CI 必须独立调用 Repository Verify，不能信任本机 Hook；新增强制 Hook 或默认阻断需用户明确批准。

## 脚本与开发数据边界

scripts/ 只放跨阶段复用、有稳定消费者和直接测试的能力；阶段工具放 ignored `tmp/phase-tools/<change-id>/`，不成为公开 manifest 或产品运行依赖。提升为长期脚本需用户批准并补齐 contract/测试。职责定位见 [Scripts Reference](../docs/development/reference/scripts.md)。

全仓只维护最新逻辑与唯一最新 SQL。结构变化显式重建本项目开发库并完整重导，不保留升级链；API 启动不得隐式清库，不触及其他项目。来源、发布身份、事务、隐私和验证护栏仍保留，重建资料不能与缓存/本机偏好的旧身份混淆。

Catalog 的 planning-only 只表示静态计划可合法未分解，不表示可以派发或正式验收。必须依据当前阶段边界分解 Task，旧 Task/receipt 不代表新阶段。共享策略更新先改 policy，再显式 `policy_projection --write` 和 `--check`，不手工双写投影。

## 文档与本机接入

图源只在 Markdown 的 plantuml 围栏。按 documentation-policy 先在 ignored tmp/diagrams/ 草稿校验、渲染并实际查看，再原样复制正文；不提交调试产物。完整操作见[图文维护](../docs/development/reference/documentation.md)，不把静态检查当中文语义或视觉验收。

本机 skill 仅引用 CODEX_HOME/skills（默认 ~/.codex/skills），不自动下载或修改用户配置。`local_skills check` 只检查，`local_skills link` 显式创建 ignored 链接，缺源 BLOCKED、异目标拒绝覆盖；具体见[独立工具](../docs/development/reference/standalone-tools.md)。

Python venv 与隔离测试资源的显式准备见[验证环境](../docs/development/operations/verification-environment.md)。报告在 tmp/quality/verification-reports/，正式记录在 tmp/quality/acceptance/，Qoder 原始运行在 tmp/qoder-tasks/；它们都不进入共享 Harness。
