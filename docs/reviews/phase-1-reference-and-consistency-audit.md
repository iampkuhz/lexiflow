# Phase 1 参考一致性与确定性执行审查

审查对象是 `LF-WP-P1-DETERMINISTIC-REFERENCE-AUDIT-096`，绑定 `audit-phase1-deterministic-enforcement@1.0.0` 的 QLT-0002@1、QLT-0009@4、QLT-0005@1，估时 160 分钟。本文只描述冻结快照，不把主会话正在修改的 live backend 当作证据。

## 范围与证据边界

冻结输入为 `tmp/quality/phase1-audit-20260917/baseline/` 的 53 个文件。`manifest.json` 的 SHA-256 为 `c2c12d708d0109625f95bdb034f77186bb61b8a3da6c523d5f0841c47bbb5168`，与 dispatch 中的绑定相同，且 53 项文件逐项校验一致。参考只读仓库为 `/Users/zhehan/Documents/tools/llm/feipi-session-browser-java`，只读取其 `java/`、`harness/`、`docs/gates/` 与 `scripts/gates/` 的源码和文档，排除了 build、`.gradle`、tmp、logs 与 worktree 运行产物。

没有运行 Gradle、Python、产品 checker、TaskV、review、catalog、Git、Qoder 或轮询；因此本文是静态一致性审查，不是当前构建或 Gate receipt。参考仓库的历史 receipt、运行统计和本机缓存也不被当作 LexiFlow 当前状态。

## 结论

LexiFlow Phase 1 已有一条边界清楚的 Java 确定性质量链：Java 25 toolchain、`-Werror`、Strict dependency locking、Spotless、Checkstyle、PMD、Javadoc、JaCoCo、ArchUnit、Gradle project dependency guard、零跳过测试检查，以及集中式 Java source gates。参考仓库证明了这些工具可如何组织，但不应把其 Web 规则、Python control plane 或更细的 receipt 生命周期整体复制进 Phase 1。

此前版本把冻结文件清单之外的 executor/receipt/lockfile 误写成仓库缺口，现予更正：53 文件快照只证明纳入快照的内容，不能证明未纳入文件不存在。主会话已提供当前事实：各 leaf 存在 `gradle.lockfile`，严格锁定的完整 `deliveryFull` 已成功；本次遵守只读审查边界，没有独立重跑。Java source gate 的 `PASS/FAIL` 是规则结果，不能直接套用参考 control plane 的 `BLOCKED` 语义；运行状态和分层验收仍由各自 owner 负责。

## 工具覆盖对照

| 能力 | LexiFlow 冻结快照 | 参考仓库 | 判断 |
|---|---|---|---|
| Java 版本与编译 | `lexiflow.java-base.gradle.kts:10-22` 固定 Adoptium Java 25、release 25、`-Xlint:all -Werror` | `java/gradle/build-logic/src/main/kotlin/feipi.java-base.gradle.kts` 同样集中固定 toolchain 与编译选项；`scripts/toolchain/java_gradle.py:17-66` 还验证 Temurin 25 | 应保留；可借鉴 launcher 的失败语义，暂不复制参考仓库的整套 runtime 入口 |
| 构建图与锁定 | `backend/settings.gradle.kts:17-40` 声明模块图；`backend/build.gradle.kts:19-22` 在 root/leaf 严格锁定 | `java/settings.gradle.kts:13-38` 以逻辑 project ID 映射物理目录；`java/build.gradle.kts:24-35` 锁 root lockfile 并聚合 leaf check | 配置与主会话提供的当前事实一致；本次冻结清单未包含 lockfile，不能据此推断缺失。完整 `deliveryFull` 成功由主会话负责验收 |
| 格式、Checkstyle、PMD | `lexiflow.java-quality.gradle.kts:22-61`，均 `ignoreFailures=false`；结果 XML/HTML 已声明 | 参考 `feipi.java-quality.gradle.kts:3-75`，还将 Kotlin DSL 格式纳入 Spotless，并明确 PMD 只扫 main | Java 产品规则已够用；不复制参考的 Kotlin/Web 目标，需补充“实际 source set 与配置输入”的证据入口 |
| Javadoc/注释 | Javadoc `Werror` 与 DocLint 在 `lexiflow.java-quality.gradle.kts:63-73`；`RecordComponentJavadocGate` 检查 record 类型及 `@param`，`JavaCommentLanguageGate` 检查所有 Java main/test 注释 | 参考 quality-gates 另有 `ChineseJavadocVerificationTest` 和可配置 rule registry | LexiFlow 的三条自定义规则是有价值的最小闭环；参考的可选 rule/基线更新只在后续有明确需求时引入 |
| ArchUnit/依赖方向 | `LayerArchitectureTest.java`（修复后）从产品 main compile dirs 导入，并检查 Domain/Application、具体 adapter、组合根、Spring 与 domain→application | 参考 `java/config/architecture/java-modules.yaml` 加多项 architecture/contract tests | 原冻结 baseline 的无限扫描/允许为空已修复；参考仓库的更多业务规则仍不应整体复制 |
| JUnit/跳过与失败 | Java test `useJUnitPlatform()`、XML/HTML 在 `lexiflow.java-quality.gradle.kts:79-90`；`VerifyNoSkippedTestsTask.kt:25-68` 检查 XML 缺失、0 tests、skip/abort | 参考 build root 的 `verifyNoSkippedJavaTests` 还统计 failures/errors/aborted，并显式处理 Java/Kotlin source dirs | LexiFlow 的 Gradle test 失败通常会先使 `test` 失败；自定义任务仍未把 failure/error 计入自身报告，建议增强诊断信息但不重复实现 Gradle 判定 |
| JaCoCo | `backend/build.gradle.kts:137-148` 聚合 leaf execution data、source/classes，生成 XML/HTML；`qualityFull:161-165` 依赖 aggregate report | 参考 `java/build.gradle.kts:49-79` 在 projectsEvaluated 后动态收集实际参与模块 | 覆盖路径已明确；应确认无 execution data 时是失败、空报告还是仅报告生成，阈值执法目前未出现 |
| Java source gate | `QualityGateMain.java:18-28` 运行三规则，先写 JSON，再以非 PASS 抛错；`JavaSourceGateRunner.java:25-55` 固定顺序、稳定排序 | 参考 `QualityGateCli.java:35-174` 有唯一 CLI、规则选择、输入按规则裁剪、baseline update 边界；`quality-gates/build.gradle.kts:56-176` 强制单 JavaExec | LexiFlow 已采用单入口，需保留；参考的 selector/baseline 语义属于可选扩展，当前不应放宽规则 |
| Gate 控制面与证据 | 冻结快照包含 `harness/gate-check-registry.yaml`、`agent-runtime.manifest.yaml` 与 `scripts/toolchain/java_gradle.py`；dispatch 明确本次不审查完整执行产物 | 参考 `docs/gates/gate-control-plane.md:3-21,138-185` 规定 list/explain/plan/run、Trigger、`NOT_TRIGGERED`、`PASS/BLOCKED/FAIL`、重跑命令；`scripts/gates/evidence/receipt_store.py:280-363` 写不可覆盖 run 目录、plan fingerprint、日志与 summary | 只能报告本次输入未覆盖 reference executor/receipt 的细节，不能认定 LexiFlow 缺少它们；继续沿用 Java owner 与 Harness 分层 |
| 缓存与构建锁定 | Gradle caching/configuration-cache/parallel=false 在 `backend/gradle.properties:1-5`；非缓存 `VerifyNoSkippedTestsTask` 明确 `@DisableCachingByDefault` | 参考 `java/gradle.properties:1-5` 相同方向；quality-gates 对 deterministic report 与 execution-scoped output 分开 cache | LexiFlow 已正确区分测试结果检查与可缓存静态检查；需要在文档中明确“缓存命中不等于执行 PASS，receipt 必须记录 FROM-CACHE/未执行语义” |
| Python 责任边界 | AGENTS 和 `docs/development/quality-gate-layering.md:1-23` 明确 Python 不重复扫描 Java | 参考 control plane 的 Python 只做 catalog/planning/evidence/execution classification，Java 规则仍由 Gradle/Java owner 执行 | 应保留该分层；不要把 Java AST/Checkstyle/PMD 再实现一份 Python scanner |

## 具体发现与优先级

### 证据范围限制：本快照未覆盖完整执行链

`QualityGateMain.java:22-28` 只生成 Java source gate JSON 后按规则结果退出；`backend/build.gradle.kts:115-119` 将它挂入 `check`。本次快照没有纳入完整 executor/receipt 产物，因此无法评价其是否存在或是否正确；参考仓库的对应职责见 `docs/gates/gate-control-plane.md:18-21,138-165` 与 `scripts/gates/evidence/receipt_store.py:301-363`。

这只是报告限制，不形成本次架构修复项。若后续验收需要跨层 receipt，应由主会话按现有 Harness contract 单独提供证据；不应把它实现成 Java source scanner。

### 语义边界：Java 规则状态与 Harness 执行状态分层

`GateStatus.java:3-6` 只有 `PASS/FAIL`，`GateReport.java:20-27` 只按 violation 推导状态，`QualityGateMain.java:26-28` 对所有非 PASS 都抛异常。这是 Java rule 层的内部结果模型；参考 control plane 的 `PASS/BLOCKED/FAIL` 是执行与验收层模型（`docs/gates/gate-control-plane.md:145-154`），两者不应直接合并。

本次修复保留 Java 内部 `PASS/FAIL`，并在本文明确不能将其单独提升为 Harness `PASS` 或正式 catalog decision；运行时缺失、跳过与分层验收仍由对应 owner 的执行模型解释。

### 证据更正：lockfile 不在本次 53 文件快照范围内

`backend/build.gradle.kts:19-22` 与 `lexiflow.java-base.gradle.kts:29-32` 都设置 `LockMode.STRICT`。53 文件清单没有列出 lockfile，但这只说明它不在冻结输入；主会话已确认各 leaf 有 `gradle.lockfile` 且完整 `deliveryFull` 成功。参考仓库明确把 lockfile 路径作为 `java/build.gradle.kts:24-27` 的输入。

本次不提出创建或改动 lockfile；主会话应在其既有 delivery 验收中保留 lockfile 与成功命令证据。

### P2：跳过检查的 failure/error 诊断弱于参考

`VerifyNoSkippedTestsTask.kt:48-64` 统计 `tests` 和 `skipped`，并用 `TestAborted/<skipped>` 兜底；测试 task 本身失败时通常已由 Gradle `check` 阻断，但这个报告不会告诉审查者 failures/errors 总数，也没有把 XML 中的 `failures`/`errors` 写入摘要。参考构建 root 的对应动作（`java/build.gradle.kts:217-286`）同时统计 missing reports、failures、errors、skipped、aborted。

建议只增强 result 摘要和错误信息，保持 Gradle test 作为失败执法 owner；不要在 Python Gate 中重复解析 Java XML。

### P2：架构检查存在“允许为空”，需要避免空集假绿

原 `LayerArchitectureTest.java:10-35` 使用无限 `importPackages("io.lexiflow")` 与两处 `allowEmptyShould(true)`。这可能把 test/quality gate 类混入产品分析，也可能在产品类未导入时假绿；`VerifyProjectDependenciesTask.kt:61-68` 对未知 project role 会失败，这是较强的补充。

本次已将导入范围绑定到当前声明项目的生产 `src/main/java` 对应编译目录，去掉 `allowEmptyShould(true)`，要求全局产品类非空并确认 API/worker scaffold marker；domain 目前只有 package-info，domain-only rule 显式 scoped empty，报告中明确不把它写成已有 Domain 行为。每条边界都由 test-only fixture 验证正反例，未向生产范围注入反例。

### P2：快慢路、Rules/Models、Learning/Profile 与生命周期边界总体一致，但入口过多

`docs/architecture/decisions.md:82-118` 将 fast lane 的 normalization/candidate/rules/cache 与 semantic slow lane 的 durable handoff 分开；`decisions.md:148-170` 将 immutable Learning facts 与 Vocabulary Profile projection 分开；`phase-1-cross-cutting-contracts.md:19,44-51` 明确字幕不等待模型、PostgreSQL 是事实来源、Provider 输出不是真实业务事实。`phase-1-lifecycle-guarantees.md` 和 `docs/roadmap/phase-1-status.md:24` 又明确 cancellation/commit fencing、ACK/display、删除 barrier、claim/retry 等是设计提案或后续实现，不是当前产品行为。

这些内容没有发现把下一阶段行为误报为 Phase 1 已实现的硬矛盾；状态表和 lifecycle 文档反复声明 Proposed/Later，属于应保留的防误读护栏。可优化之处是把六份原始输出、十个 Proposed ADR、横切合同、生命周期保证和历史执行证据收敛到一个“当前可审查入口”，避免用户从 `README.md:5-12` 进入后必须跨多页才能知道哪些是设计、哪些是实现、哪些是历史。

### P2：隐私、删除和缓存语义有原则，但缺最小可验收清单

`phase-1-cross-cutting-contracts.md:44-51` 规定最小 context、禁止默认发送整段 transcript/Profile/行为历史；`decisions.md:236-259` 规定 PostgreSQL durable handoff、Redis 仅 cache/hot state；`phase-1-lifecycle-guarantees.md` 进一步提出取消 fencing、晚到结果不可发布和删除 barrier。`docs/roadmap/phase-1-status.md:22-24` 明确 exact schema、claim/retry、删除完成和生产 conformance 属于后续。

建议 Phase 1 只补一页 evidence matrix：每项原则对应 owner、静态证据、当前状态和“不能声称已实现”的措辞。不要提前设计删除 SQL、lease 数值、provider prompt 或缓存 schema。

### P3：实际 native/Qoder 身份和 300/600 秒规则写得清楚，但不应混入产品验收

`harness/agent-runtime.manifest.yaml:247-251` 固定首次 fallback 300 秒、后续间隔 600 秒；`docs/development/current-execution-evidence.md:3-21` 区分真实 runner actor/session、publisher、validation issuer 与 review issuer，并禁止用 ACK、exit zero 或历史 receipt 代替正式验收；`docs/development/phase-1-acceptance-order.md:5-13` 规定用户决定先于 G1 exit receipt。此处与参考仓库的 identity/receipt 方向一致，且 `phase-1-status.md:20,45-49` 已将历史统计和当前状态分开。

不要把这些 harness 规则实现为 Java Domain 或产品 worker 行为。需要改进的是入口可理解性：在当前状态页顶端增加“当前可用证据、历史证据、未运行检查、用户决定前置条件”四行索引即可。

## 不建议复制的参考能力

参考仓库的 Web quality rules、Playwright/Node 规则、Kotlin source 扫描、baseline update CLI、完整 Python Gate planner/executor、健康检查和大量 contract test 不属于当前 LexiFlow Phase 1 Java foundation。参考的 `QualityGateCli` selector/baseline 机制还会扩大配置面；在没有真实增量选择需求前，LexiFlow 固定三条 source gate 的单入口更容易维护。参考仓库的不可覆盖 receipt 和状态分类值得借鉴其契约，不值得复制其全部目录和历史流程。

## 当前证据局限与建议顺序

本审查没有运行构建，无法独立复核 Java 25、Gradle wrapper、lockfile、JaCoCo 或 ArchUnit 的运行结果；lockfile 与 `deliveryFull` 的成功来自主会话后续事实。架构测试源码已完成修复，但正式 PASS 仍由主会话执行单一验收命令确认。报告中的静态“覆盖”只表示源码和规则路径，不替代运行 receipt。

建议顺序为：由主会话运行架构测试确认 bounded import 与隔离反例，然后在后续独立任务中按需要审查完整 delivery receipt。Rules/Models、fast/slow、Learning/Profile、隐私最小化、可重建缓存、取消 fencing 与删除 barrier 保留为 Proposed/Later，待后续实现任务拥有自己的 schema、测试和 receipt 后再转为执行性结论。

## 本次架构执法修复

`LayerArchitectureTest` 现在先从当前已声明的两层项目目录中查找实际存在的 `src/main/java/*.java`，再要求对应 `build/classes/java/main` 目录存在且至少有一个 `.class`；它不把缺失或空目录先过滤掉。最终只从这些预期生产目录导入，并在静态初始化阶段拒绝空 class set 和缺少 `ApiApplication`/`WorkerApplication` marker。这样既排除 test/quality-gates 类，也把每个有产品 Java source 的项目作为实际导入前置条件。当前产品 `domainCount=0`（只有 `package-info.java`），所以 domain-only 规则显式记录为空并不执行；全局产品输入仍必须非空，不能因 domain 空而整体假绿。

规则覆盖 domain/application 对 `java.net`、`java.sql`、`javax.sql`、JPA、Servlet、PostgreSQL、Lettuce、Redis、Spring 及已知 provider 包的外部依赖，对 `platform`、`adapter(s)`、`provider` 和 api/worker composition root 的具体依赖，以及 domain 反向依赖 application；Spring 仍只允许进入 api、worker 和 platform。每条规则都有 test-only 正反例：外部库、adapter/composition-root、domain→application、Spring 边界分别验证拒绝与允许路径。fixture 不进入生产 class directories。

## 主会话运行纠正

实际完整构建发现 Spring fixture 的 test compile classpath 缺依赖，主会话已补齐当前 Boot BOM 管理的 spring-context 并更新唯一 architecture 测试锁。同时，package-info-only 项目不一定产生 class，不能把它当作空产品类型错误；输入要求改为真正的类型源码。Spring 许可限定真实 io.lexiflow.api / worker 组合根，增加同名 api 包反例与正例非空 selector。Java 自定义词法、PMD suppression、record 参数和 XML 检查修复与实际结果以 [统一审查页](phase-1-deterministic-tools-audit.md) 为准；此前静态修复 signal 不是编译或测试 PASS。
