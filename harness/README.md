# LexiFlow Harness

Harness 是机器约束的真源。整体流程见[工程地图](../docs/development/overview.md)，操作步骤见[交付主干](../docs/development/change-delivery.md)。

## 配置与 owner

| 真源 | 负责什么 |
| --- | --- |
| [manifest.yaml](manifest.yaml) | 项目类型、公开命令、模块与文档入口 |
| [agent-policy.manifest.yaml](agent-policy.manifest.yaml) | 隐私、Git、工作包、模型路由、身份、调度与结果语义 |
| [agent-runtime.manifest.yaml](agent-runtime.manifest.yaml) | 客户端 Session/checkout 运行边界，共享字段由 policy 投影 |
| [policy-projections.yaml](policy-projections.yaml) | 真源到 runtime、模板、Catalog 派生字段的映射 |
| [delivery-hooks.json](delivery-hooks.json) | 交付 Stop 执行、进度、超时与安全停用；不签发正式验收 |
| [ci-policy.yaml](ci-policy.yaml) | 快速 CI 的常驻检查与正式发行检查边界，不签发完整 Verify/Formal |
| [test-services.json](test-services.json) | Hook 临时 PostgreSQL/Redis、本机镜像、就绪与安全资源边界 |
| [python-quality.toml](python-quality.toml) / [python-docstrings.toml](python-docstrings.toml) | Ruff 风格与格式、Pylint 中文公共接口文档字符串规则；仅 scripts |
| [module-checks.yaml](module-checks.yaml) | 模块 Check 的入口、触发、依赖、环境、输入与结果 contract |
| [module-boundaries.yaml](module-boundaries.yaml) | Domain owner、依赖方向与跨模块边界 |
| [java-product.manifest.yaml](java-product.manifest.yaml) | Java 25 原生构建与检查入口 |
| [documentation-policy.yaml](documentation-policy.yaml) | 中文说明、英文术语、文档结构与图源工作流 |

## 执行边界

- **开发自检**：`python3 -m scripts.verification.development` 按真实 diff 和依赖闭包选择 profile：机械/局部运行 `development-change`，高风险另加 `development-baseline`。实现者可做静态、编译与直接测试，但不能自签正式验收。Java 检查由 Gradle 执行；scripts 使用 Ruff/Pylint，中文说明的准确性仍需审阅。
- **完整验证与诊断**：`python3 scripts/check_changes.py`、`python3 scripts/check_repository.py` 用于明确要求的完整 baseline 和发行 CI；`python3 -m scripts.verification.diagnose change|repository` 只定位问题。日常入口不接受 root/base/check ID 覆盖，缺环境为 BLOCKED，不自动安装依赖。开发 PASS 不等于完整 Verify 或 Formal PASS。详见 [Verify](../docs/development/change-delivery/verification.md)。
- **验证窗口与复用**：Verify、Hook、工作包和 Formal validation 共用同 checkout 非等待窗口，忙时 BLOCKED，不锁编辑。真实风险闭包漂移使验证失败；不删除未知锁或终止未知进程。仅显式 `transaction_reuse=true` 且完整输入、配置、环境、window、runner、context 相同的成功 Check 可在同次事务复用；未知副作用、runtime transport、失败、自检到 Formal 及跨交付缓存均不复用。
- **正式验收**：`python3 -m scripts.delivery_gate submit|validate|review|check|status`。submit 从真实完整 diff 与 Catalog 派生 Task 主体和变化依赖，绑定来源报告、风险、计划、未跟踪内容及 producer。`TASK_VALIDATION` 由不同 actor 按冻结计划执行；high-risk/formal 另需 `INDEPENDENT_REVIEW`。review 只读冻结 diff/evidence，`CATALOG_DECISION` 只核对 receipt/hash DAG，均不重跑命令。机械/局部链不伪造 review。每批 validate 最多 16 个不同 submission，各 Task 独立报告，仅同一 validator、同次窗口的等价检查可复用。正式发行风险由 Task 必需检查命中 CI formal 集合触发，仍需完整 Repository baseline。详见 [Delivery Gate](../docs/development/change-delivery/delivery-gate.md)。
- **Agent 委派**：实现优先 Qoder，验证和审查使用不同原生 Codex 子代理。模型按 policy 显式选择；Sol 升级需具体证据。Goal 活跃/未知且无等待适配时不启动 Qoder；已交接就结束当前轮，终态 callback 后核对并 ack。未知运行不重派，不改 Goal、身份或预算绕过。内部不使用 `create_thread`；新任务例外须满足 policy 的两轮验证、持续 Hook 阻塞、修复及能力边界证据，并获用户明确批准。详见 [Agent workflow](../docs/development/agent-workflow.md)。
- **Stop 与 CI**：正常 Stop 在一个窗口准备隔离测试服务并运行风险要求的 profiles，最后精确清理；中间汇报、提问、等待、Qoder 交接及只读 review/catalog 回合以单独一行 `<!-- lexiflow:intermediate -->` 结尾，不运行交付检查。普通交付不得使用该标记。首次失败 block；`stop_hook_active` 再失败以 `continue=false` 结束自动重试，不改称 PASS。Git Hook 只提醒，分支/PR CI 用 quick，正式标签 CI 保留完整 Change/Repository Verify。详见 [交付 Hook](../docs/development/change-delivery/hooks.md)。

## 脚本与开发数据边界

`scripts/` 只保存跨阶段复用、有稳定消费者及直接测试的能力；阶段工具放 ignored `tmp/phase-tools/<change-id>/`，不作公开入口或产品依赖。提升为长期脚本须用户批准并补合同和测试，职责见 [Scripts Reference](../docs/development/reference/scripts.md)。

只维护最新逻辑和唯一最新 SQL。结构变化须显式重建本项目开发库并完整重导；API 不隐式清库、不触及其他项目，不复用缓存或本机偏好仍引用的身份绑定不同资料。来源、事务、发布与隐私约束不变。

Catalog 的 planning-only 不授予派发或验收资格；须按当前范围分解 Task。共享策略先改 policy，再运行 `policy_projection --write` 和 `--check`，不手工维护投影。

## 文档与本机接入

图源仅为 Markdown 的 fenced `plantuml`；先按 documentation-policy 在 ignored `tmp/diagrams/` 校验、渲染和查看，再原样复制正文。静态检查不代替语义或视觉审阅，操作见[图文维护](../docs/development/reference/documentation.md)。

本机 skill 来自 `CODEX_HOME/skills`（默认 `~/.codex/skills`），不自动下载或修改配置。`local_skills check` 只读检查，`link` 显式创建 ignored 链接；缺源 BLOCKED、异目标拒绝覆盖，见[独立工具](../docs/development/reference/standalone-tools.md)。

venv、容器与隔离资源准备见[验证环境](../docs/development/operations/verification-environment.md)。报告、正式记录和 Qoder 运行分别留在 `tmp/quality/verification-reports/`、`tmp/quality/delivery-gate/`、`tmp/qoder-tasks/`，不进入共享 Harness。
