# 1. 配置地图：先找真源与消费者

> 上级：[Reference](../reference.md)。修改前先确定是说明文字、机器值还是派生字段；不能把所有英文字符串都当成可翻译说明。

## 1.1. 工程配置

| 真源 | 控制什么 | 维护入口／注意点 |
| --- | --- | --- |
| [manifest.yaml](../../../harness/manifest.yaml) | 公开命令与入口索引 | 不保存运行状态 |
| [agent-policy](../../../harness/agent-policy.manifest.yaml) | 范围、模型、身份、风险与调度 | 共享策略的修改真源 |
| [agent-runtime](../../../harness/agent-runtime.manifest.yaml)／[policy-projections](../../../harness/policy-projections.yaml) | 客户端边界与派生映射 | 先改 policy，再生成、核对投影 |
| [module-checks](../../../harness/module-checks.yaml) | Check 命令、触发、依赖、输入与环境 | [声明加载](../../../scripts/verification/declarations.py)、[选择闭包](../../../scripts/verification/scope.py) |
| [delivery-hooks](../../../harness/delivery-hooks.json)／[test-services](../../../harness/test-services.json) | Stop 预算与隔离服务 | [Hook](../change-delivery/hooks.md)、[环境](../operations/verification-environment.md) |
| [ci-policy](../../../harness/ci-policy.yaml) | quick 与正式发行检查集合 | [CI 运行](../operations/github-actions.md) |
| [module-boundaries](../../../harness/module-boundaries.yaml)／[java-product](../../../harness/java-product.manifest.yaml) | 模块归属、依赖与 Java 入口 | [系统边界](../../architecture/boundaries.md)、[Java 检查](java-checks.md) |
| [documentation-policy](../../../harness/documentation-policy.yaml) | 文档与图源合同 | [维护操作](documentation.md)，不在 docs 再造规则 |
| [Catalog](../../../planning/workstreams.yaml)／[Task 模板](../../../planning/task-template.yaml) | Task 范围、依赖和输入形状 | planning-only 不授予派发或验收资格 |

schema 路径由 manifest 索引；schema 合法只证明形状，不授权调用者手填身份或正式结果。

## 1.2. 修改与投影

`path_ownership.resolution` 是机器字段，`most-specific` 是消费者匹配的固定值；中文解释为“按最具体路径确定模块归属”，不要翻译字段值。其他参与 enum、匹配或指纹的值同样先查消费者。

policy 修改后，显式执行 `python3 -m scripts.repository.policy_projection --write`，再 `--check`；这些是有副作用的维护操作，不因阅读文档自动执行。注释或格式变化也改变字节摘要，不据此复用旧报告。具体授权见 [AGENTS.md](../../../AGENTS.md)。

## 1.3. 产品与本机配置

产品配置由对应模块维护：API 的 `application.yaml`、扩展 manifest 和构建参数回到[系统边界](../../architecture/boundaries.md)。本机路径、凭据与运行配置见[本地体验](../operations/local-experience.md)；测试环境见[隔离验证环境](../operations/verification-environment.md)。隐私和提交边界只在 [Harness](../../../harness/agent-policy.manifest.yaml)维护。
