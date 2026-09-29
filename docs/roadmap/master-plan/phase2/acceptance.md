# 1. 主线集成验收合同

上级：[第二阶段任务](tasks.md)。本页定义 QLT-2001 的范围和断言，执行状态仅见[状态页](status.md)；不以合成主线通过代替真实 YouTube 或 Docker 发布验收。

## 1.1. 实际产品链

在既有 `:integration-tests:runtimeSmokeTest` 中用合成临时 canonical CSV 与 StarDict CSV，启动真实 `LexiconImportMain` 子进程执行 publish；只使用 Harness 提供的 PostgreSQL 测试连接，创建独立随机 schema，以最新 SQL 初始化。随后启动实际 `:api:bootJar` 的独立进程，调用正式 HTTP readiness 与 caption-hints，不注入 fake Repository、候选列表或用例。

`PublishedPipelineRuntimeTest` 拥有断言，`PipelineRuntimeFixture` 只管理本次 schema/受控子进程/HTTP/临时文件，`PipelineSyntheticSources` 只生成可解释的合成 CSV 和请求。必要 JSON 解析依赖沿现有 Spring Boot BOM；只对本集成模块更新锁文件。CLI 子进程 classpath 由 Gradle 明确传入，不依赖 Gradle worker 的 java.class.path 或运行机器的目录猜测。

所有 API 进程显式指定 formal 模式、隔离 JDBC、随机 loopback 端口和敏感记录关闭/专用临时路径；不得继承默认分析台账。记录按结构解析，不能仅匹配某个数字或字符串存在。进程和 HTTP 均有超时；退出精确停止本次子进程并删除本次 schema/临时资源，不访问或清理真实资料。

## 1.2. 必须新增的跨模块场景

1. canonical 发布后 formal readiness 为 200；HTTP 覆盖基础词及基础词形阻断、可提示的重点/长尾/未知频率条目、可靠短语/噪声短语、坏首义不补位，以及同形多候选歧义。样例的预期来自冻结分类/资格规则，不为通过测试更改产品规则。必要时分多次短请求，避免把整个规则表拼成长字幕。
2. StarDict 合成输入通过真实导入链进入同一查询路径，证明来源适配与准备/发布接通；明确缺排名不等于低频阻断，首候选坏释义不由后项替代。
3. 从日志中的实际请求聚合计数验证冷键 miss/DB 批次、重复请求命中及已发布版本；不把有版本查询的缓存命中称为零 DB 访问，不用 wall-clock 快慢替代缓存事实。
4. 一个进程内原子发布第二版后，HTTP 必须返回新版本/新资料，旧缓存提示不混入。真实数据库约束使第三次发布失败后，HTTP 仍返回第二版，事实存储没有半份替换；不以模拟异常冒充数据库回滚。
5. 增量请求通过真实 HTTP 验证已处理前缀与新片段映射/覆盖，范围是 UTF-16 半开且提示落在有效新增区间；不能让上次快照掩盖新版本身份。

## 1.3. 复用回归及证明边界

基础规则细节复用 lexicon 导入准备/资格测试；同形冲突与 UTF-16 映射复用 enrichment 测试；无资料启动、正式/演示与依赖故障复用 API/runtime 测试；敏感记录故障复用 API HTTP sink failure 测试；取消/迟到、英文优先与显示复用 extension 生命周期、stream 及合成 content bundle 检查。复用矩阵见下节，不把局部单测改称完整进程级覆盖。

直接运行通过既有隔离服务上下文执行 `python3 -m scripts.environment.java_exec backend/gradlew -p backend :integration-tests:runtimeSmokeTest`。正式同输入 Change/Repository Verify 与独立 validation/review/check 按 Harness 执行；必需项缺环境为 BLOCKED，不跳过。Docker 干净安装、两种 CPU 架构、375 秒资源预算和真实 YouTube 体验仍由发布闭环验收承担。

### 1.3.1. 新增主线定位

集成模块 `PublishedPipelineRuntimeTest.publishesCanonicalAndStarDictAndExercisesServingReplacementRollbackAndIncrementalRanges` 通过 `PipelineRuntimeFixture` 管理真实进程和隔离资源，使用 `PipelineSyntheticSources` 构造来源与字幕。它覆盖 1.2 节五类要求；测试报告中的该方法是新增主线的执行定位，断言与输入共同决定证明范围，不能只引用方法名或退出码。

### 1.3.2. 复用覆盖定位

以下定位是必须保留的回归，不表示本次已运行通过。Java 类位于对应模块的 `src/test/java`，启动 smoke 位于集成模块的 `src/runtimeSmoke/java`；新增进程链断言与这些局部回归共同组成矩阵。

| 能力 | 测试定位 | 证明边界 |
| --- | --- | --- |
| 分类、资格与预热分离 | lexicon `ClassificationPolicyTest.distinguishesActualStardictRankSourceFromExplicitCsvZipfIncludingZero`、`preservesBasicWordAndPhraseAliasBoundary`；`HintPreparationTest.keepsFrequencyEvidenceAndPrewarmSeparateFromHintEligibility` | 纯规则，不代表持久化/HTTP |
| 导入冲突与坏资料拒绝 | lexicon `LexiconImportPlanTest.keepsNaturalInflectionToLemmaAmbiguityAcrossChunks`、`rejectsCanonicalAliasCollisionAcrossChunks`；`LexiconImportPreparationTest.rejectsDigestAndCanonicalCollisionAcrossStream` | 准备与批次不变量 |
| 真实数据库回滚、候选身份 | adapters `PostgresLexiconRepositoryIntegrationTest.databaseConstraintRollbackHasConfirmedTerminalAndKeepsPublishedData`、`publishesCompleteDataAndQueriesOneServingTableIncludingAmbiguity` | 独立 PostgreSQL，非浏览器 |
| 跨片段与版本混用防护 | enrichment `IncrementalCaptionUseCaseTest.queriesOnlyContiguousAppendIntervalsAndMapsCrossSegmentPhrase`、`suppressesHintsFromDifferentPublishedVersionsAcrossIntervals`、`hiddenAndUnmatchedCandidatesFromAnotherVersionSuppressRealHintAcrossIntervals` | 用例级映射与安全边界 |
| 无资料、演示、依赖异常 | integration `SystemRuntimeSmokeTest.verifiesDependencyProtocolsSchemaInitializationAndApiHealth`；api `LexiconReadinessHttpTest.emptyFormalProcessIsLiveButNotReadyAndRejectsHintsWithoutLeaks`、`CaptionHintHttpTest.explicitDemoDoesNotAdvertiseFormalReadiness`、`CaptionRequestFailureHttpTest.dependencyFailureUses503AndInternalFailureUsesFixed500WithoutPayload` | 启动 smoke 为真实进程，后两类 HTTP 使用受控测试装配 |
| 日志故障不改变响应 | api `CaptionRequestFailureHttpTest.sensitiveRecordFailureKeepsSuccessfulResponseAndSharesRequestId`、`CaptionRequestSinkFailureHttpTest.eventSinkFailureDoesNotChangeRealHttpResponse` | 故障注入的 HTTP 测试，不注入正常发布链 |
| 取消、迟到与英文优先 | `extension/tests/stream.test.mjs` 的 `separates cancelled scheduled sends, in-flight cancellation, and late completion`；`extension/tests/caption-capture.test.mjs` 的 `actual capture rejects hashes across hidden and clear boundaries, then restores only the current caption`；`extension/tests/experience-acceptance.mjs` 的 `runExperienceAcceptance` | 前两者为确定性单测；后者真实扩展加合成页面/延迟传输，不是实际 YouTube |
