# 1. 领域归属与跨模块边界

依赖方向、语言与运行边界遵守 [AGENTS.md](../AGENTS.md)。Java 包与 Gradle 依赖由 [架构测试](../backend/verification/architecture/src/test/java/io/lexiflow/architecture/LayerArchitectureTest.java)执行校验；此处只规定依赖图无法表达的业务归属。

## 1.1 业务所有权

- **Lexicon** 拥有共享词汇知识：词与短语、词义、频率、CEFR、领域元数据及发布版本；其 Application 负责导入与版本感知查询。其他领域不能接管词库知识或发布状态。
- **Enrichment** 拥有字幕语境、候选流水线、提示必要性策略、已发布语义资料匹配及标注决策与结果。字幕提示 Use Case 留在所属领域；其词汇知识来自 Lexicon 公开 Domain contract。
- **Platform Adapters** 实现数据库、缓存、安全、事后模型、任务运行及观测等技术集成；技术运行方式不产生新的业务所有者。不得以调度、模型 Provider 或存储实现重新划分领域。

## 1.2 数据与客户端

跨模块交互只通过拥有者公开 contract；可见的类型依赖不授予直接读写别域数据的资格。技术适配器与缓存不成为数据所有者。

Chrome Extension 不拥有业务事实，只拥有用户显式操作的本机私有词段抑制偏好；客户端、观看与模型的限制以 AGENTS.md 为准。
