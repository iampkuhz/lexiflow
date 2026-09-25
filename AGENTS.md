# LexiFlow Agent 工程规则

先边界，再闭环。

## 护栏

- 中文沟通；标识符、命令、路径、API 用英文。
- 先定位、按需读取；不覆盖或回滚他人改动。
- 不提交密钥、token、真实字幕、观看历史、模型输入输出或本地运行数据。
- 非平凡产品、agent、harness、gate 或跨模块变更先创建或复用 `openspec/changes/<id>/`。
- 规则仅在 `AGENTS.md` 与 `harness/`；客户端仅入口。
- 图源只在 Markdown 的 fenced `plantuml`；先在 ignored `tmp/diagrams/` 校验/预览，再原样复制正文。禁止提交调试产物或由正文链接它们。
- 开发期全仓无历史兼容，仅最新逻辑/SQL；结构变更重建重导，边界见 Harness。
- 文档仅最新版；非 comparison/diff 路径和标题禁版本对照/迁移历史；执行状态仅状态页。

## 架构约束

- 产品后端统一使用 Java 25；Spring Boot 只进入 `backend/apps/`、`backend/platform/` 与组合根。Python 只用于 `scripts/`、`harness/`、Gate、生成器和审计，不得承载产品 Domain、Application、API 或 worker 业务逻辑。
- 采用 Modular Monolith；部署可为 `api` 与 `worker` 两个进程，业务边界不得因此拆成微服务。
- 依赖由组合根指向 application，再指向 domain/ports；domain 不依赖 HTTP、数据库、缓存或具体模型 Provider。
- 模块不得直接读写其他 Domain 拥有的数据；跨模块只通过公开 contract。
- Chrome Extension 保持薄；仅允许用户显式、本机私有的词段抑制偏好，不上传、不推断熟悉度、无账号或同步。
- 英文不等后端；观看仅用已发布资料，禁用模型（含补全/预取）；模型仅第三阶段事后分析，校验发布供后续观看。
- PostgreSQL 是事实存储；Redis、浏览器缓存和投影均可重建。

## 按需加载共享规则

- 开始任务先读 `harness/README.md` 与 `harness/manifest.yaml`，按工作类型加载对应合同；长期规范在 `openspec/specs/`，当前变更在 `openspec/changes/`。
- 子任务前读 `harness/agent-policy.manifest.yaml` 的 `subagent_protocol` 与 `qoder_delegation`；超过 10 分钟且范围可隔离、或需大量新上下文而可简洁验收的任务优先派发。模型显式按 `codex_model_policy` 选择，遵守范围、身份、回调和并发合同。
- 客户端运行边界见 `harness/agent-runtime.manifest.yaml`。共享字段为 policy 的确定性投影，不手工维护副本；更新后运行 `python3 -m scripts.repository.policy_projection --check`。
- 调用者不伪造 runner 身份；实现者不能自行签发 validation/review；完成或退出零不等于验收。集成验证串行，不覆盖其他写入者的修改。
- 修改文档时读取 `harness/documentation-policy.yaml`；图表生成/校验按其中 skill 声明加载 `feipi-plantuml-generate-diagram`，仅 skill 工程维护加载 `feipi-skill-govern`。缺少工具不得假装已执行；本机接入见 `harness/README.md`。

## 验证与 Git

- 语言检查和 Hook 见 `harness/README.md`；客户端只触发，不复制规则或代替验证。
- 功能交付只在 `TASK_VALIDATION` 执行确定性检查和业务测试；`INDEPENDENT_REVIEW` 只复核冻结 diff 与 validation evidence；`CATALOG_DECISION` 只验证 validation/review/dependency receipt 和 hash DAG。后两层不得重跑交付命令。
- 结果只用 `PASS`、`BLOCKED`、`FAIL`；必需检查未运行或跳过不得称 `PASS`。
- 交付须取得同输入 Change/Repository Verify PASS；Stop 执行与中间回合标记见 Harness。Hook 不限制编辑或 commit，不替代独立 Formal Gate。
- `scripts/` 只放跨阶段、稳定且受测的能力；仅当前阶段使用的脚本放 ignored `tmp/phase-tools/<change-id>/`，不得成为公开入口或 Registry 输入。
- 禁止自动 stage、commit、merge、rebase、reset、stash、force、push；Git 集成/发布须用户明示。
