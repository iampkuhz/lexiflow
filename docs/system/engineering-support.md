# 1. 工程协作与运行支撑体系

[三体系工程总览](engineering.md) → 工程协作与运行支撑。支撑体系为检查和交付验收提供工作包、身份、工具链与隔离资源；它不是门禁链中的顺序阶段，也不替代检查事实或验收判定。

## 1.1. 支撑边界

工作包限定目标、写入范围与验收条件；身份合同约束谁可实现、验证或审查；工具链提供可复现的执行入口；隔离资源限制对工作区和服务的影响。各项具体约束归 [Harness](../../harness/README.md)，本页不复制策略字段或运行状态。

## 1.2. 能力关系

| 能力 | 提供什么 | 深入入口 |
| --- | --- | --- |
| 工作包与委派 | 目标、范围、上下文、验收与回调交接；限制并行写入冲突 | [委派工作](../scenarios/delegate-work.md) |
| actor 与执行边界 | 真实实现、验证、审查身份；完成与验收分开 | [Harness 身份合同](../../harness/agent-policy.manifest.yaml)、[验收体系](delivery-acceptance.md) |
| 工具链与配置 | Java、Python、客户端接入及可核对的运行配置 | [配置查阅](../reference/configuration.md)、[独立工具](../reference/standalone-tools.md) |
| 隔离资源与执行窗口 | 测试服务准备、归属、取消及清理；窗口不锁编辑 | [验证环境](../scenarios/verification-environment.md)、[检查体系](quality-checks.md) |
| 本机运行与发行配套 | 本机启动、资料与候选构建的可执行能力 | [本机开发](../scenarios/local-development.md)、[发行流程](../scenarios/docker-release.md) |

支撑能力可以在实现、检查、验收或发行中被调用，不需要全部执行一次。提供容器或工具并不证明检查通过，提供 actor 身份也不证明证据充分；具体检查事实和验收结论分别由另外两个体系产生。

## 1.3. 按目标进入场景

- 需要拆分并派发实现工作：进入[委派工作](../scenarios/delegate-work.md)。
- 需要准备本机工具、容器或隔离资源：进入[验证环境](../scenarios/verification-environment.md)。
- 需要维护文档及其图源：进入[图文维护](../scenarios/maintain-documentation.md)。

运行入口、文件职责与参数按需查阅 [Reference](../reference.md)；产品服务的组件边界见[系统总览](../system.md)。
