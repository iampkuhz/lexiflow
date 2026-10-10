# 1. 配置地图：先找文件与消费者

> 上级：[Reference](../reference.md)。本页说明现有文件由谁读取；机器配置、规约说明和运行证据不是同一种东西。需要运行流程时，从[工程入口](../system/engineering.md)进入。

## 1.1. 工程配置与合同

表中“运行读取”表示自有执行代码使用其内容；“测试读取”不代表产品运行时加载。只被引用、复制、哈希或检查存在，不等于程序解析并执行了文件中的规则。

| 文件 | 已确认的消费者 | 性质与维护边界 |
| --- | --- | --- |
| [agent-policy.manifest.yaml](../../harness/agent-policy.manifest.yaml) | [风险评估](../../scripts/verification/risk.py)、[验收计划](../../scripts/delivery_gate/acceptance.py)、[Agent 派发](../../scripts/agents/dispatch_fallback.py)、[规划校验](../../scripts/repository/planning_validator.py) | 运行读取；共享策略真源，包含多个职责段 |
| [module-checks.yaml](../../harness/module-checks.yaml) | [声明加载](../../scripts/verification/declarations.py)、[选择闭包](../../scripts/verification/scope.py)、[任务主体](../../scripts/delivery_gate/task_subject.py) | 运行读取；`executions` 唯一维护命令、输入、环境与期限，`checks` 只选择执行定义及触发／依赖；固定结果判据在加载器 |
| [delivery-hooks.json](../../harness/delivery-hooks.json) | [delivery_hook.py](../../scripts/verification/delivery_hook.py) | 运行读取；Stop 启用模式、期限与进度间隔；事件、停用变量名及中间标记是入口固定协议 |
| [test-services.json](../../harness/test-services.json) | [test_services.py](../../scripts/environment/test_services.py) | 运行读取；隔离资源准备、就绪与清理，见[验证环境](../scenarios/verification-environment.md) |
| [ci-policy.yaml](../../harness/ci-policy.yaml) | [CI](../../scripts/verification/ci.py)、[风险评估](../../scripts/verification/risk.py) | 运行读取；quick 常驻项与发行专属集合 |
| [documentation-policy.yaml](../../harness/documentation-policy.yaml) | [文档静态检查](../../scripts/repository/docs_check.py)、[本机 skill 接入](../../scripts/repository/local_skills.py) | 运行读取与 Agent 规约；格式检查不能替代内容审阅 |
| [java-product.manifest.yaml](../../harness/java-product.manifest.yaml) | [Java／门禁分层测试](../../tests/verification/test_quality_gate_layering.py) | 已确认测试读取；构建操作回到[Java 检查](java-checks.md) |
| [module-boundaries.yaml](../../harness/module-boundaries.yaml) | 从[系统边界](../system/modules.md)查阅 | 领域合同；未确认直接解析消费者，不据此删除 |
| [manifest.yaml](../../harness/manifest.yaml) | [API 启动入口](../../backend/product/api/src/main/java/io/lexiflow/api/ApiApplication.java)检查存在；Agent 按工程入口读取 | 导航索引；存在性探测不等于解析字段 |

### 1.1.1. 任务与规划

| 文件 | 已确认的消费者 | 性质与维护边界 |
| --- | --- | --- |
| [workstreams.yaml](../../planning/workstreams.yaml) | [任务要求](../../scripts/delivery_gate/requirements.py)、[规划校验](../../scripts/repository/planning_validator.py) | 运行读取；任务、依赖与范围，不保存 Agent 策略副本，也不是运行报告 |
| [task-template.yaml](../../planning/task-template.yaml) | 人与 Agent 填写 Task 时读取 | 任务数据模板；不复制模型路由、调度与交接合同 |

## 1.2. 修改唯一事实

共享策略只改 [policy](../../harness/agent-policy.manifest.yaml)，用 `python3 -m scripts.repository.quality --mode policy` 检查合同；不再运行同步工具。Task 数据改 Catalog，静态关系由 `planning_check` 核对。

同一检查在日常与完整验证中共享一个 `executions` 条目；修改命令、环境或期限只改该条目。场景选择仍显式保留触发与依赖，不能用默认继承掩盖范围差异。`failures/errors/skipped = 0`、非空计数、仓库工作目录等固定判据不逐项配置；特殊的 Gradle 与扩展结果格式由加载器的封闭合同处理。

`path_ownership.resolution`、`most-specific` 等机器字段和值保持原样。注释或格式变化也会改变当前字节摘要；是否需要重做检查由实际冻结合同判定，不能据“只改文字”擅自沿用旧证据。现行授权与结果边界见 [AGENTS.md](../../AGENTS.md)。

## 1.3. 产品、构建与外部工具配置

这些文件由对应工具或实现消费。工具规定的配置位置保留在对应模块；本页列出消费关系，不保证本机工具已安装、已加载配置或已执行。

| 文件 | 消费者 | 性质与维护边界 |
| --- | --- | --- |
| [application.yaml](../../backend/product/api/src/main/resources/application.yaml) | API 的 Spring Boot 配置加载 | 产品默认配置；本机覆盖方式见[本地开发](../scenarios/local-development.md) |
| [extension/manifest.json](../../extension/manifest.json) | [扩展构建](../../extension/scripts/build.mjs)、Chrome | 构建解析与复制；扩展权限和入口声明 |
| [extension/package.json](../../extension/package.json) | npm；[发行测试](../../extension/tests/release.test.mjs)也读取 | 工具命令与开发依赖声明 |
| [extension/package-lock.json](../../extension/package-lock.json) | npm；[发行测试](../../extension/tests/release.test.mjs)也读取 | 依赖锁，不按普通手工配置拆分 |
| [extension/tsconfig.json](../../extension/tsconfig.json) | [构建入口](../../extension/scripts/build.mjs)调用 TypeScript | 编译器参数 |
| [.codex/hooks.json](../../.codex/hooks.json) | Codex 客户端 Hook 配置 | 客户端适配，不复制检查规则；存在不证明客户端已经触发 |
| [.qoder/settings.json](../../.qoder/settings.json) | Qoder 客户端 Hook 配置 | 既有客户端接入；与禁止新 Qoder 派发是两件事 |
| [ci.yml](../../.github/workflows/ci.yml) | GitHub Actions | 分支／PR 的 quick 工作流，见[CI 与发行](../scenarios/github-actions.md) |
| [release.yml](../../.github/workflows/release.yml) | GitHub Actions | 标签发行工作流，不替代原生独立验收链 |

### 1.3.1. 数据、部署与发行

| 文件 | 已确认的消费者 | 性质与维护边界 |
| --- | --- | --- |
| [infra/local/compose.yaml](../../infra/local/compose.yaml) | [数据库目标](../../ops/podman/database-target.mjs)、Compose 工具 | 本机服务声明，代码读取并核对内容 |
| [ecdict-source.lock.json](../../ops/dataset/ecdict-source.lock.json) | [词库来源](../../scripts/environment/ecdict_bundle.py)、[下载入口](../../ops/podman/fetch-ecdict.sh) | 来源锁；不是下载时生成的运行记录 |
| [generation-inputs.json](../../ops/dataset/generation-inputs.json) | [生成身份](../../ops/dataset/generation-version.mjs) | 数据生成版本的输入声明 |
| [base-images.json](../../ops/docker/base-images.json) | [发行构建](../../ops/release/build.mjs) | 基础镜像锁，随构建版本审查 |
| [ops/docker/compose.yaml](../../ops/docker/compose.yaml) | [发行 workflow](../../ops/release/workflow.mjs)、Compose 工具 | 发行服务模板；不同于本机开发 Compose |
| [compose.validation.yaml](../../ops/podman/compose.validation.yaml) | [Compose 合同测试](../../ops/docker/tests/test_podman_compose_contract.py) | 已确认测试解析；不据此声称生产运行时加载 |
| [distribution-licenses.json](../../ops/release/distribution-licenses.json) | [发行 workflow](../../ops/release/workflow.mjs)、[晋升核对](../../scripts/environment/release_promotion.py) | 分发许可声明与发行核对输入 |

### 1.3.2. 其他格式与运行数据

[python-quality.toml](../../harness/python-quality.toml) 与 [python-docstrings.toml](../../harness/python-docstrings.toml) 分别由 [python_quality.py](../../scripts/repository/python_quality.py) 传给 Ruff 与 Pylint，不属于 YAML/JSON，但同样是机器配置。

本机请求、凭据、环境覆盖和运行报告不是以上共享声明。正式验收记录、验证报告及临时服务状态留在现行忽略目录，入口见 [Harness](../../harness/README.md)。证据摘要只能引用真实记录，不能通过编辑说明替代原始验证。
