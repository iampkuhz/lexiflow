# 文档阅读路线

术语：MVP 是最小可用产品，ADR 是架构决策记录，Gate 是验收关卡，Harness 是工程约束与执行框架，SLO 是服务目标。命令、路径、API、标识符与状态枚举保留英文。

## 按问题找唯一入口

| 问题 | 入口 | 内容边界 |
|---|---|---|
| 产品解决什么问题？ | [产品说明](product/product-brief.md) | 目标、用户体验、范围与非目标 |
| 系统怎么工作？ | [架构入口](architecture/README.md) | 总览、模块、流程、一致性与决策 |
| 实现必须遵守什么？ | [合同索引](architecture/contracts/README.md) | 专题精确语义与组合验收 |
| 如何开发和校验？ | [校验手册](development/validation/README.md) | 命令、报告、失败定位；设计原理见[质量分层](development/quality-gate-layering.md) |
| 什么行为算正确？ | [验收案例](acceptance-cases/phase-1.md) | 稳定案例 ID、前提、动作和预期结果 |
| 评审什么、证据在哪里？ | [评审材料](reviews/README.md) | 决策包、检查清单、需求与任务映射，不保存历次复审报告 |
| 后续做什么、现在到哪？ | [长期计划](roadmap/master-plan.md)、[阶段状态](roadmap/phase-1-status.md) | 路线与当前状态分开维护 |
| 借鉴了哪些外部经验？ | [参考映射](references/feipi-session-browser-java.md) | 可复用的工程边界，不继承外部业务与开发机配置 |

每个主题只维护最新版；合并后删除旧页，不保留历史目录、日期快照或复审副本。原始运行收据在忽略目录中保持不可变，不随仓库提供，也不自动证明修改后的文档。
