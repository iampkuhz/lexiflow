# LexiFlow Harness

Harness 是**人类工程规则**的唯一共享正文；机器可调配置在 `config/engineering/`，固定协议、schema 与实现判据归代码。先遵守 [AGENTS.md](../AGENTS.md)，再按下面的任务选择规则，**不要求读取全部文件**。

## 按任务加载

| 当前任务 | 必须按需读取 | 操作与实现导航 |
| --- | --- | --- |
| 定界、派发、接手、验收或跨线程协作 | [Agent 协作规则](agent-policy.md)；派发前重读 `codex_model_policy` | [Agent workflow](../docs/scenarios/delegate-work.md)、[Delivery Gate](../docs/reference/delivery-gate.md) |
| 产品或跨模块实现与架构审查 | [模块边界](module-boundaries.md) 与本次 OpenSpec | [系统边界](../docs/system/modules.md) |
| 编写版本汇总卡或可执行任务卡 | [任务卡编写规范](task-writing.md) | — |
| 文档、内嵌图源或本机 skill 接入 | [文档规则](documentation-policy.md)；按触发读取完整 skill | [图文维护](../docs/scenarios/maintain-documentation.md)、[独立工具](../docs/reference/standalone-tools.md) |
| 调整检查、冻结范围、风险路径或 Python 工具配置 | 相关规则及 [工程配置地图](../docs/reference/configuration.md) | [检查入口索引](../docs/reference/checks.md)、[优化检查](../docs/scenarios/optimize-repository-gates.md) |

理解职责先读[工程三体系总览](../docs/system/engineering.md)，日常操作从[生命周期入口](../docs/README.md)进入；公开命令与参数由真正 CLI 定义，文档按需索引，不维护第二份命令注册表。

## 执行边界

- **开发自检**按完整真实 diff、场景 triggers 与 Check 依赖闭包冻结 profile/report；未知覆盖失败关闭。实现者可做静态、编译与精准测试，开发 PASS 不是完整 Verify 或 Formal PASS。开发 CLI 与 Stop 的择一执行及独立性见[协作规则](agent-policy.md)，操作见[执行场景](../docs/scenarios/run-repository-gates.md)。
- **本机浏览器验证**先做静态、编译、非浏览器单测和 API 检查。需要浏览器的布局、DOM、截图和交互检查默认无头，并使用独立配置；改动收口后按受影响范围集中跑一次，诊断仅精准重跑失败或变化项。不得自动切到可见窗口或使用用户正在用的浏览器。确需桌面原生交互或用户指定 Chrome 配置时，先说明要验证什么、为什么无头不足，取得用户同意后再集中执行。
- **完整 Verify**按本次证明目标显式运行，不与开发检查、精确诊断惯例性叠跑；同候选的正式完整基线由独立 validator 负责。见[Verify](../docs/reference/verification.md)。
- **正式验收**由不同真实 actor 对明确目标仓库执行冻结 TASK_VALIDATION，高风险另需 INDEPENDENT_REVIEW；身份与目标分开认证，review 只读冻结证据，CATALOG_DECISION 只核 receipt/hash DAG。见[协作规则](agent-policy.md)。
- **Stop 与 CI**正常 Stop 运行开发检查；中间汇报、提问、等待及只读 review/catalog 以独立行 `<!-- lexiflow:intermediate -->` 结尾，普通交付不使用该标记。Hook 不锁编辑/commit、不替代 Formal，不安装或维护 Git 提醒 Hook；跳过、终态、零退出及失败重试不是 PASS。见[Hook](../docs/reference/hooks.md)与[CI](../docs/scenarios/github-actions.md)。

## 脚本与开发数据

`scripts/` 只放跨阶段复用、有稳定消费者且直接受测的能力；阶段工具留在 ignored `tmp/phase-tools/<change-id>/`，不得作公开入口、Registry 输入或产品依赖。提升为长期脚本须用户批准并补合同与测试，职责见 [Scripts Reference](../docs/reference/scripts.md)。工程配置、规则和实现分别维护，不以同步投影或重复 policy 门禁证明一致性；Catalog planning-only 不授予派发/验收资格。

发布、开发、试用及 Agent 操作遵守[本机容器槽位合同](../openspec/specs/deployment-images/spec.md#11-两步使用与环境前提)。禁止以临时 helper、预览或手写 `podman run -d` 绕过固定槽位在日常引擎留驻容器。需要独立 API 容器的自动化在隔离验证引擎运行；一次性测试服务和构建工具由执行器限时管理，退出后按完整 ID 与归属证据确认清理（包括停止状态），无法确认则报告 `BLOCKED`。不因名称相似清理未知资源。日常试用更换使用固定槽位的计划及替换入口；身份冲突不换端口或接管。

用户试用只使用[固定试用槽位入口](../docs/scenarios/local-development.md#131-固定试用槽位)。测试交付一律指向该槽位的固定产物路径，不按日期、版本或构建号另发 Downloads 目录、临时脚本或手填摘要命令。构建与预检不等于授权启动或替换日常服务。

只维护最新逻辑与唯一最新 SQL。结构变化须显式重建本项目开发库并完整重导；API 不隐式清库、不触及其他项目，不复用缓存或本机偏好仍引用的身份绑定不同资料。来源、事务、发布与隐私约束不变。

## 本机工具与证据

本机 skill 来自 `CODEX_HOME/skills`（默认 `~/.codex/skills`），不自动下载或修改配置。接入工具只读检查或显式创建 ignored 链接；缺源 BLOCKED、异目标拒绝覆盖，见[独立工具](../docs/reference/standalone-tools.md)。图源按[文档规则](documentation-policy.md)先在 ignored `tmp/diagrams/` 校验、渲染、视觉核对再原样复制；静态检查不代替语义或视觉审阅。

venv、容器与隔离资源见[验证环境](../docs/scenarios/verification-environment.md)。报告与当前正式记录在 ignored `tmp/quality/verification-reports/` 和 `tmp/quality/delivery-gate/current/`；原始历史记录不改写、不自动消费，不进入共享规则。
