# 1. Scripts 职责：从阶段回到文件

> 位置：[工程地图](../overview.md) → [Reference](../reference.md) → Scripts。先在 [Verify](../change-delivery/verification.md)、[Acceptance](../change-delivery/acceptance.md) 或 [Agent workflow](../agent-workflow.md) 确认阶段，再查下面的文件。这里不是执行顺序表。

包导出与 __main__ 是入口设施，没有额外业务阶段。代码位置均相对于仓库根的 scripts/；目录可从[源码入口](../../../scripts/)进入。

## 1.1. 日常 Verify

| 文件（scripts/ 下） | 当前职责 | 阅读位置与边界 |
| --- | --- | --- |
| [check_changes.py](../../../scripts/check_changes.py) | diff 入口、参数、报告持久化 | 交付 S2 入口；保留薄 CLI |
| [check_repository.py](../../../scripts/check_repository.py) | baseline 入口、报告持久化 | 交付 S2 与 CI 调用入口；CI 外部配置另核实 |
| [verification/__init__.py](../../../scripts/verification/__init__.py) | 导出公共能力 | 模块 contract；明确允许外部调用的表面 |
| [verification/scenarios.py](../../../scripts/verification/scenarios.py) | 选择、冻结、执行、聚合 | Verify 子流程主干，分段解释顺序不变量 |
| [verification/declarations.py](../../../scripts/verification/declarations.py) | 加载与验证 module-checks | 选择阶段的内部能力，不独立当作交付步骤 |
| [verification/scope.py](../../../scripts/verification/scope.py) | Git diff、触发、依赖闭包 | 选择阶段；同时被 Acceptance 的 submit 消费 changed_paths |
| [verification/kernel.py](../../../scripts/verification/kernel.py) | 输入摘要、子进程、超时、结果判定 | 执行阶段；按能力解释，不逐 helper 列独立页面 |
| [verification/reports.py](../../../scripts/verification/reports.py) | 报告完整性和读写 | 证据输出阶段；不可与正式 receipt 混淆 |

## 1.2. 正式 Acceptance

| 文件（scripts/acceptance/ 下） | 当前职责 | 阅读位置与边界 |
| --- | --- | --- |
| [__main__.py](../../../scripts/acceptance/__main__.py) / [__init__.py](../../../scripts/acceptance/__init__.py) | 具名 CLI 与公共导出 | 正式验收入口；不得描述成自动连跑所有阶段 |
| [submit.py](../../../scripts/acceptance/submit.py) | 任务、diff、producer 与冻结输入绑定 | S3；明确需要哪些已存在的证据 |
| [validate.py](../../../scripts/acceptance/validate.py) | 独立身份、执行冻结检查、签发 validation | S4；身份、冻结输入与签发；共享校验 helper 由当前场景提供 |
| [review.py](../../../scripts/acceptance/review.py) | 独立 reviewer 与真实 findings、证据复核 | S5；明确不执行交付命令 |
| [check.py](../../../scripts/acceptance/check.py) | receipt、依赖、approval、hash DAG | S6；区别可补条件和输入失效；不重跑交付检查 |
| [status.py](../../../scripts/acceptance/status.py) | 查询已发布记录 | 上述 workflow 的只读观察侧链，不是无上下文工具 |
| [authority.py](../../../scripts/acceptance/authority.py) | 身份发现与证明核对 | 跨阶段内部能力，图中表现为身份边界 |
| [producer.py](../../../scripts/acceptance/producer.py) | 解析实现来源事实 | S3 内部；只消费 agents 的事实 |
| [requirements.py](../../../scripts/acceptance/requirements.py) | 读取正式 Task 的要求 | S3/S6 内部；与 planning 静态检查区别 |
| [records.py](../../../scripts/acceptance/records.py) | 安全路径、不可变发布、记录读取 | 跨阶段内部存储 contract，不是新的验收阶段 |
| [quality.py](../../../scripts/acceptance/quality.py) | acceptance 自身直接测试入口 | 模块 checker Reference，不与产品送验步骤混淆 |

## 1.3. 可选 Agent workflow

| 文件（scripts/agents/ 下） | 当前职责 | 阅读位置与边界 |
| --- | --- | --- |
| [qoder_task.py](../../../scripts/agents/qoder_task.py) | preflight/start/resume/result/ack 等入口与调度实现 | 可选委派主干；用状态图定位命令，不展开全部参数 |
| [qoder/lifecycle.py](../../../scripts/agents/qoder/lifecycle.py) | 运行状态、started、watchdog、ack | 状态详情；有状态内部协议，不是 standalone checker |
| [qoder/callback.py](../../../scripts/agents/qoder/callback.py) | 终态回调投递及重复消费边界 | 完成与交接阶段；queued 不冒充验收 |
| [qoder/facts.py](../../../scripts/agents/qoder/facts.py) | 核对原始结果与运行事实 | 完成核对与 producer 消费边界 |
| [qoder/failure_diagnostics.py](../../../scripts/agents/qoder/failure_diagnostics.py) | CLI 失败脱敏诊断 | 失败节点侧链；不是自动重试入口 |
| [dispatch_fallback.py](../../../scripts/agents/dispatch_fallback.py) | 宿主等待资格、attempt 与 fallback 记录 | 调度失败分支；属于状态相关机制，非纯格式检查 |
| [local_codex_runtime.py](../../../scripts/agents/local_codex_runtime.py) | 真实本地 Session 来源 | 身份详情；只读证明与记录写入能力分别说明 |
| [codex/runtime_binding.py](../../../scripts/agents/codex/runtime_binding.py) | 身份绑定 contract | 内部 Reference，不让调用者手写身份 |
| [codex_work_package.py](../../../scripts/agents/codex_work_package.py) | 工作包记录与结果投影 | 原始执行事实；与正式 receipt 分开 |
| [contracts.py](../../../scripts/agents/contracts.py) | path/scope/结果结构合同 | 内部共享能力；不在主图逐字段展开 |
| [quality.py](../../../scripts/agents/quality.py) | agents 直接测试入口 | 独立模块检查；不表示派发本身发生 |
| [__init__.py](../../../scripts/agents/__init__.py) / [codex/__init__.py](../../../scripts/agents/codex/__init__.py) / [qoder/__init__.py](../../../scripts/agents/qoder/__init__.py) | 包与事实导出 | 无独立 workflow 阶段 |

## 1.4. 独立维护与环境能力

| 文件（scripts/ 下） | 角色 | 阅读边界 |
| --- | --- | --- |
| [repository/docs_check.py](../../../scripts/repository/docs_check.py) | standalone checker | Reference 简明说明；保留被 Verify 调用的关系 |
| [repository/policy_projection.py](../../../scripts/repository/policy_projection.py) | check + 显式 write | 分开描述校验与修改副作用，规则只从 policy 派生 |
| [repository/planning_check.py](../../../scripts/repository/planning_check.py) | standalone 静态 checker | 不描述为所有工作包派发的必经前置阶段 |
| [repository/hooks.py](../../../scripts/repository/hooks.py) | doctor + install | 维护 Reference；安装和强制激活需明确授权 |
| [repository/local_skills.py](../../../scripts/repository/local_skills.py) | check + link | 本机接入 Reference；无关产品 workflow 不必经过 |
| [repository/quality.py](../../../scripts/repository/quality.py) | 模块 checker adapter | 汇总直接能力；输出模块检查结果，不负责正式验收 |
| repository/catalog.py / task_source.py | 内部 catalog 查询与指纹 | Catalog 解析只在 catalog.py；task_source.py 复用它绑定任务指纹，不另建解析器 |
| [repository/__main__.py](../../../scripts/repository/__main__.py) | planning CLI 转接 | 指向 planning 检查，不是另一个完整 Gate |
| [repository/__init__.py](../../../scripts/repository/__init__.py) | 包设施 | 无独立阅读章节 |
| [environment/runtime.py](../../../scripts/environment/runtime.py) | 环境能力探测与子进程变量 | Verify 环境子步骤和 Setup 的共用 contract |
| [environment/java_runtime.py](../../../scripts/environment/java_runtime.py) | Java 25 选择与验证 | 内部能力；避免多个页面重复运行时选择规则 |
| [environment/java_exec.py](../../../scripts/environment/java_exec.py) | 原生命令环境包装 | 稳定操作入口；不冒充额外 Quality Gate |
| [environment/start_api.py](../../../scripts/environment/start_api.py) | 端口检查与受控 API 启动 | 本地体验 workflow；有副作用，不作为普通 checker |
| [environment/__init__.py](../../../scripts/environment/__init__.py) | 环境能力导出 | 模块 contract |

原生产品检查由 Gradle/build-logic、extension/scripts/quality-check.mjs 与 quality-results.mjs 拥有；extension build.mjs/build-config.mjs 负责构建。Java 的交付与诊断入口见 [Java 检查](java-checks.md)，浏览器构建与测试见 [Extension E2E](../operations/extension-e2e.md)。
