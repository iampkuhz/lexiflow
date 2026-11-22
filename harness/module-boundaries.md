# 1. 领域归属与跨模块边界

依赖方向、语言与运行边界遵守 [AGENTS.md](../AGENTS.md)。Java 包与 Gradle 依赖由 [架构测试](../backend/verification/architecture/src/test/java/io/lexiflow/architecture/LayerArchitectureTest.java)执行校验；此处只规定依赖图无法表达的业务归属。

## 1.1 业务所有权

- **Lexicon** 拥有共享词汇知识：词与短语、词义、来源 BNC 的可空用户排名、CEFR、领域元数据及发布版本；已发布资料只读。其 Application 负责导入与版本感知查询。其他领域不能接管词库知识或发布状态。
- **Enrichment** 拥有字幕语境、候选流水线、提示必要性策略、已发布语义资料匹配及标注决策与结果。字幕提示 Use Case 留在所属领域；其词汇知识来自 Lexicon 公开 Domain contract。
- **Platform Adapters** 实现数据库、缓存、安全、事后模型、任务运行及观测等技术集成；技术运行方式不产生新的业务所有者。不得以调度、模型 Provider 或存储实现重新划分领域。
- **Enrichment** 拥有公开的本机显式偏好与每日提示计数存储 contract；`:adapters` 仅为实现这些 contract 可直接依赖 `:enrichment`，不能绕过 contract 直接读写其他数据或词库表。本机可写数据在独立 SQLite，词库保持只读。

## 1.2 数据与客户端

跨模块交互只通过拥有者公开 contract；可见的类型依赖不授予直接读写别域数据的资格。技术适配器与缓存不成为数据所有者。

Chrome Extension 不拥有业务事实；显式偏好与已呈现提示的词条身份只提交本机 API，不持久化在浏览器。Enrichment contract 与独立 SQLite 适配器拥有本机可写事实，不与只读发布词库共库；客户端、观看与模型的限制以 AGENTS.md 为准。

## 1.3 工具栏弹窗与管理页

工具栏弹窗只承载此刻观看所需的最少设置和简短状态，打开即显示必需控件。不得放点击后才展开的默认隐藏内容，包括折叠区、详情菜单、设置抽屉或列表；需要解释、展开、管理或排障的功能必须作为独立管理页内容。弹窗可提供跳转入口；按运行状态显示错误、保存结果或提示不属于折叠功能。

## 1.4 接口与存储兼容号

普通界面、端口、性能或词库内容修改不读、不改兼容锁。变更扩展实际使用的 HTTP 合同或 SQLite 持久结构时，从仓库根执行 `node scripts/contracts/version-contract.mjs check --scope <对象> --base <目标提交>`；仅使用同入口的 `sync` 写入版本锁。提取不完整先修实际声明与回归，不手改数字绕过。兼容对象只有 `extension-api`、`sqlite-storage`。启动校验分别核对词库和偏好文件，不按扩展接口号或总体存储号拒绝结构与身份均已验证的文件。不对未知卷补标或清库。
