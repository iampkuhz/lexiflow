# 1. Java/Gradle 任务参考

## 1.1. 公开入口

所有 Java 命令从仓库根通过启动器进入：

```bash
python3 -m scripts.toolchain.java_gradle <gradle-task> [gradle-options]
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
| 覆盖报告 | `jacocoRootReport` |
| 产品语言/项目依赖 | `verifyProductLanguage`、`verifyProjectDependencies` |

定向到模块时使用真实 Gradle project path，例如 `:apps:api:test`。除失败定位外，优先父页规定的聚合交付入口。

## 1.3. 报告与边界

工具原生报告在相应模块的 `build/reports/`，定制治理结果在 `backend/build/reports/`。目标任务的成功和实际报告内容共同构成该工具的结论；报告不存在、任务跳过或无源码时，不能表述为已验证源码。Java 工具不签发 Gate 收据。
