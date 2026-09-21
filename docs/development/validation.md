# 1. 校验手册

校验的核心不是“跑过更多命令”，而是让**本次冻结输入**与**所声称的结论**一一对应。文档改动不能证明 Java 行为，Java 聚合成功不能证明正式验收，旧收据也不能证明改动后的输入。每一层只为自己的结论负责。

## 1.1. 核心校验模型

先确认改动范围、责任人和要证明的结论；再选择该范围唯一适用的交付检查。业务代码由 Gradle 和产品测试验证，Harness/规划由对应 Python 检查验证，文档由链接、图源和人工语义审查验证。跨范围改动要组合这些检查，但不能让其中任一项替代另一项。

正式验收严格分为具名场景：`validate` 执行冻结的交付检查；`review` 只复核冻结 diff 与 validation evidence；`check` 只验证记录、批准和依赖哈希 DAG。`PASS`、`BLOCKED` 与 `FAIL` 是唯一结果；命令退出、Qoder 完成信号和历史报告都不是额外的通过状态。

Task catalog 必须显式提供 `required_check_ids`；字段缺失表示 catalog 到固定检查的集成尚未完成，提交按 `BLOCKED` 失败关闭，不能静默当作空列表。可提交的 daily report 必须通过完整报告结构、声明摘要、选择覆盖、执行结果、输入/配置指纹和输出 artifact 绑定校验；手写一个最小 `PASS` JSON 不是验证来源。

正式验收模块 `scripts/acceptance/` 拥有四个具名场景：`submit`（提交）、`validate`（独立验证）、`review`（独立审查）、`check`（条件核对），以及只读 `status`。提交自动绑定实际 diff/快照/任务要求与真实执行身份；独立验证直接调用 `scripts.verification` 公开 API；审查和条件核对不得重跑交付命令。

## 1.2. 最小闭环

1. 实现完成后运行 `python3 scripts/check_changes.py`，阅读最终 diff 的 `execution_result` 与 `scope_review`；预期外文件需要自审，但不会阻断提交。
2. 运行 `python3 scripts/check_repository.py`，确认当前 checkout 的完整确定性基线。缺少声明的
   `required_environment` 时按报告中的具体缺项显式准备，不能归因于产品断言或视为通过。
3. 正式验收使用 `python3 -m scripts.acceptance submit` 绑定已通过的 Change Verify、catalog Task 要求、冻结 artifact 和实际 producer runtime。日常报告不能提供 Task/version。
4. 不同真实 Codex task/session 运行 `validate`；它调用 `scripts.verification` 公共 API 并验证同一冻结输入。随后独立 reviewer 提交真实审查意见运行 `review`，最后用 `check` 核对 validation/review/dependency 的 hash DAG。审查和条件核对不执行交付命令。

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

本页给出校验原则和闭环，不重复命令参数、字段、报告位置或故障恢复步骤；这些可执行细节只在同名目录 [`validation/`](validation/) 维护。产品范围看[产品简介](../product/product-brief.md)，Gate 的执行合同看[Gate 控制面](gate-control-plane.md)，术语与 receipt 链看[仓库术语表](repository-glossary.md)，派发与回调合同看[Harness 与派发](validation/harness-and-dispatch.md)。
