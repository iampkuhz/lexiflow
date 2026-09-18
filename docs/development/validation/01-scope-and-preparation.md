# 步骤 1：先确认校验范围

这一步确定当前输入与本次要证明的结论，避免拿旧报告证明新改动，或为了文档变化重复构建整个产品。

## 1. 定位仓库与版本

将示例路径替换为实际仓库根目录，再在终端执行：

```bash
cd /path/to/lexiflow
git rev-parse --show-toplevel
git rev-parse HEAD
git status --short
git diff --name-only
git diff --cached --name-only
```

查看输出：根目录应是 LexiFlow；记下 HEAD、未提交文件及已暂存文件。`git status` 没有输出只说明工作区干净，不说明测试通过。已有他人改动时记录归属，不回滚、覆盖或自动暂存。

## 2. 按文件选择路线

| 改动范围 | 本次应做的检查 | 不应由它推断的结论 |
|---|---|---|
| `docs/`、根 README | 架构/图文/链接人工审查；适用任务的文档合同诊断；差异空白字符 | Java 行为通过、G1 通过 |
| `backend/` 产品源码 | Java 聚合交付；失败时定向诊断 | 正式任务验收已签发 |
| Gradle 配置、质量工具实现或依赖锁 | Java 聚合交付；按改动补对应工具正反例 | 未启用规则也能执法、双环境复现完成 |
| `scripts/`、`harness/`、`planning/` | 对应治理配置和 Python 工具测试 | Java 源码规则或产品旅程已验证 |
| 既有收据 / 证据包的输入变化 | 只读重验相关哈希 DAG，重新准备真实当前输入验收 | 改写旧哈希就能恢复 PASS |

跨范围改动先标出所有负责人和受影响任务，遵守现有 OpenSpec / 交接契约。规则入口是 [AGENTS.md](../../../AGENTS.md)，当前任务与版本看 [目录](../../../planning/workstreams.yaml)。

## 3. 先排除入口错误

需要 Python 工具时运行：

```bash
python3 --version
python3 scripts/gates/cli.py --help
```

需要 Java 工程检查时，再运行：

```bash
python3 -m scripts.toolchain.java_gradle --version
```

后一个输出应显示仓库 Wrapper 的 Gradle 9.7.1 和 Temurin / Eclipse Adoptium Java 25。启动器会拒绝其他主版本或发行商；系统 `java -version` 不能证明实际构建用的是 Java 25。精确选取规则见 [启动器](../../../scripts/toolchain/java_gradle.py)，版本真源见 [Java 清单](../../../harness/java-product.manifest.yaml)。

缺少 JDK 时设置 `LEXIFLOW_JAVA_HOME` 为已安装的 Temurin 25 **JDK home**，或准备仓库 `.local/toolchains/jdk-25/`，然后重复版本检查。不要用 JDK 26 顶替，不在这一步重新生成 Wrapper 或更新锁。

## 判断与下一步

能识别当前版本、改动归属、校验目的，且所需入口可用，范围准备才算 `PASS`。前提缺失记 `BLOCKED`；入口拒绝、版本错误等实际检查记 `FAIL`，保留原始原因。

选定 [架构](02-architecture-and-diagrams.md)、[Java](03-java-engineering.md) 或 [Harness](04-harness-and-dispatch.md) 路线。在任何正式验收前，准备 [步骤 5](05-receipts-and-acceptance.md) 所需真实上下文。
