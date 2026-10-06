# 1. Scripts 职责：从流程节点回到文件

> 上级：[Reference](../reference.md)。执行顺序看[编码后执行门禁](../scenarios/run-repository-gates.md)，改进任务看[优化门禁](../scenarios/optimize-repository-gates.md)。这里只回答“从哪个文件进入、在哪里维护”。

**先看公开入口，再查内部实现。** 包导出不是流程阶段；一个文件有内部函数不意味着每个函数都是公开命令。下表均链接实际源码，不用角色名称替代文件入口。

## 1.1. 开发与完整 Verify

| 入口文件／命令 | 核心职责 | 深入位置 |
| --- | --- | --- |
| [development.py](../../../scripts/verification/development.py) · `python3 -m scripts.verification.development` | 风险驱动开发 profiles；调用者准备隔离环境 | [执行场景 B](../scenarios/run-repository-gates.md) |
| [delivery_hook.py](../../../scripts/verification/delivery_hook.py) · `main()` | Stop 输入判断、隔离服务、开发检查与清理 | [Hook](../change-delivery/hooks.md) |
| [check_changes.py](../../../scripts/check_changes.py) · `python3 scripts/check_changes.py` | 显式完整 Change Verify | [Verify](../change-delivery/verification.md) |
| [check_repository.py](../../../scripts/check_repository.py) · `python3 scripts/check_repository.py` | 显式完整 Repository baseline | [Verify](../change-delivery/verification.md) |
| [diagnose.py](../../../scripts/verification/diagnose.py) · `python3 -m scripts.verification.diagnose change\|repository` | 定向问题定位，不发布完整交付报告 | [诊断](standalone-tools.md) |
| [work_package.py](../../../scripts/verification/work_package.py) · `python3 -m scripts.verification.work_package <request.json>` | 显式所选 Check 的非正式诊断 | [请求与资格](standalone-tools.md) |
| [ci.py](../../../scripts/verification/ci.py) · `python3 -m scripts.verification.ci plan\|quick` | quick 计划与执行；不是完整或 Formal | [CI](../operations/github-actions.md) |

| 内部文件 | 核心职责 | 维护真源／直接测试 |
| --- | --- | --- |
| [scenarios.py](../../../scripts/verification/scenarios.py) | profiles 选择、冻结、执行与聚合 | [module-checks](../../../harness/module-checks.yaml)、[编排测试](../../../tests/verification/test_lightweight_orchestration.py) |
| [declarations.py](../../../scripts/verification/declarations.py)／[scope.py](../../../scripts/verification/scope.py) | 声明加载、真实 diff 与依赖闭包 | [Verification 测试](../../../tests/verification/) |
| [risk.py](../../../scripts/verification/risk.py)／[transaction.py](../../../scripts/verification/transaction.py) | 风险计算、验证窗口和事务边界 | [agent-policy](../../../harness/agent-policy.manifest.yaml) |
| [kernel.py](../../../scripts/verification/kernel.py)／[execution.py](../../../scripts/verification/execution.py) | 冻结输入、子进程、预算与结果判定 | [Verification 测试](../../../tests/verification/) |
| [reports.py](../../../scripts/verification/reports.py)／[summaries.py](../../../scripts/verification/summaries.py) | 报告持久化、完整性与脱敏摘要 | 正式报告与诊断命名空间分开 |

## 1.2. 正式 Delivery Gate

公开 CLI 是 [delivery_gate/__main__.py](../../../scripts/delivery_gate/__main__.py)::`main()`：`python3 -m scripts.delivery_gate <子命令> ...`。下列场景文件提供内部 API，不能误写为各自独立的 `python file.py` 入口。参数在 [Delivery Gate](../change-delivery/delivery-gate.md)，流程在[执行场景 C](../scenarios/run-repository-gates.md)。

| 场景文件 | 核心职责 | 执行边界 |
| --- | --- | --- |
| [submit.py](../../../scripts/delivery_gate/submit.py) | 绑定 Task、producer、开发报告并冻结送验输入 | 不运行测试命令 |
| [validate.py](../../../scripts/delivery_gate/validate.py) | 独立 actor 执行冻结必需计划 | 唯一正式执行检查层 |
| [review.py](../../../scripts/delivery_gate/review.py) | 独立复核冻结 diff/evidence | 不重跑检查 |
| [check.py](../../../scripts/delivery_gate/check.py) | 核对 receipt、依赖、approval 和 hash DAG | 不重跑检查、不生成用户批准 |
| [status.py](../../../scripts/delivery_gate/status.py) | 查询具体 submission 的已有记录 | 只读观察，不自动推进流程 |

身份与存储分别由 [authority.py](../../../scripts/delivery_gate/authority.py)、[producer.py](../../../scripts/delivery_gate/producer.py)、[requirements.py](../../../scripts/delivery_gate/requirements.py)、[records.py](../../../scripts/delivery_gate/records.py)支撑；直接回归见 [tests/delivery_gate](../../../tests/delivery_gate/)。[quality.py](../../../scripts/delivery_gate/quality.py)检查 Gate 实现本身，不是产品送验入口。

## 1.3. Agent 运行与历史记录

新实现与独立验收使用原生 Codex 协作工具；Qoder 新 `start`／`resume` 被策略拒绝。**Qoder 客户端 Stop 配置仍存在。** 客户端 Hook 与执行器派发不是同一开关，见 [Hook](../change-delivery/hooks.md)。

| 文件／入口 | 核心职责 | 深入位置 |
| --- | --- | --- |
| [local_codex_runtime.py](../../../scripts/agents/local_codex_runtime.py)／[runtime_binding.py](../../../scripts/agents/codex/runtime_binding.py) | 从真实宿主元数据识别并绑定执行者 | [身份事实](../agent-workflow/identity.md) |
| [codex/work_package.py](../../../scripts/agents/codex/work_package.py) | 不可变工作包记录与结果投影 | 库 API，不是原生派发命令 |
| [delegation/codex_cli.py](../../../scripts/agents/delegation/codex_cli.py) · `verify --run-id <run_id>` | 核对精确 Codex 工作包 run | [Agent workflow](../agent-workflow.md) |
| [delegation/qoder_cli.py](../../../scripts/agents/delegation/qoder_cli.py) | 读取／核对／ack 既有 Qoder 运行 | 不猜 latest，不允许新 start/resume |
| [qoder/read_model.py](../../../scripts/agents/qoder/read_model.py)／[facts.py](../../../scripts/agents/qoder/facts.py) | 历史运行快照与完成事实 | 不签发 validation/review |
| [qoder/lifecycle.py](../../../scripts/agents/qoder/lifecycle.py)／[callback.py](../../../scripts/agents/qoder/callback.py) | 历史生命周期、终态通知与消费 | [身份事实](../agent-workflow/identity.md) |
| [dispatch_fallback.py](../../../scripts/agents/dispatch_fallback.py) | attempt 与实际宿主工具调用绑定 | 未知运行不授权重派 |
| [contracts.py](../../../scripts/agents/contracts.py)／[module_tests.py](../../../scripts/agents/module_tests.py) | 工作包合同与工具直接测试 | [tests/agents](../../../tests/agents/) |

## 1.4. 仓库维护与环境

| 入口文件 | 核心职责 | 操作位置 |
| --- | --- | --- |
| [docs_check.py](../../../scripts/repository/docs_check.py) | 文档静态检查 | [图文维护](documentation.md) |
| [policy_projection.py](../../../scripts/repository/policy_projection.py) | `--check` 核对；`--write` 显式修改投影 | [配置地图](configuration.md) |
| [planning_check.py](../../../scripts/repository/planning_check.py)／[planning_validator.py](../../../scripts/repository/planning_validator.py) | CLI 与共用 Catalog 校验实现 | 不是两套门禁 |
| [python_quality.py](../../../scripts/repository/python_quality.py) | Ruff/Pylint，只读质量判断 | [独立工具](standalone-tools.md) |
| [hooks.py](../../../scripts/repository/hooks.py)／[local_skills.py](../../../scripts/repository/local_skills.py) | `doctor/check` 查询；`install/link` 有副作用 | [独立工具](standalone-tools.md) |
| [quality.py](../../../scripts/repository/quality.py) | 仓库 Check adapter | 复用各检查器，不签发 Formal |
| [catalog.py](../../../scripts/repository/catalog.py)／[task_source.py](../../../scripts/repository/task_source.py) | Catalog 查询与任务指纹 | 单一解析实现 |
| [test_services.py](../../../scripts/environment/test_services.py) | Hook 隔离服务 lease、就绪与精确清理 | [验证环境](../operations/verification-environment.md) |
| [runtime.py](../../../scripts/environment/runtime.py)／[toolchain.py](../../../scripts/environment/toolchain.py) | 环境能力与真实工具身份 | [Verify](../change-delivery/verification.md) |
| [java_runtime.py](../../../scripts/environment/java_runtime.py)／[java_exec.py](../../../scripts/environment/java_exec.py) | Java 选择与原生命令包装 | [Java 检查](java-checks.md) |
| [start_api.py](../../../scripts/environment/start_api.py) | 端口检查与受控 API 启动 | [本地体验](../operations/local-experience.md) |

产品检查回到原生模块：后端 [Gradle](../../../backend/build.gradle.kts)，扩展 [quality-check.mjs](../../../extension/scripts/quality-check.mjs)／[quality-results.mjs](../../../extension/scripts/quality-results.mjs)。不为“每文件一个入口”的阅读愿望增加无职责 wrapper。

## 1.5. 按修改目标找维护真源

| 要改什么 | 真源 | 实现／回归 |
| --- | --- | --- |
| Check 触发、依赖与冻结输入 | [module-checks](../../../harness/module-checks.yaml) | declarations／scope／[Verification 测试](../../../tests/verification/) |
| 风险、身份与复用 | [agent-policy](../../../harness/agent-policy.manifest.yaml) | risk／transaction／[Gate 测试](../../../tests/delivery_gate/) |
| Stop 触发、预算与清理 | [delivery-hooks](../../../harness/delivery-hooks.json)、[test-services](../../../harness/test-services.json) | [Hook 回归](../../../tests/verification/test_delivery_hook.py) |
| quick 与正式发行集合 | [ci-policy](../../../harness/ci-policy.yaml)、[workflows](../../../.github/workflows/) | [CI 回归](../../../tests/verification/test_ci.py) |
| 图中时间预算 | [执行场景的预算口径](../scenarios/run-repository-gates.md) | 既有成功物理执行样本；估算和实测分开，不靠重跑补数字 |

改变规则或实现前走[优化任务](../scenarios/optimize-repository-gates.md)；仅运行既有流程走[执行任务](../scenarios/run-repository-gates.md)。
