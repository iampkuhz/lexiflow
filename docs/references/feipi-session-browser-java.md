# 参考项目映射：复用工程边界，保留 LexiFlow 业务所有权

`feipi-session-browser-java` 提供的是成熟的工程组织方式：领域与适配器隔离、固定 Java 工具链、共享 Harness、callback-first 执行和证据分层。LexiFlow 复用这些边界，再按英文学习产品定义自己的 Domain。

本页回答“借鉴什么、落实在哪里、哪些还未证明”。参考项目的业务模型、运行数据和开发机配置不成为本项目依赖。

## 先理解三组复用关系

| 参考模式 | 在 LexiFlow 解决的问题 | 本仓库入口 |
|---|---|---|
| ports/adapters 与唯一组合根 | 核心不绑定 HTTP、数据库、缓存和具体模型；模块只使用公开合同 | [模块与依赖](../architecture/modules-and-dependencies.md)、[机器边界](../../harness/module-boundaries.yaml) |
| 固定 Java 25、Wrapper、locks 与确定性工具 | 构建不能继承开发机默认 runtime，规则由唯一工具执法 | [工程与交付](../architecture/engineering-and-delivery.md)、[Java manifest](../../harness/java-product.manifest.yaml) |
| caller/runner 分层、完成落盘后回调、不可覆盖收据 | worker 不长期占用 Main；queued 与 exit 0 不能冒充业务验收 | [agent policy](../../harness/agent-policy.manifest.yaml)、[质量分层](../development/quality-gate-layering.md) |

第一组固定业务边界；第二组让边界可执行；第三组让执行结果可追踪。三者共同使用，不复制一套独立的客户端规则正文。

## 参考设计如何对应到学习产品

Session Browser 的 source/index/domain 隔离，在本项目映射为 Content、Lexicon、Vocabulary、Learning、Semantic 和 Enrichment 的公开 contract。其 core 禁止技术实现耦合，对应 LexiFlow core 不依赖 HTTP、PostgreSQL、Redis、YouTube/Chrome 或模型 SDK。

客户端会话和 checkout 仍由宿主拥有；Harness 不维护第二套文件写入授权状态。OpenSpec change 管理非平凡变更，普通文件修改不依赖临时 active-state token。shared policy 放在 AGENTS/harness，Codex/Qoder 客户端只保留入口。

工作完成先落盘，再回调精确父会话；Main 不循环轮询，首次/后续兜底按共享 300/600 秒规则。Qoder 的 allowed/forbidden scope 是协作合同，**不构成 OS 沙箱**，仍需核对真实 diff。

## 已有工程实现与未完成证明分别查看

Java 25 launcher、Gradle Wrapper、strict locks、Spotless、Checkstyle、PMD、Java source gates、ArchUnit、JUnit/零跳过和 JaCoCo 已在本项目骨架落地。当前精确版本及拒绝行为以 [工具审查](../reviews/phase-1-deterministic-tools-audit.md) 与本仓库配置为准；不把参考仓库 Wrapper 版本直接复制过来。

本机成功构建不能证明干净双环境复现，也不能证明未来业务类型合规。正式 Java Task receipt 仍需真实上下文与 Gradle adapter，产品旅程则需后续实现和测试。[批准后复现计划](../development/post-approval-toolchain-verification.md) 保留后续检查顺序。

## 明确不复制的内容

不复制会话浏览器的业务模块、SQLite 模型、UI 或发布脚本；不复制 `.idea`、`.local`、真实 session、个人 settings、临时报告和凭据。Redis/cache/outbox 的具体实现按 LexiFlow 的事实 owner 决定。

不引入自动 Git mutation、第二套仓库 writer lease、全局轮询 daemon，或把 OpenSpec active change 当文件写权限。Gate 只覆盖有明确 owner 和 workload 的检查，Java 能执法的规则不再增加同义 Python 扫描。

## 查看原始审计而非继承开发机版本

[2026-09-16 参考审计](history/feipi-session-browser-java-2026-09-16.md) 保留当时 Wrapper、lock、源码及 shell inventory。它是有日期的历史观察，不能用作今天的 runtime pin 或构建状态。LexiFlow 产品始终使用 Java 25。
