# 1. LexiFlow 文档入口

**先按任务进入生命周期。** 不必按 `product/`、`architecture/`、`development/` 的目录顺序阅读；它们是现有存放位置，不是三套执行流程。

## 1.1. 从目标进入

| 你要完成什么 | 从哪里开始 | 下一步 |
| --- | --- | --- |
| 修改功能、文档或工程工具 | [仓库生命周期](development/overview.md) | 明确范围 → 实现自检 → 交付验收 → 授权集成 |
| 修改词库导入逻辑 | [功能变更场景](development/scenarios/change-lexicon-import.md) | 领域／适配器／持久化定位与直接测试 |
| 编码后执行现有门禁 | [执行门禁场景](development/scenarios/run-repository-gates.md) | 开发 Stop；需要时正式独立验收 |
| 优化门禁脚本 | [优化门禁场景](development/scenarios/optimize-repository-gates.md) | 先定位成本，再改实现；共用执行页交付 |
| 安装使用或排障 | [用户指南](user/podman-macos.md)／[排障](development/troubleshooting.md) | 使用者不需要先读工程规则 |
| 导入资料、调试或发行 | [运行任务](development/operations.md) | 选择对应操作，不把发行当日常开发前置 |

<a id="12-选择你的入口"></a>

## 1.2. 按需理解或查阅

| 要查什么 | 唯一正文入口 |
| --- | --- |
| 产品价值与范围 | [产品说明](product/product-brief.md)、[产品术语](product/glossary.md) |
| 系统模型、领域合同与数据 | [系统总览](architecture/overview.md) → [调用流程](architecture/flows.md) → 对应合同 |
| 命令、脚本文件与配置 | [Reference](development/reference.md) |
| 计划、验收要求与未决问题 | [路线图](roadmap/master-plan.md)、[状态与问题台账](roadmap/master-plan/phase2/status.md) |
| Agent 约束与长期规范 | [Harness](../harness/README.md)、[OpenSpec](../openspec/project.md) |

<a id="11-阅读地图"></a>
<a id="13-如何逐层定位"></a>

总流程只在[仓库生命周期](development/overview.md)维护；详情页回链到自己的上级。规则、操作、计划和运行证据各有维护位置，不再按目录复制相同事实。
