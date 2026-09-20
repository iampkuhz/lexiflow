# 1. 校验手册

校验的核心不是“跑过更多命令”，而是让**本次冻结输入**与**所声称的结论**一一对应。文档改动不能证明 Java 行为，Java 聚合成功不能证明正式验收，旧收据也不能证明改动后的输入。每一层只为自己的结论负责。

## 1.1. 核心校验模型

先确认改动范围、责任人和要证明的结论；再选择该范围唯一适用的交付检查。业务代码由 Gradle 和产品测试验证，Harness/规划由对应 Python 检查验证，文档由链接、图源和人工语义审查验证。跨范围改动要组合这些检查，但不能让其中任一项替代另一项。

正式验收严格分三层：`TASK_VALIDATION` 执行冻结的交付检查；`INDEPENDENT_REVIEW` 只复核冻结 diff 与 validation evidence；`CATALOG_DECISION` 只验证收据和依赖哈希 DAG。`PASS`、`BLOCKED` 与 `FAIL` 是唯一结果；命令退出、Qoder 完成信号和历史报告都不是额外的通过状态。

## 1.2. 最小闭环

1. 实现完成后运行 `python3 scripts/gates/change_verify.py`，阅读最终 diff 的 `execution_result` 与 `scope_review`；预期外文件需要自审，但不会阻断提交。
2. 运行 `python3 scripts/gates/repository_verify.py run`，确认当前 checkout 的完整确定性基线。`BLOCKED repository-readiness` 要按 stable remediation ID 用 doctor/bootstrap 交给仓库维护修复，不归因于本次业务改动。
3. 正式认证前，执行者明确确认 scope review，并以 `certify_submit.py` 生成 subject evidence。
4. 不同真实 Codex task/session 运行 Formal Gate 的 `TASK_VALIDATION`；它必须在同冻结输入取得 Repository Verify PASS。之后独立 review 与 catalog 仅消费 receipts。

## 1.3. 详细流程入口

| 要验证的内容 | 详细流程 |
| --- | --- |
| 范围、改动和入口 | [范围与准备](validation/preparation.md) |
| 架构、链接与图表 | [架构与图表](validation/architecture-and-diagrams.md) |
| Java 工程与定向诊断 | [Java 工程](validation/java-engineering.md) |
| Harness、目录与派发 | [Harness 与派发](validation/harness-and-dispatch.md) |
| 正式收据与独立复核 | [收据与验收](validation/receipts-and-acceptance.md) |
| 第一阶段出口 | [第一阶段决定](validation/phase-1-decision.md) |
| Java/Gradle 精确任务 | [任务参考](validation/java-gradle-task-reference.md) |
| 故障处理与执行记录 | [故障处理](validation/troubleshooting.md)；[记录模板](validation/record-template.md) |

## 1.4. 边界

本页给出校验原则和闭环，不重复命令参数、字段、报告位置或故障恢复步骤；这些可执行细节只在同名目录 [`validation/`](validation/) 维护。产品范围看[产品简介](../product/product-brief.md)，Gate 的执行合同看[Gate 控制面](gate-control-plane.md)，派发冲突判定看[派发预检](dispatch-preflight.md)。
