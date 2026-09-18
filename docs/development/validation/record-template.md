# 校验记录模板

复制下面结构到新的忽略的 `tmp/validation/<record-id>/notes.md`，由执行者填写；不要覆盖旧记录。这个目录是人工记录建议，不是 Gate / 运行器的规范结构定义。

只记录必要元数据和证据定位，原始日志另存本地。密钥、真实字幕、观看历史、配置档案和模型载荷不进入 Git。正式 Gate 产物仍由既有运行器 / 签发者写入规定位置。

```markdown
# 校验记录

日期：
执行者：
校验目的：设计审查 / Java 工程 / Harness / 正式 Task / G1
最终范围与结果：PASS / BLOCKED / FAIL

## 1. 当前输入

- 仓库 / checkout：
- HEAD：
- 未提交文件及归属：
- OpenSpec change / version：
- Task id / task_version / change_version（适用时）：
- 输入与冻结版本 / recorded hash 引用：
- 所需 Java / Python / Wrapper 实际版本：

## 2. 操作

| 步骤 | cwd | 完整命令或人工阅读文件 | 开始/结束时间 | 退出码 | 检查结果 | 证据 locator |
|---|---|---|---|---|---|---|
| | | | | | PASS / BLOCKED / FAIL | |

- required 检查及实际执行数：
- 未运行 / skipped 的项目和原因：
- 人工审查的文件、图、失败案例与结论依据：
- 采用增量或完整聚合的原因（适用时）：
- 失败后的修复、新输入以及必要重验范围：

## 3. 正式证据（仅有真实 artifact 时填写）

- Producer work-package run id：
- Gate run id（与 producer run 不同）：
- evidence / issuer packet locator：
- validation / review / catalog receipt locator 与原引用 SHA：
- issuer / reviewer 独立性与授权核对依据：
- DAG 复核命令与实际结果：
- 当前 catalog 依赖的核对结果：

## 4. 结论边界与下一步

- 本记录具体证明什么：
- 尚不能证明什么：
- 实际 FAIL 与阻塞下一层的 BLOCKED 分别是什么：
- 问题所在 owner / 文件及下一步：
- G1 用户决定来源（未发生写“尚未发生”）：
```

记录中的“最终范围与结果”不能跨层汇总为不真实的 PASS。例如 Java 工程 PASS、正式 Gate 缺上下文 FAIL、G1 BLOCKED 应分别列明。不能把本模板保存为 `receipt.json` 冒充正式收据。
