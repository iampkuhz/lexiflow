# 1. LexiFlow 文档

文档只维护最新版。图表以正文内 `plantuml` 围栏为唯一图源；草稿、渲染和预览仅在 ignored 的 `tmp/diagrams/` 中存在。

| 区域 | 责任 | 入口 |
| --- | --- | --- |
| 产品 | 目标、范围和第一阶段验收案例 | [产品简介](product/product-brief.md) |
| 架构 | 边界、流程、合同和决策 | [架构总览](architecture/overview.md) |
| 开发 | 方针与边界在根页；详细操作在同名子目录 | [开发与校验](development/validation.md) |
| 路线图 | 长期计划与阶段状态 | [主计划](roadmap/master-plan.md) |

## 1.1. 架构专题

- [模块边界与 Java 模块](architecture/boundaries.md)
- [字幕提示流程](architecture/flows.md)
- [语义能力与结果合同](architecture/semantic-contract.md)
- [运行安全：缓存、信任与可观测性](architecture/runtime-safety.md)
- [来源适配合同](architecture/source-contract.md)
- [架构决策](architecture/decisions.md)

## 1.2. 开发专题

开发根页保留确定结论、核心模型和责任边界；仅在确有必要时，命令、字段、示例与诊断才进入同名子目录，拆分数量由主题决定。

- [校验手册](development/validation.md)
- [Gate 控制面](development/gate-control-plane.md)
- [派发预检](development/dispatch-preflight.md)
- [工具链复现](development/toolchain-reproduction.md)
