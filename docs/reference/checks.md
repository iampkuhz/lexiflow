# 1. 检查入口索引

> 上级：[Reference](../reference.md)。本页供按需定位入口；检查体系职责见[质量检查](../system/quality-checks.md)，整体关系见[三体系工程总览](../system/engineering.md)。

## 1.1. 按目标选择

| 要做什么 | 操作入口 | 本页不替代什么 |
| --- | --- | --- |
| 对已完成的工作做开发检查 | [编码后执行门禁](../scenarios/run-repository-gates.md) | 不要求把所有检查入口顺序执行 |
| 申请独立 Formal 验收 | [执行场景 1.4](../scenarios/run-repository-gates.md#14-展开-c-正式验证审查与条件核对) | 不以开发报告代替 Task 证据判定 |
| 查找具体命令、字段或工具 | [Verify](verification.md)、[Delivery Gate](delivery-gate.md)、[Hook](hooks.md)、[独立工具](standalone-tools.md) | 不保存第二份参数或策略真源 |
| 准备运行环境或定位失败 | [验证环境](../scenarios/verification-environment.md)、[排障](troubleshooting.md) | 不把缺环境称为检查通过 |
| 了解 CI 与发行检查入口 | [GitHub Actions](../scenarios/github-actions.md)、[Docker 发行](../scenarios/docker-release.md) | 不把普通 CI 与完整发行验证混成一种范围 |

质量检查产出输入上的事实报告；交付验收独立判断 Task 证据充分性、独立性、当前性及依赖批准。场景、检查范围、CLI／Stop／CI 入口与 actor 是不同维度，不构成并列门禁层次。唯一机器合同见 [Harness](../../harness/README.md)。
