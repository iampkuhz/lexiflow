# 1. 校验手册

校验的核心不是“跑过更多命令”，而是让**本次冻结输入**与**所声称的结论**一一对应。文档改动不能证明 Java 行为，Java 聚合成功不能证明正式验收，旧收据也不能证明改动后的输入。每一层只为自己的结论负责。

## 1.1. 核心校验模型

先确认改动范围、责任人和要证明的结论；再选择该范围唯一适用的交付检查。业务代码由 Gradle 和产品测试验证，Harness/规划由对应 Python 检查验证，文档由链接、图源和人工语义审查验证。跨范围改动要组合这些检查，但不能让其中任一项替代另一项。

正式验收严格分三层：`TASK_VALIDATION` 执行冻结的交付检查；`INDEPENDENT_REVIEW` 只复核冻结 diff 与 validation evidence；`CATALOG_DECISION` 只验证收据和依赖哈希 DAG。`PASS`、`BLOCKED` 与 `FAIL` 是唯一结果；命令退出、Qoder 完成信号和历史报告都不是额外的通过状态。

## 1.2. 最小闭环

1. 固定受验输入，记录改动归属和范围。
2. 运行该范围的交付检查；业务代码同时运行产品测试。
3. 读取实际报告，区分已执行、未执行、阻断与失败。
4. 交接前运行 `python3 scripts/gates/cli.py run --mode incremental`；正式验收再按三层顺序消费新鲜证据。

这套顺序避免用更宽泛、更昂贵或历史的检查掩盖真正缺失的证明。

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
