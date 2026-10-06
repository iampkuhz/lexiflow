# 1. 第二阶段任务与启动合同

本页说明阶段顺序与验收入口，不复制 Task 身份、负责人、范围、版本、依赖或检查命令；这些机器可核对的内容以[Catalog](../../../../planning/workstreams.yaml)为准。仅 `READY` Task 可派发，执行事实只见[状态页](status.md)，目标见[阶段入口](../phase2.md)。

## 1.1. 正式启动条件

启动前核对当前授权、Harness、Catalog 与实际代码，确认 checkout 和并发写入边界。冻结公共合同后再按依赖串行集成；验证串行。stage、commit、merge、分支切换和公开发布均须本次明确授权，旧计划不构成授权。

## 1.2. 执行波次与边界

- **设计冻结**：先核对分类及处理主线，再冻结观测合同和架构表达；图不能替代产品合同。
- **后端主线**：按 Catalog 依赖顺序落地词库、持久化、离线准备、观测和 API；公共接口与消费者在同一集成窗口切换，不保留旧接口兼容层。
- **客户端与主线验收**：按依赖完成扩展，再执行跨模块验收；实施者不签发独立验证或审查。
- **发布与使用闭环**：以[发布与使用闭环](release.md)为计划与验收入口；发行、安装或真实平台未验收前不得宣称支持。
- 观测是各功能的验收要求，不另建观测业务阶段；未来性能和模型事项不混入本阶段。

冻结后按 Catalog 收紧文件范围与验收断言。超出 Catalog 工作包限制时先拆分，不用大 glob 扩大授权。模块职责见[边界合同](../../../architecture/boundaries.md)，具体实现行为见对应系统合同与源码。

## 1.3. 验收与深入入口

- 产品主线的跨模块场景：[主线集成验收合同](acceptance.md)。
- 制品、安装、升级、恢复与发行：[发布与使用闭环](release.md)。
- Agent 调度与质量门禁：[Agent workflow](../../../development/agent-workflow.md)及[交付主干](../../../development/change-delivery.md)。
- 后续模型与性能规划：[后续待办池](../future.md)，不混入本阶段。

## 1.4. 准备与交付证据的区别

准备阶段只核对文档、Catalog 与策略投影，不证明产品行为。正式实现后，直接测试及风险要求的验证由独立 `TASK_VALIDATION` 按冻结计划执行；`INDEPENDENT_REVIEW` 只读冻结 diff/evidence，`CATALOG_DECISION` 只核对 receipt 与依赖 hash DAG。发行仍需完整 Repository Verify；必需检查未运行、跳过或缺环境不得称 PASS。身份和执行入口以[Harness](../../../../harness/README.md)为准。

## 1.5. 计划整合与共享入口

Task 列表、依赖、范围和检查命令只在[Catalog](../../../../planning/workstreams.yaml)维护；本页不另存清单或版本副本。跨批次交接按 Catalog 当前任务查看，避免随路线图变更重复维护任务号索引。

## 1.6. Agent 调度与质量门禁优化

调度、模型路由、身份与质量门禁由[Harness](../../../../harness/README.md)及[长期执行规范](../../../../openspec/specs/agent-execution/spec.md)维护。自检、独立验收、审查与正式发行各自遵守对应边界，不相互替代。

## 1.7. 当前文档精简

文档清理目标见对应 OpenSpec 变更；本页不维护历史检查结果或验收状态。精简应保留必要合同、操作前提、失败去向和可验收断言，详细范围见 Catalog，执行事实见[状态页](status.md)。
