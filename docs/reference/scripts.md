# 1. 脚本职责索引：按读者任务定位入口

> 上级：[Reference](../reference.md)。先按职责进入[工程治理三体系](../system/engineering.md)：[质量检查](../system/quality-checks.md)、[交付验收](../system/delivery-acceptance.md)、[工程协作与运行支撑](../system/engineering-support.md)。本页从职责回到真实文件，不表示执行顺序。目录图与生命周期图共用下方[文件编号索引](#16-文件编号索引)。

## 1.1. 先区分入口类型

| 类型 | 读者如何使用 | 输入、输出与副作用 |
| --- | --- | --- |
| CLI | 人工显式调用；参数以源码 `main()` 和对应 reference 为准 | 读取当前仓库或显式请求，输出状态／报告；是否持久化须看具体 workflow |
| 触发适配器 | 由 Stop、CI 或模块检查器调用，不作为普通人工步骤 | 适配外部事件并调用能力；可能准备、清理隔离资源或形成运行证据 |
| 内部库 | 由 CLI、适配器或其他受控调用者导入 | 接收已冻结的输入／依赖并返回结构化结果；不是独立命令，也不因文件存在而成为阶段 |
| 人工步骤 | 由符合要求的实现者、独立验证者或审查者完成 | 例如独立验证、review、批准；命令成功或代理角色名称不能代替身份与证据 |

目录名只说明代码组织，不自动说明产品职责：`scripts/repository/` 是仓库工程治理，不是产品业务；`scripts/delivery_gate/` 是正式验收实现，不是所有质量检查的总入口。

## 1.2. 质量检查：选取并执行 checks

质量检查体系根据实际输入与合同选择 checks，输出该输入的事实报告。开发反馈、任务验收和正式发行使用不同证明要求；命令入口不等于验收阶段。流程见[编码后执行门禁](../scenarios/run-repository-gates.md)，能力范围见[检查入口索引](checks.md)。

| 类型 | 真实入口 | 输入、输出与边界 |
| --- | --- | --- |
| CLI | [`development.py`](../../scripts/verification/development.py) · `python3 -m scripts.verification.development` | 从真实 diff 与声明选择日常 checks；输出开发报告，不准备隔离服务 |
| CLI | [`check_changes.py`](../../scripts/check_changes.py) · `python3 scripts/check_changes.py`；[`check_repository.py`](../../scripts/check_repository.py) · `python3 scripts/check_repository.py` | 分别执行完整 Change Verify 与 Repository baseline；输出完整检查报告 |
| CLI | [`ci.py`](../../scripts/verification/ci.py) · `python3 -m scripts.verification.ci plan`／`python3 -m scripts.verification.ci quick` | `plan` 展示计划，`quick` 执行 quick CI；`--base`／`--all` 是该入口实际参数，不等于 Formal |
| CLI | [`diagnose.py`](../../scripts/verification/diagnose.py) · `python3 -m scripts.verification.diagnose change`／`python3 -m scripts.verification.diagnose repository --check-id <check_id>` | 对所选变化或 Check 做定向诊断并输出摘要；不发布完整 Verify 报告 |
| 有报告副作用的 CLI | [`work_package.py`](../../scripts/verification/work_package.py) · `python3 -m scripts.verification.work_package <request.json>` | 请求选择已登记的 Check；输出诊断并写入本机诊断记录，不是无状态 checker 或正式验收；请求及资格见 [Verify](verification.md) |
| 触发适配器 | [`delivery_hook.py`](../../scripts/verification/delivery_hook.py) · Stop 调用 | 读取客户端 Stop 事件、协调隔离服务、开发检查和清理；不是普通用户命令入口，见[Hook](hooks.md) |

核心内部实现按职责而非目录阶段查找：

| 职责 | 内部文件 | 维护真源／边界 |
| --- | --- | --- |
| 选择、冻结与聚合 | [`scenarios.py`](../../scripts/verification/scenarios.py)、[`declarations.py`](../../scripts/verification/declarations.py)、[`scope.py`](../../scripts/verification/scope.py) | [module-checks](../../harness/module-checks.yaml)；闭包选择与[验证测试](../../tests/verification) |
| 风险与窗口协作 | [`risk.py`](../../scripts/verification/risk.py)、[`coordination.py`](../../scripts/verification/coordination.py)、[`transaction.py`](../../scripts/verification/transaction.py) | [agent-policy](../../harness/agent-policy.manifest.yaml)；同次事务复用边界见 [Verify](verification.md) |
| 检查执行与结果 | [`kernel.py`](../../scripts/verification/kernel.py)、[`execution.py`](../../scripts/verification/execution.py)、[`reports.py`](../../scripts/verification/reports.py)、[`summaries.py`](../../scripts/verification/summaries.py) | 冻结输入、子进程、预算、报告完整性与脱敏摘要；直接回归见[验证测试](../../tests/verification) |
| 受限数据辅助 | [`yaml_input.py`](../../scripts/verification/yaml_input.py)、[`release_source_bridge.py`](../../scripts/verification/release_source_bridge.py) | 前者复用同内容 YAML 解析树；后者把冻结来源接到发行 fixture，不复用风险或检查结论，也不授予发行资格 |

## 1.3. 交付验收：独立判断 Task 证据

交付验收围绕 Task、冻结输入、独立性、依赖与批准判断证据是否充分；需要独立 validation 时复用上节质量检查引擎，不另建检查引擎。操作与参数见 [Delivery Gate](delivery-gate.md)，场景见[编码后执行门禁](../scenarios/run-repository-gates.md)。

| 类型 | 真实入口／职责 | 边界 |
| --- | --- | --- |
| CLI | [`delivery_gate/__main__.py`](../../scripts/delivery_gate/__main__.py) · `python3 -m scripts.delivery_gate <subcommand>` | 唯一公开 CLI；`submit`、`validate`、`review`、`check`、`status`、`consume-existing`、`consume-candidate` 子命令是有 Task／submission 上下文的 workflow 操作，不是独立无状态 checker；具体参数见 [Delivery Gate](delivery-gate.md) |
| 内部 workflow | [`submit.py`](../../scripts/delivery_gate/submit.py)、[`validate.py`](../../scripts/delivery_gate/validate.py) | submit 绑定并冻结送验输入；validate 由独立验证者执行冻结的必需计划，调用质量检查能力 |
| 人工／只读裁决 | [`review.py`](../../scripts/delivery_gate/review.py)、[`check.py`](../../scripts/delivery_gate/check.py) | reviewer 复核冻结 diff/evidence；check 核对 receipt、依赖、approval 与 hash DAG；两者都不重跑检查，check 不生成用户批准 |
| 状态查询 | [`status.py`](../../scripts/delivery_gate/status.py) | 查询具体 submission 的已有记录，不自动推进；状态属于该验收 workflow，不属于独立工具 |
| 内部支撑 | [`authority.py`](../../scripts/delivery_gate/authority.py)、[`producer.py`](../../scripts/delivery_gate/producer.py)、[`requirements.py`](../../scripts/delivery_gate/requirements.py)、[`records.py`](../../scripts/delivery_gate/records.py) | 分别支撑身份、来源事实、必需计划与记录；直接回归见 [`tests/delivery_gate`](../../tests/delivery_gate) |
| 验收实现自检 | [`quality.py`](../../scripts/delivery_gate/quality.py) | 运行 Delivery Gate 模块自身测试；不是产品 Task 验收入口 |

## 1.4. 工程协作与运行支撑：让能力有界运行

支撑体系提供工作包、身份、工具链、仓库治理和隔离资源；它不是检查或验收前后的阶段。运行方式与身份合同见[委派工作](../scenarios/delegate-work.md)，环境操作见[验证环境](../scenarios/verification-environment.md)。

| 类型 | 真实入口／职责 | 输入、输出与副作用 |
| --- | --- | --- |
| CLI | [`codex_cli.py`](../../scripts/agents/delegation/codex_cli.py) · `python3 -m scripts.agents.delegation.codex_cli verify --run-id <run_id>` | 核对精确 Codex 工作包 run，不派发执行 |
| 历史记录 CLI | [`qoder_cli.py`](../../scripts/agents/delegation/qoder_cli.py) | 仅读取、核对或 ack 既有 Qoder 运行；不允许新 `start`／`resume` |
| 内部库 | [`runtime_binding.py`](../../scripts/agents/codex/runtime_binding.py)、[`contracts.py`](../../scripts/agents/contracts.py)、[`module_tests.py`](../../scripts/agents/module_tests.py) | 真实宿主绑定、工作包 contract 与直接测试；不是派发命令，见[Agent workflow](../scenarios/delegate-work.md) |
| 仓库治理 CLI | [`docs_check.py`](../../scripts/repository/docs_check.py)、[`planning_check.py`](../../scripts/repository/planning_check.py)、[`python_quality.py`](../../scripts/repository/python_quality.py) | 分别检查文档结构、Catalog 和 scripts 风格／文档字符串；静态结果不等于语义审阅或正式验收，详见[独立工具](standalone-tools.md) |
| 仓库治理 CLI | [`hooks.py`](../../scripts/repository/hooks.py)、[`local_skills.py`](../../scripts/repository/local_skills.py) | `doctor`／`check` 只读；`install`／`link` 修改本机仓库接入，须明确需要时才运行，见[独立工具](standalone-tools.md) |
| 机器 Check adapter | [`scripts/repository/quality.py`](../../scripts/repository/quality.py) · `python3 -m scripts.repository.quality --mode <mode>` | 由模块检查器消费的结构化入口；`<mode>` 限于 `tests`、`docs`、`policy`、`planning`、`hooks`、`python`，把仓库治理能力转换为 Check 结果，不是普通人工门禁，也不代表正式验收 |
| 隔离资源与环境库 | [`test_services.py`](../../scripts/environment/test_services.py)、[`runtime.py`](../../scripts/environment/runtime.py)、[`toolchain.py`](../../scripts/environment/toolchain.py)、[`git_runtime.py`](../../scripts/environment/git_runtime.py) | 测试服务 lease／就绪／清理、工具链识别与显式准备的 Git wrapper 识别；不自动安装或修改全局 PATH |
| 有副作用的本机入口 | [`start_api.py`](../../scripts/environment/start_api.py)、[`java_exec.py`](../../scripts/environment/java_exec.py) | 受控启动 API 或执行 Java 原生命令；启动/停止与精确参数见[本地体验](../scenarios/local-development.md)和[Java 检查](java-checks.md) |

Agent 运行记录内部库包括 [`codex/work_package.py`](../../scripts/agents/codex/work_package.py)、[`qoder/read_model.py`](../../scripts/agents/qoder/read_model.py)、[`facts.py`](../../scripts/agents/qoder/facts.py)、[`lifecycle.py`](../../scripts/agents/qoder/lifecycle.py)、[`callback.py`](../../scripts/agents/qoder/callback.py) 与 [`dispatch_fallback.py`](../../scripts/agents/dispatch_fallback.py)。它们维护运行事实、回调与派发关联，不签发 validation/review；细节见[身份事实](../scenarios/delegate-work/identity.md)。`scripts/delivery_gate/quality.py` 是另一项模块测试 adapter，只运行 Delivery Gate 自身回归，不是产品 Task 验收。

### 1.4.1. 部署与发行的配套实现

部署、构建与发行也需要工程支撑，但入口主要在 `ops/`，不是 `scripts/`。以下保持真实路径，不把目录清单误作执行顺序；操作从[本机使用](../user/podman-macos.md)或对应[发行场景](../scenarios/docker-release.md)进入。

| 类型 | 入口／实现 | 核心职责与阅读位置 |
| --- | --- | --- |
| CLI／内部事务 | [`local.mjs`](../../ops/podman/local.mjs)／[`unified-local.mjs`](../../ops/podman/unified-local.mjs) | 本机公开命令／安装、更新与恢复事务；见[用户操作](../user/podman-macos.md)、[安装与更新](../scenarios/docker-release.md) |
| 内部库 | [`versioned-images.mjs`](../../ops/podman/versioned-images.mjs)／[`image-slot-lock.mjs`](../../ops/podman/image-slot-lock.mjs) | 基础版本镜像槽位／槽位写入互斥；见[部署镜像合同](../../openspec/specs/deployment-images/spec.md)，版本标签不替代完整构建身份 |
| CLI／内部 workflow | [`workflow.mjs`](../../ops/release/workflow.mjs)／[`pipeline.mjs`](../../ops/release/pipeline.mjs) | 候选准备和既有证据续办／镜像、组装与完整性验证；见[发行步骤与请求字段](../scenarios/github-actions.md) |
| Check adapter | [`module_check_adapter.py`](../../ops/docker/tests/module_check_adapter.py) | 日常 surface／shell／local／node 固定组与完整 Docker 合同；见[检查范围](verification.md)，分组入口不是新的部署命令 |
| 内部检查实现 | [`release_runtime_check.py`](../../scripts/environment/release_runtime_check.py)／[`candidate_runtime_check.py`](../../scripts/environment/candidate_runtime_check.py) | 源码发行机制／真实候选生命周期检查；见[发行边界](../scenarios/github-actions.md)，fixture 与真实候选证据不能互代 |
| CLI | [`release_promotion.py`](../../scripts/environment/release_promotion.py) | 消费既有验收链并核对固定候选；发布另需显式授权，见[候选与证据消费](delivery-gate.md)、[晋升操作](../scenarios/github-actions.md) |

## 1.5. 按修改目标找维护真源

| 要改什么 | 真源 | 实现与回归 |
| --- | --- | --- |
| Check 触发、依赖与冻结输入 | [module-checks](../../harness/module-checks.yaml) | declarations／scope／[Verification 测试](../../tests/verification) |
| 风险、身份与复用 | [agent-policy](../../harness/agent-policy.manifest.yaml) | risk／transaction／[Gate 测试](../../tests/delivery_gate) |
| Stop 触发、预算与清理 | [delivery-hooks](../../harness/delivery-hooks.json)、[test-services](../../harness/test-services.json) | [Hook 回归](../../tests/verification/test_delivery_hook.py) |
| quick 与正式发行集合 | [ci-policy](../../harness/ci-policy.yaml)、[workflows](../../.github/workflows) | [CI 回归](../../tests/verification/test_ci.py) |
| 图中时间预算 | [执行场景的预算口径](../scenarios/run-repository-gates.md) | 既有成功物理执行样本；估算和实测分开，不靠重跑补数字 |

改变规则或实现前走[优化任务](../scenarios/optimize-repository-gates.md)；仅运行既有流程走[执行任务](../scenarios/run-repository-gates.md)。

## 1.6. 文件编号索引

以下编号连接[工程目录图](../system/engineering.md)与[执行生命周期](../scenarios/run-repository-gates.md)。每个编号只对应一个真实文件；不是执行顺序、检查编号或可运行命令。总图选择核心入口，内部实现与配套测试按本页前述职责下钻。

| 编号 | 文件 | 类型与用途 |
| --- | --- | --- |
| F01 | [`scripts/check_changes.py`](../../scripts/check_changes.py) | CLI；完整变化验证 |
| F02 | [`scripts/check_repository.py`](../../scripts/check_repository.py) | CLI；完整仓库验证 |
| F03 | [`scripts/verification/development.py`](../../scripts/verification/development.py) | CLI；开发报告 |
| F04 | [`scripts/verification/delivery_hook.py`](../../scripts/verification/delivery_hook.py) | 适配器；Stop交付检查 |
| F05 | [`scripts/verification/diagnose.py`](../../scripts/verification/diagnose.py) | CLI；失败定位 |
| F06 | [`scripts/verification/ci.py`](../../scripts/verification/ci.py) | CLI；PR快速检查 |
| F07 | [`scripts/verification/scenarios.py`](../../scripts/verification/scenarios.py) | 内部库；选择冻结与检查编排 |
| F08 | [`scripts/delivery_gate/__main__.py`](../../scripts/delivery_gate/__main__.py) | CLI；验收命令分派 |
| F09 | [`scripts/delivery_gate/submit.py`](../../scripts/delivery_gate/submit.py) | 内部 API；送验与冻结 |
| F10 | [`scripts/delivery_gate/validate.py`](../../scripts/delivery_gate/validate.py) | 内部 API；独立验证 |
| F11 | [`scripts/delivery_gate/review.py`](../../scripts/delivery_gate/review.py) | 内部 API；只读审查 |
| F12 | [`scripts/delivery_gate/check.py`](../../scripts/delivery_gate/check.py) | 内部 API；验收条件核对 |
| F13 | [`scripts/repository/docs_check.py`](../../scripts/repository/docs_check.py) | CLI；文档静态检查 |
| F14 | [`scripts/repository/python_quality.py`](../../scripts/repository/python_quality.py) | CLI；Python质量检查 |
| F15 | [`scripts/repository/planning_check.py`](../../scripts/repository/planning_check.py) | CLI；Catalog静态检查 |
| F17 | [`scripts/environment/java_exec.py`](../../scripts/environment/java_exec.py) | CLI；Java工具链入口 |
| F18 | [`scripts/environment/start_api.py`](../../scripts/environment/start_api.py) | CLI；本机API启动 |
| F19 | [`scripts/environment/test_services.py`](../../scripts/environment/test_services.py) | 内部库；测试资源准备与清理 |
| F20 | [`harness/module-checks.yaml`](../../harness/module-checks.yaml) | 声明；Check触发与依赖 |
| F21 | [`harness/delivery-hooks.json`](../../harness/delivery-hooks.json) | 配置；Stop行为配置 |
| F22 | [`harness/agent-policy.manifest.yaml`](../../harness/agent-policy.manifest.yaml) | 合同；风险身份与协作约束 |
| F23 | [`ops/release/workflow.mjs`](../../ops/release/workflow.mjs) | CLI；发行候选与晋升 |

命令与子命令由同一文件提供时共用该文件编号；例如 F08 是验收命令分派器，F09—F12 是它调用的实现，不是四个新增 CLI。
