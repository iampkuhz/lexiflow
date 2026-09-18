# 1. 校验：范围与准备

## 1.1. 固定输入

在仓库根记录 `HEAD`、未提交差异、已暂存差异和本次负责范围；发现他人修改时只标注归属，不回滚、覆盖或暂存。`git status` 为空只说明工作区干净，不说明校验通过。

```bash
git rev-parse --show-toplevel
git rev-parse HEAD
git status --short
git diff --name-only
git diff --cached --name-only
```

## 1.2. 选择检查

| 改动 | 必需证明 |
| --- | --- |
| `docs/` | 链接、图源与受影响语义 |
| `backend/` | Java 交付检查和产品测试 |
| Gradle/质量工具/锁 | Java 交付检查及对应正反例 |
| `scripts/`、`harness/`、`planning/` | 对应 Python 测试和治理检查 |
| 收据输入 | 新鲜证据或只读哈希复核 |

跨范围改动组合相应检查，不能以其中一项代替另一项。

## 1.3. 入口失败

```bash
python3 --version
python3 scripts/gates/cli.py --help
python3 -m scripts.toolchain.java_gradle --version
```

Java 启动器必须实际选中 Temurin 25 与仓库 Wrapper；系统默认 Java 版本不是构建证据。缺少 JDK 时准备 `LEXIFLOW_JAVA_HOME` 或 `.local/toolchains/jdk-25/`，不以其他主版本替代。

## 1.4. 结论

输入、归属、目标和入口明确才可继续；前提缺失为 `BLOCKED`，入口或版本实际不符为 `FAIL`。随后进入父页对应的专题。
