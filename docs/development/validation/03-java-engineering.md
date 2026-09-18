# 步骤 3：检查 Java 工程

Java 的源码规则、架构与测试由 Gradle / Java 工具执行。本页提供两种使用方式：**一次完成工程检查，或逐项查看工具的原生效果**。所有命令从仓库根运行，不使用输出重定向；Gradle 工具仍按现有配置生成报告。

| 使用方式 | 入口 | 得到什么 |
|---|---|---|
| A：一次完成全部检查 | [聚合入口](#route-a)，选择检查或完整 deliveryFull | 工程检查结论；完整另含汇总覆盖报告和两个启动 jar |
| B：逐项在控制台查看 | [单项入口](#route-b)，按工具或模块选择一条命令 | 所选检查的结果，适合了解工具、观察效果或定位问题 |

两条路线任选其一；无需先发现失败才能使用 B。交付时采用 A，单项检查不能替代完整工程结论，也不在独立审查/目录中重复执行。

需要逐条确认“命令加载哪个脚本、执行哪个任务、代表什么”时，查看 [命令、Gradle 脚本与任务全表](java-gradle-task-reference.md)。它列出共有加载链、全部根定制入口、18 个模块适用的叶模块任务族、实际 Java 规则实现与报告。

<a id="route-a"></a>

## 1. 路线 A：一次完成全部检查

本页命令使用启动器默认的 `--console=colored`，保留 ANSI 颜色且不输出丰富的进度条。需要动态进度时显式追加 `--console=rich`；需要纯文本时追加 `--console=plain`，它会关闭颜色。`--console=auto` 恢复 Gradle 的终端自动判断；环境中非空 `NO_COLOR` 仍由 Gradle 禁止颜色。输出模式详见 [Gradle 官方说明](https://docs.gradle.org/current/userguide/command_line_interface.html#sec:command_line_logging)。

前提是 [步骤 1](01-scope-and-preparation.md) 的 Temurin 25 / Wrapper 检查通过。普通 Java 交付执行增量入口：

```bash
python3 -m scripts.toolchain.java_gradle check
```

需要完整交付、聚合覆盖报告和两个启动 jar 时，**改用**：

```bash
python3 -m scripts.toolchain.java_gradle clean deliveryFull
```

同一次冻结交付选择其中一个，不先检查再完整，也不并列补跑聚合包含的所有子任务。完整命令的 `clean` 会清除既有后端构建产物；需要历史证据时先归档到新的本地 tmp 目录。

想在一次运行中收集多个互不依赖的失败，可在所选命令末尾追加 `--continue`。依赖编译或其他前置任务失败的检查仍无法执行，应记为未完成，不能因命令跑到最后就称所有检查通过。

启动器自动为交付聚合加入 `--rerun-tasks --no-build-cache`。禁止 `-x`、`--tests`、`--dry-run` 等跳过或过滤方式；不能因输出构建 SUCCESSFUL 就忽略必需任务未运行。精确聚合关系看 [根构建](../../../backend/build.gradle.kts) 与 [Java 清单](../../../harness/java-product.manifest.yaml)。

要核对“工具实际配置了什么”，先看 [共享质量约定插件](../../../backend/gradle/build-logic/src/main/kotlin/lexiflow.java-quality.gradle.kts) 和 [版本目录](../../../backend/gradle/libs.versions.toml)。再按问题查看 [Checkstyle XML](../../../backend/gradle/config/checkstyle/checkstyle.xml)、[PMD XML](../../../backend/gradle/config/pmd/pmd.xml)、[Java gate 运行器](../../../backend/tests/quality-gates/src/main/java/io/lexiflow/quality/JavaSourceGateRunner.java) 或 [ArchUnit 测试集](../../../backend/tests/architecture/src/test/java/io/lexiflow/architecture/LayerArchitectureTest.java)。报告中的通过只覆盖这里实际启用的规则。

<a id="route-b"></a>

## 2. 路线 B：逐项在控制台查看

### B.1 按工具检查全工程

下表每一行都是独立选择，**不是要求从上到下全部运行**。复制所需的一条命令即可看控制台，不必重定向或打开报告才知道该任务是否成功。`--rerun-tasks --no-build-cache` 让所选任务及其前置任务实际执行，便于观察当前输入的效果。

| 想单独看什么 | 完整命令 | 控制台结果与范围 |
|---|---|---|
| 全部主代理 Java 编译 | `python3 -m scripts.toolchain.java_gradle compileJava --rerun-tasks --no-build-cache` | 各叶模块的主代理 Java 编译，包括产品与工程工具；错误/警告回到文件和行，Werror 生效 |
| 测试 Java 编译 | `python3 -m scripts.toolchain.java_gradle compileTestJava --rerun-tasks --no-build-cache` | 编译测试源码及其所需主代理依赖，不执行测试 |
| Spotless 格式/导入 | `python3 -m scripts.toolchain.java_gradle spotlessJavaCheck --rerun-tasks --no-build-cache` | 检查各叶模块生产源码/测试格式；失败时显示文件与差异 |
| Checkstyle 生产源码 | `python3 -m scripts.toolchain.java_gradle checkstyleMain --rerun-tasks --no-build-cache` | 检查主代理的命名/Javadoc 等启用规则；看违规和任务结果 |
| Checkstyle 测试源码 | `python3 -m scripts.toolchain.java_gradle checkstyleTest --rerun-tasks --no-build-cache` | 只选择测试的 Checkstyle；不执行 JUnit |
| PMD 生产源码 | `python3 -m scripts.toolchain.java_gradle pmdMain --rerun-tasks --no-build-cache` | 分析各叶模块主代理；看任务结果与提示的报告位置，规则明细可打印 XML（B.3） |
| PMD 测试源码 | `python3 -m scripts.toolchain.java_gradle pmdTest --rerun-tasks --no-build-cache` | 只选择测试的 PMD；不执行 JUnit |
| Javadoc / DocLint | `python3 -m scripts.toolchain.java_gradle javadoc --rerun-tasks --no-build-cache` | 生成并校验文档；错误、警告/Werror 直接显示 |
| Java 专属源码规则 | `python3 -m scripts.toolchain.java_gradle javaSourceGates --rerun-tasks --no-build-cache` | 中文注释、记录参数 Javadoc、PMD 抑制三条规则共用入口；详细结果见 B.3 |
| ArchUnit 架构规则 | `python3 -m scripts.toolchain.java_gradle architectureTest --rerun-tasks --no-build-cache` | 只运行架构测试集；控制台显示测试名称及 PASSED/跳过/FAILED |
| 产品只用 Java | `python3 -m scripts.toolchain.java_gradle verifyProductLanguage --rerun-tasks --no-build-cache` | 检查固定产品来源根；违规时显示非法语言源码路径 |
| 项目依赖方向与环 | `python3 -m scripts.toolchain.java_gradle verifyProjectDependencies --rerun-tasks --no-build-cache` | 检查 Gradle 项目依赖；失败时显示禁止方向/环，不证明私有表访问合规 |
| 全部 Java 测试及完整性 | `python3 -m scripts.toolchain.java_gradle verifyNoSkippedJavaTests --rerun-tasks --no-build-cache` | 先运行全部叶模块测试，再拒绝失败/错误/跳过/缺报告；摘要见 B.3 |
| 汇总覆盖报告 | `python3 -m scripts.toolchain.java_gradle jacocoRootReport --rerun-tasks --no-build-cache` | 包含全部测试与叶模块覆盖；控制台看报告任务是否完成，比例看 HTML，没有最低覆盖阈值 |
| 两个应用打包 | `python3 -m scripts.toolchain.java_gradle productBootJar --rerun-tasks --no-build-cache` | 编译并生成 API/工作进程启动 JAR 文件，不包含全部质量检查 |

`compileJava`、`spotlessJavaCheck`、`checkstyleMain` 等不带项目前缀的名字，由 Gradle 从后端根选择各子项目的同名任务；不是只检查一个根源目录。选定检查还会执行编译等必要前置任务，不能将“单项检查”理解为进程内只出现一条任务。选择语义见 [Gradle 多项目命令说明](https://docs.gradle.org/current/userguide/command_line_interface.html#sec:executing_tasks_in_multi_project_builds)。

`verifyNoSkippedJavaTests` 和 `jacocoRootReport` 都包含全部测试；选择它们时无需另跑一次全部测试。架构和源码规则的正反例属于已有测试集，修改规则时的追加验证见第 4 节。

<a id="b2-只检查一个-java-module"></a>

### B.2 只检查一个 Java 模块

要更集中地观察效果，用真实项目路径限定模块。下面分别检查 API 模块，选择想看的一条；换模块时使用 [完整清单](../../architecture/modules-and-dependencies.md#3-java-module-的完整划分)，如 `:modules:lexicon`、`:apps:worker`。

```bash
python3 -m scripts.toolchain.java_gradle :apps:api:spotlessJavaCheck --rerun-tasks --no-build-cache
```

```bash
python3 -m scripts.toolchain.java_gradle :apps:api:checkstyleMain --rerun-tasks --no-build-cache
```

```bash
python3 -m scripts.toolchain.java_gradle :apps:api:pmdMain --rerun-tasks --no-build-cache
```

```bash
python3 -m scripts.toolchain.java_gradle :apps:api:javadoc --rerun-tasks --no-build-cache
```

JUnit 也按模块独立运行，日志使用当前配置显示每项测试的结果。API / 工作进程目前是启动上下文骨架测试，不能据此证明业务旅程：

```bash
python3 -m scripts.toolchain.java_gradle :apps:api:test --rerun-tasks --no-build-cache
```

```bash
python3 -m scripts.toolchain.java_gradle :apps:worker:test --rerun-tasks --no-build-cache
```

当前 Java 专属源码 gate 是全仓三条规则共用的扫描器，**没有按模块或按单条规则过滤的公共 CLI**；上面的模块前缀用法不适用于它。源码 gate 的规则测试集可单独执行 `:tests:quality-gates:test`，但它验证规则实现，不替代当前源码扫描。

### B.3 在控制台展开详细结果

多数工具在控制台给任务成败和诊断，详细报告仍由原生工具生成。Java 来源检查当前只写 JSON，成功时不逐条打印规则；需要看每条规则的状态/违规明细，先运行 B.1 的 `javaSourceGates`，再直接打印已有报告：

```bash
python3 -m json.tool backend/tests/quality-gates/build/reports/java-source-gates/report.json
```

这是读取报告，不重新扫描源码、不产生另一份校验。顶层看 `status` / `sourceCount`，逐项看 `evaluations[].rule/status/violations`。若扫描失败，也可用同一命令显示本次写出的违规。

三个定制治理任务的简短摘要同样可以直接看；只读取刚才实际执行的对应任务的产物：

```bash
cat backend/build/reports/product-language/result.txt
```

```bash
cat backend/build/reports/project-dependencies/result.txt
```

```bash
cat backend/build/reports/verify-no-skipped-tests/result.txt
```

PMD 的规则明细未必直接出现在默认日志中。若单独执行的是 `:apps:api:pmdMain`，可以直接打印原生 XML；换模块 / 测试来源集合时改为相应目录或 `test.xml`：

```bash
cat backend/apps/api/build/reports/pmd/main.xml
```

### B.4 怎样判断单项结果

控制台最后的 `BUILD SUCCESSFUL` / `BUILD FAILED` 与退出码说明本条命令的结果；同时核对目标任务的结果。`NO-SOURCE` 表示没有适用源码，不能算未写业务已合规；`UP-TO-DATE` / `FROM-CACHE` 表示复用结果，不能说刚刚观察到实际执法。上面的强制执行参数不会让没有源码的任务产生源码。

一个模块的 Checkstyle 成功只证明该范围的已启用规则；单个测试成功也不包含根全部测试完整性。需要工程交付结论时再选择路线 A；同一输入的聚合已通过，就无需为同一个证明再跑表内各项。

输入或规则修复后重新冻结范围再验证。单项成功不能补写先前聚合的失败，也不能在审查/目录重跑这些命令制造第二份交付。

<a id="2-对照报告确认实际执行"></a>

## 3. 对照报告确认实际执行

`<leaf>` 表示实际 Gradle 叶子项目的目录，例如 `backend/apps/api`、`backend/tests/architecture`。没有源码的叶模块可以 NO-SOURCE；不得把空领域的检查当作业务完成。

| 检查 | 查看哪里 | 怎么判断 |
|---|---|---|
| 编译、Spotless | 当前命令日志里的编译 / spotless 任务 | 无编译警告/错误；格式任务通过。Spotless 没有这里承诺的独立 HTML 报告 |
| Checkstyle | `<leaf>/build/reports/checkstyle/main.html`、`main.xml`；有测试源码时看测试报告 | 零错误/warnings；当前日志任务成功 |
| PMD | `<leaf>/build/reports/pmd/main.html`、`main.xml`；测试同理 | 启用规则无违规；当前任务成功，不把未启用规则算入证明 |
| Java 专属源码 gate | `backend/tests/quality-gates/build/reports/java-source-gates/report.json` | 顶层 `status` / `sourceCount`、`evaluations[].rule/status/violations`；失败项回到源码行 |
| 语言和项目依赖 | `backend/build/reports/product-language/result.txt`、`project-dependencies/result.txt` | PASS；对应当前命令，无旧结果冒充 |
| ArchUnit | `backend/tests/architecture/build/reports/tests/test/index.html` 及 `test-results/test/TEST-*.xml` | 所需规则正反例成功；真实产品导入范围明确 |
| API / 工作进程与工具测试 | `<leaf>/build/reports/tests/test/index.html` 与 `test-results/test/TEST-*.xml` | 测试、失败、错误、跳过与当前运行一致；失败/错误/跳过均不可通过 |
| 全部 Java 测试完整性 | `backend/build/reports/verify-no-skipped-tests/result.txt` | `PASS tests=... skipped=0 failures=0 errors=0`；有测试源码却缺结果应拒绝 |
| Javadoc / DocLint | 当前 javadoc 任务日志与 `<leaf>/build/docs/javadoc/index.html` | 无 DocLint 错误；HTML 存在本身不证明当前任务通过 |
| 覆盖报告 | `<leaf>/build/reports/jacoco/test/html/index.html`；完整另看 `backend/build/reports/jacoco/jacocoRootReport/html/index.html` | 报告属于当前输入；当前没有最低覆盖率拒绝阈值，不自行设一个数字当 PASS 标准 |
| 完整的打包结果 | `backend/apps/api/build/libs/`、`backend/apps/worker/build/libs/` | 两个 bootJar 任务成功，产物来自本次完整；版本化 jar 文件名以实际输出为准 |

如果报告没有生成或当前日志任务失败，先按 [故障定位](troubleshooting.md) 判断是否预期 NO-SOURCE。历史报告只用于比较，不能证明新输入。

## 4. 质量工具配置变化时检查执法能力

只在规则实现、扫描范围、豁免或配置变化时补正反例。源码门禁与架构工具已有可直接运行的 Java 测试：

```bash
python3 -m scripts.toolchain.java_gradle :tests:quality-gates:test --rerun-tasks --no-build-cache
python3 -m scripts.toolchain.java_gradle :tests:architecture:test --rerun-tasks --no-build-cache
```

在修复阶段选择适用测试集；冻结交付时由聚合包含它们，不额外再跑。用 [源码测试样例](../../../backend/tests/quality-gates/src/test/resources/fixtures) 与 [架构测试样例](../../../backend/tests/architecture/src/test/java/io/lexiflow/architecture/fixtures) 核对非法输入拒绝、合法输入通过。

Spotless / PMD / Checkstyle / DocLint / 锁的真实工具反例需要在一次性隔离副本准备，执行对应真实工具，记录预期拒绝与恢复结果。没有通用探针 CLI；未准备当前反例就记未完成，不把普通构建 PASS 当作执法证明。

格式自动修复会修改源码；需要时显式运行 `python3 -m scripts.toolchain.java_gradle spotlessApply`，然后审阅差异。它是修复动作，不是路线 B 的只读格式检查。

## 判断与下一步

所选聚合实际完成、必需报告齐全、失败/错误/跳过均为零，直接工程验证才可记 `PASS`。错误输入必须被对应工具拒绝，反例的非零退出是预期拒绝证据，不能写成正常构建 PASS。

当前真实产品只有基础骨架，空领域、工具测试样例、覆盖率不能证明提示质量或用户数据删除。正式 Java 任务验收还需统一 Gate 的 Gradle 适配器与冻结报告绑定；当前尚未接入，按 [步骤 5](05-receipts-and-acceptance.md) 保留该阻塞，不伪装正式收据。
