<a id="java-校验命令gradle-脚本与-task-全表"></a>

# Java 校验命令、Gradle 脚本与任务全表

这里回答三个问题：**命令首先加载哪些脚本，选择的任务实际做什么，以及从哪里读取结果**。这是 [Java 操作手册](03-java-engineering.md) 的详细参考；一次交付使用 [聚合路线 A](03-java-engineering.md#route-a)，主动观察工具或定位问题使用 [单项控制台路线 B](03-java-engineering.md#route-b)，不照着全表逐条执行。

本表覆盖仓库的全部自定义聚合/治理任务、Java 质量与交付任务族，以及手册使用的诊断命令。Gradle/插件的内部辅助任务以 `tasks --all` 实际输出为准，不把每个插件的内部任务都作为另一份验收。

## 1. 所有命令共同经历的脚本加载链

在仓库根使用 `python3 -m scripts.toolchain.java_gradle <task> <options>`。以下脚本会参与构建配置；它们不是每条命令独占的一段 shell 脚本，真正执行的是选中任务及其依赖图。

| 顺序 | 实际文件 | 负责什么 |
|---|---|---|
| 入口 | [java_gradle.py](../../../scripts/toolchain/java_gradle.py) | 选择 Temurin 25，拒绝改构建根/init 脚本；交付强制实际执行参数；默认带颜色的，显式控制台选择原样传递；不扫描 Java 规则 |
| Wrapper | [gradlew](../../../backend/gradlew)、[wrapper 属性](../../../backend/gradle/wrapper/gradle-wrapper.properties) | 使用固定 Gradle 发行包 / 校验和，启动后端构建 |
| 项目注册 | [settings.gradle.kts](../../../backend/settings.gradle.kts) | 注册 18 个 Java 叶模块项目、仓库及包含的构建 |
| 工程默认 | [gradle.属性](../../../backend/gradle.properties) | 配置缓存、串行项目执行、警告 fail、JVM 参数；交付缓存策略由启动器覆盖 |
| 工具构建 | [build-logic/settings.gradle.kts](../../../backend/gradle/build-logic/settings.gradle.kts)、[build-logic/build.gradle.kts](../../../backend/gradle/build-logic/build.gradle.kts) | 编译 Kotlin DSL 约定插件插件 / 自定义任务类型；加载 Spotless 插件依赖 |
| 根装配 | [backend/build.gradle.kts](../../../backend/build.gradle.kts) | 给 18 个叶模块应用 `lexiflow.java-library`，配置项目依赖与根聚合 |
| 组合约定插件 | [lexiflow.java-library.gradle.kts](../../../backend/gradle/build-logic/src/main/kotlin/lexiflow.java-library.gradle.kts) | 应用下面两份约定插件 |
| Java 基础 | [lexiflow.java-base.gradle.kts](../../../backend/gradle/build-logic/src/main/kotlin/lexiflow.java-base.gradle.kts) | Java 库、工具链/发布 25、UTF-8、静态检查 Werror、严格锁、可复现归档 |
| Java 质量 | [lexiflow.java-quality.gradle.kts](../../../backend/gradle/build-logic/src/main/kotlin/lexiflow.java-quality.gradle.kts) | Spotless、Checkstyle、PMD、JUnit、DocLint、JaCoCo 与叶模块检查的接线 |
| 唯一专用叶模块脚本 | [tests/quality-gates/build.gradle.kts](../../../backend/tests/quality-gates/build.gradle.kts) | 注册 `runJavaSourceGates`，传入固定来源根与 JSON 报告路径 |

大部分叶模块 **没有自己的 build.gradle.kts**：它们由根构建和共享约定插件配置。不能去 `backend/modules/lexicon` 找一份并不存在的 PMD 脚本。依赖与插件版本登记在 [libs.versions.toml](../../../backend/gradle/libs.versions.toml)；Checkstyle、PMD、google-java-format 与 JaCoCo 实际采用的版本还要核对共享质量约定插件中的 toolVersion / 配置值。手册不另维护一组版本。

<a id="2-root-聚合与定制任务命令全表"></a>

## 2. 根聚合与定制任务：命令全表

下表命令全部从仓库根运行，表中的“后续任务”是依赖关系，**不保证终端输出的先后顺序**。同一 Gradle 图中共享的任务去重；多个根别名指向同一测试，不代表同一次构建把测试重复执行。

| 完整命令 | 任务定义 / 接线脚本 | 实际执行与结论 | 主要产物 |
|---|---|---|---|
| `python3 -m scripts.toolchain.java_gradle check --console=plain` | 根构建的 `check`；每叶模块的共享质量约定插件 / 插件生命周期 | 18 叶模块检查，加语言、项目依赖、ArchUnit、Java 来源检查、测试完整性；普通 Java 交付 | 叶模块质量/测试报告、三类根 result.txt |
| `python3 -m scripts.toolchain.java_gradle clean deliveryFull --console=plain` | 根构建的 `deliveryFull` | 先清理旧输出；依赖 qualityFull 与 productBootJar；完整交付入口 | 上述报告、根覆盖率、API/工作进程启动 JAR 文件 |
| `python3 -m scripts.toolchain.java_gradle qualityFull --console=plain` | 根构建的 `qualityFull` | 检查 + jacocoRootReport；质量全量但不单独保证两个 bootJar | 检查报告、根 JaCoCo XML/HTML |
| `python3 -m scripts.toolchain.java_gradle architectureTest --console=plain` | 根构建的 `architectureTest` | 别名到 `:tests:architecture:test`；类依赖与编译输入诊断 | 架构的 JUnit XML/HTML |
| `python3 -m scripts.toolchain.java_gradle javaSourceGates --console=plain` | 根构建的 `javaSourceGates` → quality-gates 叶模块脚本 | 别名到 `:tests:quality-gates:runJavaSourceGates`；执行真实源码扫描负责人 | Java 来源检查 JSON |
| `python3 -m scripts.toolchain.java_gradle verifyProductLanguage --console=plain` | 根构建注册 `VerifyProductLanguageTask` | 拒绝产品来源根的 Python/Kotlin/Scala/Groovy 源码 | `backend/build/reports/product-language/result.txt` |
| `python3 -m scripts.toolchain.java_gradle verifyProjectDependencies --console=plain` | 根构建注册 `VerifyProjectDependenciesTask` | 校验直接项目依赖的角色方向、目标存在及无环；不扫描 DB 表访问 | `backend/build/reports/project-dependencies/result.txt` |
| `python3 -m scripts.toolchain.java_gradle verifyNoSkippedJavaTests --console=plain` | 根构建注册 `VerifyNoSkippedTestsTask` | 依赖所有叶模块测试，安全解析 XML，拒绝失败、错误、跳过、缺报告与不完整计数 | `backend/build/reports/verify-no-skipped-tests/result.txt` |
| `python3 -m scripts.toolchain.java_gradle jacocoRootReport --console=plain` | 根构建的 `JacocoReport` | 依赖各叶模块 jacocoTestReport，汇总当前执行数据；报告任务，不设最低覆盖阈值 | `backend/build/reports/jacoco/jacocoRootReport/` |
| `python3 -m scripts.toolchain.java_gradle productBootJar --console=plain` | 根构建的 `productBootJar` | 别名到 `:apps:api:bootJar` 与 `:apps:worker:bootJar`；打包不替代检查 | 两个 apps 的 build/libs |
| `python3 -m scripts.toolchain.java_gradle spotlessApply --console=plain` | 根构建的 `spotlessApply` → 每叶模块 Spotless | 修改全部叶模块 Java 格式/导入；是修复，不是只读校验 | 源码差异，需人工审阅 |
| `python3 -m scripts.toolchain.java_gradle clean --console=plain` | Gradle 基准/java 插件注册；根目录任务选择器 | 删除根/对应子项目构建输出；清理不是验证 | 旧构建产物被清理 |

单独运行 qualityFull 也被启动器视为交付聚合并禁止排除/过滤。单独诊断任务不产生正式验证收据。聚合包含的检查不在审查/目录再运行。

<a id="自定义-task-类型最终落在哪个实现"></a>

### 自定义任务类型最终落在哪个实现

| 任务类型 | Kotlin 实现文件 | 检查范围与失败含义 |
|---|---|---|
| `VerifyProductLanguageTask` | [VerifyProductLanguageTask.kt](../../../backend/gradle/build-logic/src/main/kotlin/io/lexiflow/buildlogic/VerifyProductLanguageTask.kt) | 固定产品目录；非法扩展名拒绝，大小写规范化；不实现风格规则 |
| `VerifyProjectDependenciesTask` | [VerifyProjectDependenciesTask.kt](../../../backend/gradle/build-logic/src/main/kotlin/io/lexiflow/buildlogic/VerifyProjectDependenciesTask.kt) | 模块/应用/平台/应用/测试角色与 DFS 环检查；每领域允许列表仍要合同审阅 |
| `VerifyNoSkippedTestsTask` | [VerifyNoSkippedTestsTask.kt](../../../backend/gradle/build-logic/src/main/kotlin/io/lexiflow/buildlogic/VerifyNoSkippedTestsTask.kt) | JUnit XML tests/testcase、failure/error/skipped、报告存在性；失败移除旧 PASS 摘要 |
| `GenerateJavadocAnchorTask` | [GenerateJavadocAnchorTask.kt](../../../backend/gradle/build-logic/src/main/kotlin/io/lexiflow/buildlogic/GenerateJavadocAnchorTask.kt) | 生成仅给 Javadoc 的文档锚点，不编译进产品类路径；本身不证明文档合规 |

<a id="3-leaf-的-java-质量--交付-task-族"></a>

## 3. 叶模块的 Java 质量 / 交付任务族

用 [完整模块清单](../../architecture/modules-and-dependencies.md#3-java-module-的完整划分) 选择真实项目路径。例如：

```bash
JAVA_PROJECT=':apps:api'
python3 -m scripts.toolchain.java_gradle "${JAVA_PROJECT}:checkstyleMain" --console=plain
```

下表的 `P` 表示该实际项目路径，不是可直接粘贴的 Gradle 项目。每项完整命令格式是 `python3 -m scripts.toolchain.java_gradle P:<task> --console=plain`。`P` 对应的目录简称为 `<leaf>`；根配置、基准 / 质量约定插件都按第 1 节加载。

| 任务名（P 前缀） | 注册工具 / 实际配置脚本 | 执行什么 | 产物或失败定位 |
|---|---|---|---|
| `check` | Java/checkstyle/pmd 插件生命周期 + 共享质量约定插件 | 本叶模块的质量与测试；**不包含根全仓治理保证** | 下列适用报告 |
| `compileJava` | Java 库插件；基准约定插件编译参数 | 主代理 Java 编译，发布 25，静态检查 all Werror | `<leaf>/build/classes/java/main`、编译日志 |
| `processResources`、`classes` | Java 插件生命周期 | 主代理资源复制 / 编译聚合；不是额外风格检查 | build/resources/main、类输出 |
| `compileTestJava` | Java 插件；基准约定插件 | 测试 Java 编译，使用该叶模块的测试依赖 | build/classes/java/test、编译日志 |
| `processTestResources`、`testClasses` | Java 插件生命周期 | 测试资源 / 编译聚合 | build/resources/test、测试类输出 |
| `spotlessCheck` | Spotless 插件；质量约定插件的 `spotless.java` | 聚合 Java 格式检查 | 任务日志与指出的源码 |
| `spotlessJavaCheck` | 同上；google-java-format / removeUnusedImports / 空白字符 | 实际 Java 格式与导入校验，扫描生产源码/测试来源集合 | 任务日志，不承诺 HTML |
| `spotlessApply`、`spotlessJavaApply` | 同上 | 修复格式，修改源码；不签 PASS | 源码差异 |
| `checkstyleMain`、`checkstyleTest` | Checkstyle 插件；质量约定插件 + [checkstyle.xml](../../../backend/gradle/config/checkstyle/checkstyle.xml) | 类型/方法 Javadoc、命名等启用规则，零 warnings/错误 | build/reports/checkstyle/main或test.xml / html |
| `pmdMain`、`pmdTest` | PMD 插件；质量约定插件 + [pmd.xml](../../../backend/gradle/config/pmd/pmd.xml) | 执行已启用的高信号规则白名单，生产源码/测试静态分析 | build/reports/pmd/main或test.xml / html |
| `generateJavadocAnchor` | 质量约定插件注册 GenerateJavadocAnchorTask | package-info-only 模块的文档输入锚点 | build/generated/sources/javadoc/JavadocAnchor.java |
| `javadoc` | Java 插件；质量约定插件的 Javadoc configureEach | 私有可见性，DocLint all,-missing，Werror；文档缺失由其他负责人执法 | build/docs/javadoc、当前错误日志 |
| `test` | Java 测试 / JUnit 平台；质量约定插件 + 根依赖 | JUnit/Spring/ArchUnit 或 gate 规则测试，取决于叶模块测试源码 | build/test-results/test/TEST-*.xml、build/reports/tests/test/index.html |
| `jacocoTestReport` | JaCoCo 插件；质量约定插件 | 依赖测试，生成该叶模块 XML/HTML 覆盖报告 | build/reports/jacoco/test/ |
| `jacocoTestCoverageVerification` | JaCoCo 插件默认任务 | **当前未配置 violationRules，未接入检查的最低覆盖阈值**；不能用名字推断强制执行生效 | 当前不作为交付通过条件 |
| `jar`、`sourcesJar` | Java 库插件；基准约定插件归档设置 | 编译产物 / 来源归档；固定顺序、去时间戳 | build/libs；不替代质量或产品测试 |
| `bootJar`（仅两个 apps） | Spring Boot插件；根配置(appProjectPaths) | 打包可启动应用与运行依赖 | apps 对应 build/libs |
| `runJavaSourceGates`（仅测试:quality-gates） | 叶模块 build.gradle.kts 注册 JavaExec | 执行 QualityGateMain，写 JSON，再按实际状态失败或成功 | 见第 4 节 |

PMD、Checkstyle 对没有源码的来源集合可 NO-SOURCE；这不是未实现业务的 PASS。测试有源码却无结果、跳过/failed 都必须由根完整性负责人拒绝。叶模块 `check` 不替代根 `check`。

`bootRun` / `bootBuildImage` 是 Spring Boot的运行/镜像工具，不属于本手册校验入口；本轮没有运行它们。插件内部生成/缓存等辅助任务可查看全量任务输出，不另外纳入一组重复检查。

## 4. 专项诊断命令实际执行哪个测试 / 扫描器

| 完整命令 | 实际 Gradle 接线 | 最后执行的源码 | 结论范围 |
|---|---|---|---|
| `python3 -m scripts.toolchain.java_gradle :apps:api:test --rerun-tasks --no-build-cache --console=plain` | 根 apps 依赖 + 共享质量测试配置 | [ApiApplicationTest.java](../../../backend/apps/api/src/test/java/io/lexiflow/api/ApiApplicationTest.java) | API 启动上下文骨架，不是业务 API 旅程 |
| `python3 -m scripts.toolchain.java_gradle :apps:worker:test --rerun-tasks --no-build-cache --console=plain` | 同上，工作进程叶模块 | [WorkerApplicationTest.java](../../../backend/apps/worker/src/test/java/io/lexiflow/worker/WorkerApplicationTest.java) | 工作进程启动上下文骨架，不是恢复/删除实测 |
| `python3 -m scripts.toolchain.java_gradle :tests:architecture:test --rerun-tasks --no-build-cache --console=plain` | 根架构依赖 / systemProperty，共享测试 | [LayerArchitectureTest.java](../../../backend/tests/architecture/src/test/java/io/lexiflow/architecture/LayerArchitectureTest.java) | 产品主代理导入范围、类型方向与隔离规则正反例 |
| `python3 -m scripts.toolchain.java_gradle :tests:quality-gates:test --rerun-tasks --no-build-cache --console=plain` | 根 JUnit 依赖 + 共享测试 | [质量测试工作包](../../../backend/tests/quality-gates/src/test/java/io/lexiflow/quality) | 自定义规则和词法/AST/扫描范围测试，不代替扫描当前实际源码 |
| `python3 -m scripts.toolchain.java_gradle :tests:quality-gates:runJavaSourceGates --console=plain` | quality-gates 叶模块 JavaExec → 类 | [QualityGateMain.java](../../../backend/tests/quality-gates/src/main/java/io/lexiflow/quality/QualityGateMain.java) → [JavaSourceGateRunner.java](../../../backend/tests/quality-gates/src/main/java/io/lexiflow/quality/JavaSourceGateRunner.java) | 扫描当前生产源码/测试 Java，执行三条源码 gate |

最后一条扫描先经 JavaSourceDiscovery / ParsedJavaSources，共享固定输入与编译器解析，再运行：

| Java 规则实现 | 负责什么 | 如何看失败 |
|---|---|---|
| [JavaCommentLanguageGate.java](../../../backend/tests/quality-gates/src/main/java/io/lexiflow/quality/JavaCommentLanguageGate.java) | 中文业务注释与明确工具/版权例外 | JSON 的规则 / violations.路径、系列、代码、消息 |
| [RecordComponentJavadocGate.java](../../../backend/tests/quality-gates/src/main/java/io/lexiflow/quality/RecordComponentJavadocGate.java) | 生产记录组件参数 Javadoc | 同上；不在 Python 重新扫描 |
| [NoPmdSuppressionsGate.java](../../../backend/tests/quality-gates/src/main/java/io/lexiflow/quality/NoPmdSuppressionsGate.java) | 生产源码/测试 PMD 抑制与真实行注释 NOPMD | 同上；字符串中的术语不等于注释抑制 |

JSON 在 `backend/tests/quality-gates/build/reports/java-source-gates/report.json`：看 `status`、`sourceCount`、`evaluations[].rule/status/violations`。规则测试集成功与实际源码扫描成功是两种证据，不混用。

## 5. 查看注册清单与依赖的命令

这些是只读诊断，不执行交付检查，不生成正式收据：

| 完整命令 | 接线 / 含义 | 看哪里 |
|---|---|---|
| `python3 -m scripts.toolchain.java_gradle --version` | Wrapper 运行时 | Gradle 与实际 Java 25 版本 |
| `python3 -m scripts.toolchain.java_gradle projects --console=plain` | Gradle 项目诊断 / 设置注册 | 完整项目树，包含容器项目；18 是 Java 叶模块数量 |
| `python3 -m scripts.toolchain.java_gradle tasks --all --console=plain` | Gradle 任务报告 | 根、所有叶模块与插件当前任务注册 |
| `python3 -m scripts.toolchain.java_gradle dependencies --console=plain` | 根 `DependencyReportTask` 接线到所有叶模块的依赖 | 依赖报告；严格锁定约束解析，不自动更新锁 |
| `python3 -m scripts.toolchain.java_gradle :apps:api:dependencies --configuration compileClasspath --console=plain` | API Java 编译类路径 | 直接/传递项目与外部依赖，图中的当前 API 装配 |
| `python3 -m scripts.toolchain.java_gradle :apps:worker:dependencies --configuration compileClasspath --console=plain` | 工作进程 Java 编译类路径 | 同上；不是 API / 工作进程已跑通业务 |
| `python3 -m scripts.toolchain.java_gradle :apps:api:help --task checkstyleMain --console=plain` | Gradle 帮助任务诊断 | 任务路径、类型、说明与选项；具体来源策略回对应约定插件 / XML |

需要保留完整注册清单时将 `tasks --all` 输出保存到新的本地 tmp 记录。它会显示插件辅助任务，与本页的仓库检查选择分开看。不存在的任务记实际失败；未来 OPS 计划中的 `resolveAndLockAllProjects` / `verifyDependencyLocks` 目前不是可直接运行的公共入口。

## 6. 怎么读通过结果

回到 [操作手册](03-java-engineering.md#2-对照报告确认实际执行) 核对本次日志与报告。根检查、qualityFull、deliveryFull 的保证不同；打包、诊断、自动格式修复、规则测试和正式收据也各有范围。

Java 源码检查只由这里的 Gradle/Java 负责人执行。Python 启动器选运行时，Python Gate 消费治理合同/证据，审查/目录不重跑交付。当前 Gradle 适配器尚未接入统一 Gate，所有直接构建报告都不能冒充正式 Java 任务收据。
