# 1. 脚本架构说明

本文列出当前脚本模块的职责、依赖方向和公开场景。日常检查、正式验收、agent 执行、仓库维护与产品运行各自只有一个所有者；全局入口只组合模块能力，不理解模块内部测试类或产品字段。

## 1.1. 依赖方向

- `scripts/check_changes.py` 与 `scripts/check_repository.py` 只调用 `scripts.verification`。
- `scripts.acceptance` 可以调用 `scripts.verification` 和 `scripts.agents` 的公开事实接口。
- `scripts.verification`、`scripts.agents` 和 `scripts.repository` 不导入 `scripts.acceptance`。
- `scripts.repository` 不导入 verification、agent、acceptance 或产品实现。
- Java 产品只由 Gradle 构建和测试；Python 不承载 Domain、Application、API 或 worker 业务。
- `scripts.environment` 只做只读诊断和受控子进程环境准备，不安装依赖、不创建业务数据。

静态方向测试位于 `tests/verification/test_dependencies.py`、`tests/agents/test_module_boundaries.py`、`tests/acceptance/test_dependencies.py` 与 `tests/repository/test_repository_module.py`。

# 2. 公开场景

## 2.1. 日常验证

| 场景 | 入口 | 输入 | 结果 |
| --- | --- | --- | --- |
| 变更验证 | `python3 scripts/check_changes.py` | Git diff、`harness/module-checks.yaml` | 受影响检查、覆盖缺口、范围自审 |
| 仓库验证 | `python3 scripts/check_repository.py` | 全部 `repository-baseline` 声明 | 全仓模块结果与输入快照 |

日常入口不读取 Task、执行身份或正式验收记录。没有检查、覆盖缺口、未执行、环境缺失、结果缺字段、零测试或 skip 都不能得到 `PASS`。

## 2.2. 正式验收

| 场景 | 入口 | 责任 |
| --- | --- | --- |
| 提交 | `python3 -m scripts.acceptance submit` | 绑定已持久化的 PASS 变更报告、Task 要求、冻结输入和 producer 来源 |
| 独立验证 | `python3 -m scripts.acceptance validate` | 在独立真实会话中执行冻结的模块检查 |
| 独立审查 | `python3 -m scripts.acceptance review` | 只读复核冻结差异与 validation evidence |
| 条件核对 | `python3 -m scripts.acceptance check` | 核对 validation、review、依赖、批准和 hash DAG |
| 状态读取 | `python3 -m scripts.acceptance status` | 只读返回已发布记录状态 |

`review` 与 `check` 不运行交付命令。记录位于 ignored `tmp/quality/acceptance/`，采用不可覆盖发布与当前输入复核。

## 2.3. 仓库维护

| 场景 | 入口 | 边界 |
| --- | --- | --- |
| 计划静态检查 | `python3 -m scripts.repository.planning_check --root .` | ID、版本、依赖、批准继承、owner、Task 必需检查映射与投影一致性 |
| 文档治理 | `python3 -m scripts.repository.docs_check` | 链接、锚点、标题、图源围栏和 latest-only 规则 |
| 策略投影检查 | `python3 -m scripts.repository.policy_projection --check` | 只读核对 policy 的确定性投影 |
| 策略投影写入 | `python3 -m scripts.repository.policy_projection --write` | 显式更新声明的投影字段 |
| 本机 skill 检查/链接 | `python3 -m scripts.repository.local_skills check|link` | 不下载、不覆盖、不把本机绝对路径写入共享配置 |
| Hook 诊断/安装 | `python3 -m scripts.repository.hooks doctor|install` | 非阻断提醒；只有 install 修改本仓 Git 配置 |

## 2.4. Agent 执行

`scripts/agents/qoder_task.py` 拥有 Qoder 预检、启动、恢复、结果验证与 ack；`scripts/agents/qoder/` 拥有生命周期、回调、事实读取和脱敏诊断。`scripts/agents/local_codex_runtime.py` 与 `scripts/agents/codex/runtime_binding.py` 绑定真实本机会话来源。`scripts/agents/codex_work_package.py` 发布和核对 Codex 工作包原始记录。

Agent 只产生可核对的运行事实，不签发 validation、review 或 catalog 结论。Qoder 进程退出零、结构化结果 `PASS` 和 callback 信号都不等于正式验收。

宿主等待兼容性与跨执行器调度同属 agents：先证明当前模式允许安全等待 Qoder，不能证明时转 Terra；同轮两条路径都失败才累计调度失败，成功接单清连续计数。规则和阈值唯一来自 `harness/agent-policy.manifest.yaml.agent_dispatch`，不会进入验证或验收模块。真实宿主工具调用仍由父代理完成，仓库接口核对并保存工具结果，不模拟 Codex 身份、不修改宿主 Goal 状态。

## 2.5. 产品与环境

| 能力 | 入口 | 所有者 |
| --- | --- | --- |
| 后端完整交付 | `python3 -m scripts.environment.java_exec backend/gradlew -p backend deliveryFull` | Gradle/Java 25 |
| 扩展质量 | `node extension/scripts/quality-check.mjs` | Chrome Extension |
| Java 运行时选择 | `python3 -m scripts.environment.java_exec <command...>` | `scripts.environment` |
| 数据库初始化 | `:platform:adapters:postgresInit` | 后端 platform adapter |
| 离线词库导入 | `:platform:adapters:lexiconImport` | 后端 application/platform |

环境值只通过声明的 `required_environment` 注入子进程。PostgreSQL 与 Redis 测试目标必须显式指向隔离资源；检查不会自动安装、拉取或触碰开发数据库。

# 3. 模块检查声明

`harness/module-checks.yaml` 只登记稳定模块入口、适用 scope、触发路径、模块依赖、环境要求、输入闭包和结果合同。当前固定能力为：

- `eng.backend.delivery`
- `eng.extension.quality`
- `eng.verification.module-tests`
- `eng.agents.module-tests`
- `eng.acceptance.module-tests`
- `eng.repository.module-tests`
- `eng.repository.docs`
- `eng.repository.policy`
- `eng.repository.planning`
- `eng.repository.hooks`

每项都有 `repository-baseline` 与 `change-targeted` 声明。Python 工程模块通过 JSON 报告给出 `checks_run`、`failures`、`errors` 和 `skipped`；产品模块由原生 Gradle/npm 入口保证完整性。全局执行器不硬编码测试文件，模块新增内部测试不需要修改全局执行器。

相同命令、配置和输入闭包在一次验证中只执行一次；别名结果只能投影同次执行事实，不能复用实现者的历史报告充当独立验证。

# 4. 计划与验收边界

`planning/workstreams.yaml` 的 86 个 Task 均保留 Task ID、版本、依赖和阶段批准要求，并通过 `required_check_ids` 绑定实际稳定模块能力。计划检查负责静态完整性；它的错误作为独立结果报告，不是产品检查的启动凭证。

正式提交由 `scripts.acceptance.requirements` 读取当前 Task 要求并绑定来源哈希。Task 要求、模块声明、输入、依赖或批准变化会使相关结论失效；日常验证本身不携带 Task identity。

# 5. 完整性与副作用

- 模块测试入口拒绝 0 项、skip、error、failure 和缺失 JSON 字段。
- Change Verify 对无覆盖路径返回非 PASS；Repository Verify 对空声明或部分选择返回非 PASS。
- 后端 `deliveryFull` 强制完整 Java 测试、无 skipped tests、架构和源码规则、数据库/跨进程集成及 boot JAR。
- 扩展质量入口报告 unit test 与 browser smoke 的分别结果。
- 普通检查不安装依赖、不写用户配置、不自动执行 Git 集成。
- 文档图源仅保留 Markdown `plantuml` 围栏；渲染和预览产物留在 ignored `tmp/diagrams/`。
