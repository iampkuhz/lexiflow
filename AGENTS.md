# LexiFlow Agent 工程规则

先定边界，再做最小闭环。

## 首要护栏

- 中文沟通；标识符、命令、路径与 API 使用英文。
- 先搜索定位，只读取必要上下文；不覆盖或回滚他人改动。
- 不提交密钥、token、真实字幕、观看历史、模型输入输出或本地运行数据。
- 非平凡产品、agent、harness、gate 或跨模块变更先创建或复用 `openspec/changes/<id>/`。
- 共享规则只在 `AGENTS.md` 与 `harness/` 维护；客户端配置只保留入口。
- 图源只在 Markdown 的 fenced `plantuml`；先在 ignored `tmp/diagrams/` 校验/预览，再原样复制正文。禁止提交调试产物或由正文链接它们。
- 文档只留最新版；除非路径和标题均明确为 comparison/diff，禁止用当前、目标、改前/后等作版本对照或写迁移历史；执行状态只在状态页。

## 架构约束

- 产品后端统一使用 Java 25；Spring Boot 只进入 `backend/apps/`、`backend/platform/` 与组合根。Python 只用于 `scripts/`、`harness/`、Gate、生成器和审计，不得承载产品 Domain、Application、API 或 worker 业务逻辑。
- 采用 Modular Monolith；部署可为 `api` 与 `worker` 两个进程，业务边界不得因此拆成微服务。
- 依赖由组合根指向 application，再指向 domain/ports；domain 不依赖 HTTP、数据库、缓存或具体模型 Provider。
- 模块不得直接读写其他 Domain 拥有的数据；跨模块只通过公开 contract。
- Chrome Extension 保持薄；产品不维护账号、多人档案、学习行为归约或跨设备状态同步。
- 英文字幕渲染不得等待后端或模型；Rules 决定是否提示，Models 决定语境含义。
- PostgreSQL 是事实存储；Redis、浏览器缓存和投影均可重建。

## 按需加载共享规则

- 开始任务先读 `harness/README.md` 与 `harness/manifest.yaml`，按工作类型加载对应合同；长期规范在 `openspec/specs/`，当前变更在 `openspec/changes/`。
- 子任务派发前必须读取当前 `harness/agent-policy.manifest.yaml` 的 `subagent_protocol` 与 `qoder_delegation`；模型按其中 `codex_model_policy` 选择并显式传参，不以角色或继承模型替代。规模、范围、身份、结果布局、回调和并发均遵守该合同。
- 客户端运行边界见 `harness/agent-runtime.manifest.yaml`。共享字段为 policy 的确定性投影，不手工维护副本；更新后运行 `python3 -m scripts.harness.policy_projection --check`。
- 调用者不伪造 runner 身份；实现者不能自行签发 validation/review；完成信号与进程退出零不等于验收通过。集成验证串行，不覆盖其他写入者的修改。
- 修改文档时读取 `harness/documentation-policy.yaml`；图表生成/校验按其中 skill 声明加载 `feipi-plantuml-generate-diagram`，仅 skill 工程维护加载 `feipi-skill-govern`。缺少工具不得假装已执行；本机接入见 `harness/README.md`。

## 验证与 Git

- Java 源码格式、静态分析、注释、架构和测试规则由 Gradle 下的 Spotless、Checkstyle、PMD、Java quality gate、ArchUnit、JUnit 与 JaCoCo 唯一执行；Python Gate 只负责规划、编排、证据、收据和跨产物治理，不得重复扫描 Java 源码实现同义规则。
- 功能交付只在 `TASK_VALIDATION` 执行确定性检查和业务测试；`INDEPENDENT_REVIEW` 只复核冻结 diff 与 validation evidence；`CATALOG_DECISION` 只验证 validation/review/dependency receipt 和 hash DAG。后两层不得重跑交付命令。
- 结果只使用 `PASS`、`BLOCKED`、`FAIL`；必需检查未运行或跳过不得称 `PASS`。
- 交接前显式运行 `python3 scripts/gates/cli.py run --mode incremental`；业务代码还需运行其产品测试。
- 禁止自动 stage、commit、merge、rebase、reset、stash、force 或 push。Git 集成与发布只按用户明确指令执行。
