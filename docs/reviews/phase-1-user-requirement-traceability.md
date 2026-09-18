# 第一阶段用户需求追踪

来源：项目发起人 2026-09-16 的产品需求。本文证明设计覆盖关系，不证明实现、正式收据或用户批准。

## 六项架构交付

| 用户要求 | 设计证据 | 后续证明 |
|---|---|---|
| 领域划分 | [模块与依赖](../architecture/modules-and-dependencies.md)：七个领域与投递/同步应用边界 | 当前输入验收与明确用户决定 |
| 各模块职责 | 同页的领域职责、Java 模块及精确边界条款 | 工程骨架不等于业务交付 |
| 依赖方向 | 同页静态依赖与公开端口；机器约束为 `harness/module-boundaries.yaml` | 架构检查与产品行为分别验证 |
| 字幕到提示 | [字幕与学习流程](../architecture/caption-and-learning-flows.md)：快慢链路、持久交接、晚到拒绝 | 扩展/后端实测上下文、时延与失败路径 |
| 行为到学习状态 | 同页：不可变事实、版本化证据与个人档案投影，显式动作可读己之写 | 数据模型、评分与重放实现 |
| 同步和异步边界 | 同页同步矩阵与[生命周期](../architecture/phase-1-lifecycle-guarantees.md) | 一致性与延迟实验，不能以设计推演代替 |

## 产品与工程约束

| 要求 | 设计立场 | 证据 |
|---|---|---|
| 英文为主，不做整句双语字幕 | 本地英文先显示，网络和模型不阻塞；只对选中词段提示 | [产品说明](../product/product-brief.md)、ADR-003 |
| 少量、语境化帮助 | 规则决定是否提示，语义端口只提供含义证据 | ADR-003、[语义能力](../architecture/contracts/semantic-capability.md) |
| 跨设备个人档案 | 服务端维护可解释版本化投影；known/unknown 只是输入，不是全部学习模型 | ADR-005/006、学习流程 |
| 保留行为事实 | 学习领域拥有事实并生成证据，档案拥有投影；不是全系统事件溯源 | ADR-005 |
| YouTube 只是首个来源 | 平台解析在扩展适配器，内容核心与来源端口独立 | ADR-004、[来源合同](../architecture/contracts/source-adapters.md) |
| 模型供应商可替换 | 业务依赖任务级端口及标准结果，不依赖供应商 SDK | ADR-007、[语义结果](../architecture/contracts/semantic-result.md) |
| 模块化单体 | 同一代码库与业务版本，api/工作进程两组合根，PostgreSQL 持久交接；Redis 和浏览器缓存可重建 | ADR-001/002/008 |
| 薄扩展 | 获取上下文、渲染、缓存、发行为事件，不拥有档案真相 | 模块职责与根规则 |
| 可测质量、失败和成本 | P95/P99 等是待验证假设，不虚构已达成的 SLO 或模型成本 | [决策包](g1-decision-package.md)、产品证据清单 |
| 隐私与观测 | 不提交真实字幕、档案、历史、模型载荷和运行数据；诊断最小化与脱敏 | [信任边界](../architecture/contracts/trust-boundaries.md)、[观测合同](../architecture/contracts/observability.md) |
| 重大选择比较替代方案 | 十项 ADR 包含背景、推荐、替代、代价与复审条件，仍为 Proposed | [架构决策](../architecture/decisions.md) |

## 阶段边界

阶段准入顺序只见[手册步骤 6](../development/validation/06-phase1-decision.md)，当前结果只见[阶段状态](../roadmap/phase-1-status.md)。需求追踪不声明任务通过或用户批准。

SQL 字段、具体 REST 端点、部署编排、提示词工程与业务实现仍属于后续阶段。
