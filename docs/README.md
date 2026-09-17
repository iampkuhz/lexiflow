# 文档阅读路线

先理解产品和系统，再选择专题与实现条款；工程执行证据和阶段决定单独查阅。这样阅读架构时不需要同时处理历史 run、Task 编号和验收过程。

## 理解产品与架构

1. [产品目标](product/product-brief.md)：解决的问题、MVP、非目标和仍需验证的产品假设。
2. [架构阅读入口](architecture/README.md)：三层阅读地图，以及每个专题回答什么问题。
3. [架构总览](architecture/phase-1.md)：两条核心链路、边界、事实来源与部署分工。
4. [架构决策](architecture/decisions.md)：十项 Proposed ADR，按四组问题展开取舍。
5. [详细合同目录](architecture/contracts/README.md)：实现时核对精确语义与验收条件。

## 理解工程如何落地

[工程与交付](architecture/engineering-and-delivery.md) 解释 Java 工具 owner、Python 治理、工作包和分层验收。[参考项目映射](references/feipi-session-browser-java.md) 解释复用的设计模式与未复制的业务内容。

需要修改工程协议时，再查 [Agent 子任务](development/agent-subtasks.md)、[质量验收分层](development/quality-gate-layering.md)、[Gate 控制面设计](development/gate-control-plane-design.md) 和 [dispatch preflight](development/dispatch-preflight-design.md)。这些属于工程合同，不能被总览中的简化图放宽。

## 查看当前证据与阶段出口

| 想确认的事项 | 入口 | 不能混淆的状态 |
|---|---|---|
| Java 工具的实际执法结果 | [确定性工具审查](reviews/phase-1-deterministic-tools-audit.md) | 直接工程验证不等于业务完成或正式 G1 PASS |
| 文档重构质量和输入影响 | [重构审查](reviews/architecture-documentation-restructure.md) | 修改后的正文不能沿用绑定旧 bytes 的 receipt |
| 第一阶段需要接受什么 | [G1 决策包](reviews/g1-decision-package.md)、[需求追踪](reviews/phase-1-user-requirement-traceability.md) | 用户阶段决定与工程测试分别记录 |
| 实现时将证明哪些旅程 | [稳定验收案例](acceptance-cases/README.md) | 预期测试不等于已经执行 |
| 后续阶段如何展开 | [长期交付计划](roadmap/master-plan.md) | 当前只激活第一阶段 |

## 历史记录按需查阅

[历史审查索引](reviews/README.md) 汇集原 run/rework、控制面演进和旧状态材料。它们的 current/活动/PASS 描述适用于原记录时点；正文、历史 receipt 和原始失败都保持原样。
