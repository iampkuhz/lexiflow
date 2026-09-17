# 架构详细合同索引

这里保存实现和审查时需要逐项核对的精确语义。建议先读 [架构总览](../phase-1.md) 和对应专题，再查条款。所有设计合同保持 Proposed；原任务、Must/Should/Later 和稳定验收 ID 保留。

## 提示、内容与个人状态

| 参考 | 回答的问题 | 配套解释 |
|---|---|---|
| [模块边界](module-boundaries.md) | Domain、Application、adapter 与 root 的详细职责 | [模块与依赖](../modules-and-dependencies.md) |
| [核心流程](core-workflows.md) | caption→annotation、event→evidence→Profile 每步的责任 | [字幕与学习流程](../caption-and-learning-flows.md) |
| [架构不变量](architecture-invariants.md) | assumptions、17 条约束、同步矩阵、失败矩阵和验收 ID | [总览](../phase-1.md)、[工程交付](../engineering-and-delivery.md) |

## 跨模块的共同语义

| 合同 | 核心职责 | 原任务 |
|---|---|---|
| [Semantic 能力](semantic-capability.md) | 四类任务级能力、输入最小化、路由责任与预算 | LF-TSK-SEM-0001 |
| [Semantic 结果](semantic-result.md) | outcome、置信、失败和晚到兼容 | LF-TSK-SEM-0002 |
| [Cache](cache.md) | key、版本、hard expiry、数据分类、purge | LF-TSK-PRF-0002 |
| [信任边界](trust-boundaries.md) | 资产、边界 crossing、威胁与 abuse cases | LF-TSK-SEC-0001 |
| [Observability](observability.md) | correlation、logs、metrics、trace 和 redaction | LF-TSK-OBS-0001 |
| [Source Adapter](source-adapters.md) | canonical segment、capability 缺失和跨来源 conformance | LF-TSK-ADP-0001 |

## 阅读与实现约束

详细条款保持原章节编号，避免历史评审引用失去定位；文件移动本身会影响按 path/hash 冻结的证据。新专题图只简化视角，不放宽 owner、deadline、鉴权、幂等、版本或隐私要求。正式要求还受 [Product Architecture Spec](../../../openspec/specs/product-architecture/spec.md) 约束。
