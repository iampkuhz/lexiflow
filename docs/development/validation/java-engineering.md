# 1. 校验：Java 工程

## 1.1. 交付检查

普通 Java 交付只运行一次聚合入口：

```bash
python3 -m scripts.toolchain.java_gradle check
```

需要完整交付、聚合覆盖报告和应用 JAR 时运行：

```bash
python3 -m scripts.toolchain.java_gradle clean deliveryFull
```

同一冻结交付选择与目标相符的一条；不要先跑较小检查再补跑已被完整交付包含的任务。启动器会强制执行并禁用构建缓存；禁止用 `-x`、`--tests` 或 `--dry-run` 跳过要求。

## 1.2. 定向诊断

失败定位时可运行一个精确 Gradle 任务，例如：

```bash
python3 -m scripts.toolchain.java_gradle :apps:api:checkstyleMain --rerun-tasks --no-build-cache
python3 -m scripts.toolchain.java_gradle :apps:api:test --rerun-tasks --no-build-cache
python3 -m scripts.toolchain.java_gradle architectureTest --rerun-tasks --no-build-cache
```

定向命令只帮助定位，不能替代交付结论。完整任务、实现脚本和报告位置见[任务参考](java-gradle-task-reference.md)。

## 1.3. 读取结果

`BUILD SUCCESSFUL` 只说明所选任务成功；`NO-SOURCE`、`UP-TO-DATE`、`FROM-CACHE` 不等同于本次对源码的完整执法。报告、失败诊断和规则变更时的正反例应与本次命令对应，不把单个工具结果扩大为产品或正式验收通过。
