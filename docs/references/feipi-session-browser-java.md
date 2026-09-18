# 参考项目映射：复用工程边界，保留 LexiFlow 业务所有权

`feipi-session-browser-java` 提供的是成熟的工程组织方式：领域与适配器隔离、固定 Java 工具链、共享 Harness、回调优先执行和证据分层。LexiFlow 复用这些边界，再按英文学习产品定义自己的领域。

本页回答“借鉴什么、落实在哪里、哪些还未证明”。参考项目的业务模型、运行数据和开发机配置不成为本项目依赖。

## 先理解三组复用关系

| 参考模式 | 在 LexiFlow 解决的问题 | 本仓库入口 |
|---|---|---|
| 端口/适配器与唯一组合根 | 核心不绑定 HTTP、数据库、缓存和具体模型；模块只使用公开合同 | [模块与依赖](../architecture/modules-and-dependencies.md)、[机器边界](../../harness/module-boundaries.yaml) |
| 固定 Java 25、Wrapper、锁与确定性工具 | 构建不能继承开发机默认运行时，规则由唯一工具执法 | [质量验收分层](../development/quality-gate-layering.md)、[Java 清单](../../harness/java-product.manifest.yaml) |
| 调用者/运行器分层、完成落盘后回调、不可覆盖收据 | 工作进程不长期占用主代理；queued 与零退出码不能冒充业务验收 | [代理策略](../../harness/agent-policy.manifest.yaml)、[质量分层](../development/quality-gate-layering.md) |

第一组固定业务边界；第二组让边界可执行；第三组让执行结果可追踪。三者共同使用，不复制一套独立的客户端规则正文。

## 参考设计如何对应到学习产品

会话浏览器的来源/索引/领域隔离，在本项目映射为内容、词库、个人词汇、学习归约、语义和提示编排的公开合同。其核心禁止技术实现耦合，对应 LexiFlow 核心不依赖 HTTP、PostgreSQL、Redis、YouTube/Chrome 或模型 SDK。

客户端会话和检出副本仍由宿主拥有；Harness 不维护第二套文件写入授权状态。OpenSpec 变更管理非平凡变更，普通文件修改不依赖临时活动状态令牌。共享策略放在 AGENTS/harness，Codex/Qoder 客户端只保留入口。

工作完成先落盘，再回调精确父会话；主代理不循环轮询，兜底按共享策略执行。Qoder 的允许/禁止范围是协作合同，**不构成 OS 沙箱**，仍需核对真实差异。

## 已有工程实现与未完成证明分别查看

Java 25 启动器、Gradle Wrapper、严格锁、Spotless、Checkstyle、PMD、Java 来源检查、ArchUnit、JUnit/零跳过和 JaCoCo 已在本项目骨架落地。精确版本以 [Java 清单](../../harness/java-product.manifest.yaml) 与本仓库构建配置为准；拒绝行为按[校验手册](../development/validation/03-java-engineering.md)验证，不继承参考仓库或开发机版本。

本机成功构建不能证明干净双环境复现，也不能证明未来业务类型合规。正式 Java 任务收据仍需真实上下文与 Gradle 适配器，产品旅程则需后续实现和测试。[批准后复现计划](../development/post-approval-toolchain-verification.md) 保留后续检查顺序。

## 明确不复制的内容

不复制会话浏览器的业务模块、SQLite 模型、界面或发布脚本；不复制 `.idea`、`.local`、真实会话、个人设置、临时报告和凭据。Redis/缓存/事务发件箱的具体实现按 LexiFlow 的事实负责人决定。

不引入自动 Git 变异、第二套仓库写入者租约、全局轮询守护进程，或把 OpenSpec 活动变更当文件写权限。Gate 只覆盖有明确负责人和工作负载的检查，Java 能执法的规则不再增加同义 Python 扫描。
