# LexiFlow Harness

Harness 是机器约束的真源。理解职责先读[工程三体系总览](../docs/system/engineering.md)，按任务操作从[生命周期入口](../docs/README.md)进入场景，具体命令见[检查入口索引](../docs/reference/checks.md)。

## 配置与职责

| 真源 | 负责什么 |
| --- | --- |
| [manifest.yaml](manifest.yaml) | 项目类型、公开命令、模块与文档入口 |
| [agent-policy.manifest.yaml](agent-policy.manifest.yaml) | 隐私、Git、工作包、模型路由、身份、调度与结果语义 |
| [delivery-hooks.json](delivery-hooks.json) | 交付 Stop 的模式、超时与进度间隔；固定协议由入口实现，不签发正式验收 |
| [ci-policy.yaml](ci-policy.yaml) | 快速 CI 的常驻检查与正式发行检查边界，不签发完整 Verify/Formal |
| [test-services.json](test-services.json) | Hook 临时 PostgreSQL/Redis、本机镜像、就绪与安全资源边界 |
| [python-quality.toml](python-quality.toml) / [python-docstrings.toml](python-docstrings.toml) | Ruff 风格与格式、Pylint 中文公共接口文档字符串规则；仅 scripts |
| [module-checks.yaml](module-checks.yaml) | 唯一执行定义与场景选择列表；固定结果合同由加载器实现 |
| [module-boundaries.yaml](module-boundaries.yaml) | 领域归属、依赖方向与跨模块边界 |
| [java-product.manifest.yaml](java-product.manifest.yaml) | Java 25 原生构建与检查入口 |
| [documentation-policy.yaml](documentation-policy.yaml) | 中文说明、英文术语、文档结构与图源工作流 |

## 执行边界

- **开发自检**：`python3 -m scripts.verification.development` 按完整真实 diff 的 triggers 与模块消费依赖闭包选择检查；未知覆盖失败关闭，开发 PASS 不是完整 Verify 或 Formal PASS。实现者可做静态、编译与精准测试，不能自签验收。见[执行场景](../docs/scenarios/run-repository-gates.md)。
- **完整 Verify**：`public_executables` 定义公开命令；完整基线按需显式运行，诊断不签发验收。见[Verify](../docs/reference/verification.md)。
- **窗口与复用**：同 checkout 的执行窗口非等待，不锁编辑；输入漂移失败，不终止未知进程或删锁。复用只在同次事务的完整等价合同内，自检不得复用为 Formal。见[协调合同](agent-policy.manifest.yaml)。
- **正式验收**：`TASK_VALIDATION` 由不同 actor 执行冻结计划；高风险另需 `INDEPENDENT_REVIEW`。review 只读冻结 diff/evidence，`CATALOG_DECISION` 只核对 receipt/hash DAG，两层不重跑命令。见[Delivery Gate](../docs/reference/delivery-gate.md)。
- **Agent 委派**：默认当前父任务下原生子代理，模型显式按 policy 选择；Qoder 只消费历史运行，新 start/resume 拒绝。不同 actor 分担验证／审查，集成验证串行，内部不用 `create_thread`。见[Agent workflow](../docs/scenarios/delegate-work.md)。
- **Stop 与 CI**：正常 Stop 运行开发检查；中间汇报、提问、等待及只读 review/catalog 以独立行 `<!-- lexiflow:intermediate -->` 结尾。普通交付不使用该标记；跳过、callback、零退出和失败重试均不是 PASS。Git Hook 仅提醒；CI quick 与正式发行基线见[Hook](../docs/reference/hooks.md)及[CI](../docs/scenarios/github-actions.md)。

## 脚本与开发数据边界

`scripts/` 只保存跨阶段复用、有稳定消费者及直接测试的能力；阶段工具放 ignored `tmp/phase-tools/<change-id>/`，不作公开入口或产品依赖。提升为长期脚本须用户批准并补合同和测试，职责见 [Scripts Reference](../docs/reference/scripts.md)。

只维护最新逻辑和唯一最新 SQL。结构变化须显式重建本项目开发库并完整重导；API 不隐式清库、不触及其他项目，不复用缓存或本机偏好仍引用的身份绑定不同资料。来源、事务、发布与隐私约束不变。

Catalog 的 planning-only 不授予派发或验收资格；须按当前范围分解 Task。共享策略只改 policy；`python3 -m scripts.repository.quality --mode policy` 检查合同，Catalog 检查仍由 `planning_check` 执行，无同步步骤。

## 文档与本机接入

图源仅为 Markdown 的 fenced `plantuml`；先按 documentation-policy 在 ignored `tmp/diagrams/` 校验、渲染和查看，再原样复制正文。静态检查不代替语义或视觉审阅，操作见[图文维护](../docs/scenarios/maintain-documentation.md)。

本机 skill 来自 `CODEX_HOME/skills`（默认 `~/.codex/skills`），不自动下载或修改配置。`local_skills check` 只读检查，`link` 显式创建 ignored 链接；缺源 BLOCKED、异目标拒绝覆盖，见[独立工具](../docs/reference/standalone-tools.md)。

venv、容器与隔离资源准备见[验证环境](../docs/scenarios/verification-environment.md)。报告、正式记录和 Qoder 运行分别留在 `tmp/quality/verification-reports/`、`tmp/quality/delivery-gate/`、`tmp/qoder-tasks/`，不进入共享 Harness。
