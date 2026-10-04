# 1. Java 检查：原生交付与定向诊断

> 位置：[工程地图](../overview.md) → [Reference](../reference.md) → Java 检查。由 [Verify](../change-delivery/verification.md) 调用时使用声明的完整入口；以下精确任务用于理解或诊断。

## 1.1. 公开入口

所有 Java 命令从仓库根通过启动器进入：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend <gradle-task> [gradle-options]
```

启动器选择仓库规定的 Java 与 Wrapper；不要直接以全局 `gradle` 或系统 Java 替代。根聚合、版本与项目依赖见 `backend/build.gradle.kts`、`backend/settings.gradle.kts` 和 `harness/java-product.manifest.yaml`。

## 1.2. 常用任务

| 目的 | 任务 |
| --- | --- |
| 普通交付 | `check` |
| 完整交付与 JAR | `clean deliveryFull` |
| 格式 | `spotlessJavaCheck` |
| 风格/静态分析 | `checkstyleMain`、`pmdMain` |
| 架构 | `architectureTest` |
| Java 专属规则 | `javaSourceGates` |
| 测试完整性 | `verifyNoSkippedJavaTests` |
| 按需覆盖报告 | `jacocoRootReport`（XML），加 `-PqualityHtmlReports=true` 生成 HTML |
| 产品语言/项目依赖 | `verifyProductLanguage`、`verifyProjectDependencies` |

定向到模块时使用真实 Gradle project path，例如 `:api:test`。除失败定位外，按交付目标选择完整聚合入口；不要再逐个运行已包含的任务。Repository Verify 的 backend Check 使用 deliveryFull，不能用普通 check 替代。

## 1.3. 报告与边界

工具原生报告在相应模块的 `build/reports/`，定制治理结果在 `backend/build/reports/`。目标任务的成功和实际报告内容共同构成该工具的结论；报告不存在、任务跳过或无源码时，不能表述为已验证源码。Java 工具不签发 Gate 收据。

普通 `check` 和 `qualityFull` 执行质量判定，不自动生成 JaCoCo 覆盖率汇总；覆盖率采集和显式报告任务保留。当前没有覆盖率阈值门禁，不能把报告生成成功解释为覆盖率合格。测试完整性仍消费 JUnit XML，静态检查、业务测试和文档完整性要求不变。

测试、Checkstyle、PMD 的 HTML 默认关闭，XML 结果保留；JaCoCo 显式任务默认输出 XML。需要人工浏览时使用例如：

```bash
python3 -m scripts.environment.java_exec backend/gradlew -p backend -PqualityHtmlReports=true jacocoRootReport
```

该参数只控制浏览产物，不改变 Check 的成功标准。Javadoc 仍由普通 `check` 执行，因为它承载 DocLint/Werror 判定；其网站型输出随工具产生，不将其与无判定消费者的覆盖报告一起删除。旧报告文件可能仍留在 build 目录，必须核对本次命令与产物，不凭文件存在推断本次已经执行。

## 1.4. 从失败回到交付

保留完整 Gradle 输出后，可用 `:api:checkstyleMain`、`:api:test`、`architectureTest` 等真实任务定向定位。启动器会强制执行并禁用构建缓存；不得用 `-x`、`--tests` 或 `--dry-run` 跳过交付要求。修复后重新运行对应完整检查；单项成功不能覆盖原失败记录。

`NO-SOURCE`、`UP-TO-DATE`、`FROM-CACHE` 不是本次对全部源码执行检查的证据。缺隔离 PG/Redis 环境时先去[环境准备](../operations/verification-environment.md)，不把开发库地址填作测试地址。
