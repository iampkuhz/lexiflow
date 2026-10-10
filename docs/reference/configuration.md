# 1. 配置地图：先找文件与消费者

> 上级：[Reference](../reference.md)。本页说明现有文件由谁读取；机器配置、规约说明和运行证据不是同一种东西。需要运行流程时，从[工程入口](../system/engineering.md)进入。

## 1.1. 规则、机器配置与固定实现

人类规则由 Agent 和人工按任务阅读；程序不解析 Markdown 生成 policy 对象。真正可调的工程配置只在 `config/engineering/`，固定协议直接归代码。文件被链接、哈希或检查存在，不代表运行时解析其正文。

| 职责 | 唯一正文或配置 | 实际消费者与边界 |
| --- | --- | --- |
| 协作、模型选择、范围、身份、回调与独立性 | [agent-policy.md](../../harness/agent-policy.md) | 人与 Agent；原生身份/工作包协议由 [Agent 实现](../../scripts/agents/README.md) 定义 |
| 风险路径与本地归属根 | [risk.yaml](../../config/engineering/risk.yaml) | [change_risk_assessment.py](../../scripts/verification/planning/change_risk_assessment.py)；风险层、敏感识别、私有路径与独立 actor 判据固定在代码 |
| Check、场景资格、triggers、依赖、ownership 与输入 | [module-checks.yaml](../../config/engineering/module-checks.yaml) | [check_definitions.py](../../scripts/verification/planning/check_definitions.py)、[check_scope_selection.py](../../scripts/verification/planning/check_scope_selection.py)；schema、结果判据固定在加载器 |
| 文档组织、图源与 skill 操作 | [documentation-policy.md](../../harness/documentation-policy.md) | 人与 Agent；固定静态检查与本机 skill 接入分别归 [check_documentation.py](../../scripts/checks/repository/documentation/check_documentation.py)、[manage_local_skill_links.py](../../scripts/local/manage_local_skill_links.py) |
| 领域、依赖方向与跨模块边界 | [module-boundaries.md](../../harness/module-boundaries.md) | 人与 Agent；架构自动断言由对应代码/测试维护 |
| 临时 PostgreSQL/Redis 生命周期 | [isolated_postgres_redis.py](../../scripts/environment/services/isolated_postgres_redis.py) | 固定镜像、容量、就绪、lease 与 owned 清理合同；[验证环境](../scenarios/verification-environment.md)说明操作 |
| Ruff 风格与格式 | [python-quality.toml](../../config/engineering/python-quality.toml) | [check_python_quality.py](../../scripts/checks/repository/python/check_python_quality.py)传给 Ruff；中文公共接口 docstring 的固定 Pylint 规则由实现维护 |
| 工程导航与公开命令 | [Harness README](../../harness/README.md)、[检查入口索引](checks.md) | 按任务定位；命令参数以真正 CLI 为准，不维护平行注册表 |

### 1.1.1. 任务与规划

| 文件 | 已确认的消费者 | 性质与维护边界 |
| --- | --- | --- |
| [workstreams.yaml](../../planning/workstreams.yaml) | [任务要求](../../scripts/acceptance/task_definition/task_acceptance_requirements.py)、[规划校验](../../scripts/checks/repository/catalog/task_catalog_rules.py) | 任务、依赖与范围；Catalog 唯一任务入口在 `Gate.entry_tasks`，不保存规则副本或运行报告 |
| [task-template.yaml](../../planning/task-template.yaml) | 人与 Agent 填写 Task 时读取 | 任务数据模板；不复制模型、调度或交接规则 |

## 1.2. 修改唯一事实

人类协作规则只改 [agent-policy.md](../../harness/agent-policy.md)，Task 数据只改 Catalog，静态关系由 `planning_check` 核对；不另设重复 policy 门禁。修改工程机器配置须同步直接消费者与测试，输入冻结包含实际配置及其文件集合。

同一 Check 在日常和完整验证共用一个条目；命令为 argv 数组，纯源码输入优先声明目录，由快照冻结实际集合及内容，检测新增、删除和变更。Docker 精确成员声明用于不重叠执行分组；场景资格与 triggers 共用唯一 Registry，不提供场景内覆盖或继承。真实依赖使用严格 Check ID；固定计数、失败/跳过判据及结果格式由加载器的封闭合同处理，不逐项配置。

ownership 只扫描共享 Git 源码候选及冻结的共享 ignore，不进入本机私有树，不让个人 ignore 隐藏新文件。注释与格式同样影响字节摘要，是否重做检查由真实冻结合同判定，不能因“只改文字”沿用旧证据。授权与结果边界见 [AGENTS.md](../../AGENTS.md)。

## 1.3. 产品、构建与外部工具配置

这些文件由对应工具或实现消费。工具规定的配置位置保留在对应模块；本页列出消费关系，不保证本机工具已安装、已加载配置或已执行。

| 文件 | 消费者 | 性质与维护边界 |
| --- | --- | --- |
| [application.yaml](../../backend/product/api/src/main/resources/application.yaml) | API 的 Spring Boot 配置加载 | 产品默认配置；本机覆盖方式见[本地开发](../scenarios/local-development.md) |
| [extension/manifest.json](../../extension/manifest.json) | [扩展构建](../../extension/scripts/build/build-extension.mjs)、Chrome | 构建解析与复制；扩展权限和入口声明 |
| [extension/package.json](../../extension/package.json) | npm；[发行测试](../../extension/tests/release.test.mjs)也读取 | 工具命令与开发依赖声明 |
| [extension/package-lock.json](../../extension/package-lock.json) | npm；[发行测试](../../extension/tests/release.test.mjs)也读取 | 依赖锁，不按普通手工配置拆分 |
| [extension/tsconfig.json](../../extension/tsconfig.json) | [构建入口](../../extension/scripts/build/build-extension.mjs)调用 TypeScript | 编译器参数 |
| [.codex/hooks.json](../../.codex/hooks.json) | Codex 客户端 Hook 配置 | 客户端适配，不复制检查规则；存在不证明客户端已经触发 |
| [.qoder/settings.json](../../.qoder/settings.json) | Qoder 客户端 Hook 配置 | 手工客户端 Stop 适配；不提供仓库子代理委派链 |
| [ci.yml](../../.github/workflows/ci.yml) | GitHub Actions | 分支／PR 的 quick 工作流，见[CI 与发行](../scenarios/github-actions.md) |
| [release.yml](../../.github/workflows/release.yml) | GitHub Actions | 标签发行工作流，不替代原生独立验收链 |

### 1.3.1. 数据、部署与发行

| 文件 | 已确认的消费者 | 性质与维护边界 |
| --- | --- | --- |
| [infra/local/compose.yaml](../../infra/local/compose.yaml) | [数据库目标](../../ops/podman/environment/local-database-target.mjs)、Compose 工具 | 本机服务声明，代码读取并核对内容 |
| [ecdict-source.lock.json](../../ops/dataset/ecdict-source.lock.json) | [固定词库准备入口](../../ops/dataset/prepare_ecdict_bundle.py) | 来源锁；不是下载时生成的运行记录 |
| [generation-inputs.json](../../ops/dataset/generation-inputs.json) | [生成身份](../../ops/dataset/dataset-generation-identity.mjs) | 数据生成版本的输入声明 |
| [base-images.json](../../ops/docker/base-images.json) | [发行构建](../../ops/release/build/release-image-build-plan.mjs) | 基础镜像锁，随构建版本审查 |
| [ops/docker/compose.yaml](../../ops/docker/compose.yaml) | [发行 workflow](../../ops/release/workflow/run-release-workflow.mjs)、Compose 工具 | 发行服务模板；不同于本机开发 Compose |
| [compose.validation.yaml](../../ops/podman/compose.validation.yaml) | [Compose 合同测试](../../ops/docker/tests/test_podman_compose_contract.py) | 已确认测试解析；不据此声称生产运行时加载 |
| [distribution-licenses.json](../../ops/release/distribution-licenses.json) | [发行 workflow](../../ops/release/workflow/run-release-workflow.mjs)、[晋升核对](../../ops/release/workflow/promote_verified_release.py) | 分发许可声明与发行核对输入 |

### 1.3.2. 其他格式与运行数据

Ruff 机器配置在 `config/engineering/python-quality.toml`；固定中文 docstring 规则归执行实现，消费关系见上表。

本机请求、凭据、环境覆盖和运行报告不是以上共享声明。正式验收记录、验证报告及临时服务状态留在现行忽略目录，入口见 [Harness](../../harness/README.md)。证据摘要只能引用真实记录，不能通过编辑说明替代原始验证。
