# 架构图源与阅读位置

图以“一张图回答一个问题”为原则：上下文、组件、静态依赖和部署使用不同箭头语义；时序解释交互，活动解释选择，状态解释异常，概念关系区分事实与投影。

正文使用 PNG，便于 Markdown 直接阅读；同名 SVG 支持放大，`.puml` 是可编辑真源。已注册 profile 的 `.brief.yaml` 记录图的边界与覆盖要求。

## 从问题找到图

| 问题 | 图源 / SVG | 类型 | 正文 |
|---|---|---|---|
| 应该先看什么？ | [reading-map.puml](reading-map.puml) / [SVG](reading-map.svg) | 思维导图 | [架构入口](../README.md) |
| 谁与 LexiFlow 交互？ | [system-context.puml](system-context.puml) / [SVG](system-context.svg) | 系统上下文 | [总览](../phase-1.md) |
| 哪个领域拥有什么？ | [domain-responsibility.puml](domain-responsibility.puml) / [SVG](domain-responsibility.svg) | 组件 | [模块与依赖](../modules-and-dependencies.md) |
| 代码可以依赖谁？ | [dependency-direction.puml](dependency-direction.puml) / [SVG](dependency-direction.svg) | 静态依赖分层 | [模块与依赖](../modules-and-dependencies.md) |
| 哪些工作在哪里执行？ | [runtime-deployment.puml](runtime-deployment.puml) / [SVG](runtime-deployment.svg) | 部署 | [总览](../phase-1.md) |
| 一个候选如何选择快慢路径？ | [annotation-selection.puml](annotation-selection.puml) / [SVG](annotation-selection.svg) | 活动 | [核心流程](../caption-and-learning-flows.md) |
| 已提交慢路结果如何到达客户端？ | [annotation-async.puml](annotation-async.puml) / [SVG](annotation-async.svg) | 时序 | [核心流程](../caption-and-learning-flows.md) |
| 显式动作成功与投影 pending 有何区别？ | [learning-feedback.puml](learning-feedback.puml) / [SVG](learning-feedback.svg) | 时序 | [核心流程](../caption-and-learning-flows.md) |
| 事实、证据与状态分别是什么？ | [learning-concepts.puml](learning-concepts.puml) / [SVG](learning-concepts.svg) | 概念类图 | [核心流程](../caption-and-learning-flows.md) |
| 合格语义请求有哪些标准职责？ | [semantic-outcome.puml](semantic-outcome.puml) / [SVG](semantic-outcome.svg) | 活动 | [Semantic 能力](../contracts/semantic-capability.md) |
| 取消和重试还能不能提交？ | [work-lifecycle.puml](work-lifecycle.puml) / [SVG](work-lifecycle.svg) | 状态 | [生命周期](../phase-1-lifecycle-guarantees.md) |
| 收到、显示和点击能否互推？ | [delivery-lifecycle.puml](delivery-lifecycle.puml) / [SVG](delivery-lifecycle.svg) | 观察状态关系 | [生命周期](../phase-1-lifecycle-guarantees.md) |
| 旧任务为何不能复活删除数据？ | [delete-barrier.puml](delete-barrier.puml) / [SVG](delete-barrier.svg) | 时序 | [生命周期](../phase-1-lifecycle-guarantees.md) |
| 十项 ADR 共同回答什么？ | [decision-map.puml](decision-map.puml) / [SVG](decision-map.svg) | 思维导图 | [架构决策](../decisions.md) |
| Java 与 Python 工具谁检查什么？ | [quality-ownership.puml](quality-ownership.puml) / [SVG](quality-ownership.svg) | 工具责任分层 | [工程交付](../engineering-and-delivery.md) |
| 交付、独立复核、目录决定怎样分层？ | [gate-layers.puml](gate-layers.puml) / [SVG](gate-layers.svg) | 活动 | [工程交付](../engineering-and-delivery.md) |

## 如何理解验证范围

16 张图均由本地 PlantUML 实际渲染。13 张使用已注册 typed profiles，经过 brief、coverage、layout、SVG 和 artifact hash 验证；概念类图与两张状态图走 skill 的 fallback，**没有 typed coverage/layout 证明**，结构与语义由视觉/正文审阅核对。

同一图的组件箭头、编译依赖、部署连接不能混用。图下正文说明正常路径、省略范围和结论；完整 owner、失败、授权、版本和幂等条件仍以 [详细合同](../contracts/README.md) 为准。图片不包含真实字幕、学习历史、模型 payload 或本地运行数据。

## 后续修改顺序

先改正文所解释的问题及对应 brief，再编辑 `.puml`；用 `feipi-plantuml-generate-diagram` 的统一入口校验一次，最后更新 SVG/PNG 并做视觉复核。源码不依赖远程 include/theme；renderer preflight、校验回执和调试日志保留在本地 tmp，不把它们当架构正文。

本次检查与收据影响见 [重构审查](../../reviews/architecture-documentation-restructure.md)。
