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

## 1.2. 完成后的两类 Verify

不用在编辑前预测或锁定最终文件。完成时可选地用 `change_context.py begin|update --paths <directory>...` 记录预期范围，然后运行 `change_verify.py`；它以显式 `--base`、上游 merge-base 或 `HEAD` 之一计算最终 diff，并把预期外文件列为自审项。

随后运行 `repository_verify.py run`。它不关心本次范围，对 checkout 执行全部 repository-baseline 检查及 registry/profile/planning 完整性。两者都不阻断编辑、范围调整或 commit。

## 1.3. Repository readiness

```bash
python3 scripts/gates/repository_verify.py doctor
python3 scripts/gates/repository_verify.py bootstrap --remediation-id jdk-25
```

Java 启动器必须实际选中 Temurin 25 与仓库 Wrapper；系统默认 Java 版本不是构建证据。缺少 JDK、Podman 或受控运行时是 `BLOCKED repository-readiness`，由稳定 remediation ID 指向仓库维护动作；doctor 不修改环境，bootstrap 也不会隐式下载或安装。

## 1.4. 结论

检查断言、输入或版本实际不符为 `FAIL`；仓库准备缺项为 `BLOCKED repository-readiness`。二者均是结束前应阅读的事实，不是本地修改或 commit 的许可判断。随后进入父页对应的专题。
