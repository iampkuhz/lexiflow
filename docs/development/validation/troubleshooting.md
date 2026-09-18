# 1. 校验：故障处理

## 1.1. 先定位失败层

- 启动器/Java 版本错误：先修 `LEXIFLOW_JAVA_HOME` 或仓库 JDK，不改 Wrapper、锁或任务参数。
- Gradle/测试失败：保留完整输出，用一个定向任务定位，再回到聚合交付。
- 文档链接、围栏或策略漂移：运行 `docs_check` 与 `policy_projection --check`，修复真源而不是绕过检查。
- Gate 缺 evidence/issuer packet：记 `FAIL` 或 `BLOCKED` 的实际原因，不从旧运行取值。

## 1.2. Qoder 诊断

先核对显式任务描述符、运行身份、活动快照、完成记录和回调顺序；未知状态禁止补开任务。调用 `qoder_task.py` 只使用现有授权和精确任务输入，不靠重试、等待或扫描“最新”状态恢复。

## 1.3. 记录结果

记录命令、输入版本、产物定位、实际三态和未证明的边界。未运行或跳过的必需检查不是 `PASS`；需要模板时用[记录模板](record-template.md)。
