# 1. 配置地图：先找 owner，再改说明或机器值

> 位置：[工程地图](../overview.md) → [Reference](../reference.md) → 配置。字段和固定值由消费者解释；不是 YAML 中所有字符串都可以翻译。

## 1.1. Harness 与计划

| 真源 | 拥有的内容 | 读者应注意 |
| --- | --- | --- |
| [manifest.yaml](../../../harness/manifest.yaml) | 工程入口、公开命令与文档导航 | 不保存运行状态 |
| [agent-policy.manifest.yaml](../../../harness/agent-policy.manifest.yaml) | 共享身份、调度、规模与安全策略 | 是派发策略真源，不在客户端复制规则 |
| [agent-runtime.manifest.yaml](../../../harness/agent-runtime.manifest.yaml) | 客户端 Session/checkout 能力 | 共享字段由 policy 投影，非独立修改入口 |
| [policy-projections.yaml](../../../harness/policy-projections.yaml) | 真源到派生字段的映射 | 先改真源，再显式生成、核对 |
| [module-checks.yaml](../../../harness/module-checks.yaml) | Check 的命令、scope、triggers、环境、输入与结果 contract | 当前 baseline/change 声明是不同场景，不等于每次重复执行 |
| [module-boundaries.yaml](../../../harness/module-boundaries.yaml) | 模块 owner 与依赖边界 | 与流程次序区别 |
| [java-product.manifest.yaml](../../../harness/java-product.manifest.yaml) | Java 构建与质量入口 | Java 检查仍由 Gradle 执行 |
| [documentation-policy.yaml](../../../harness/documentation-policy.yaml) | 文档语言、图源与信息组织 | 机器静态检查不代替人工理解 |
| [planning/workstreams.yaml](../../../planning/workstreams.yaml) | Catalog 与阶段分解 | planning-only 不是派发与验收资格 |
| [planning/task-template.yaml](../../../planning/task-template.yaml) | 工作包/Task 输入形状 | 示例或模板不是运行身份与正式证据 |

Qoder 与 Codex 工作包 schema、Acceptance record schema 定义固定形状；它们不是可手填身份的授权接口。精确 schema 路径见 manifest 的 agent_contract。

## 1.2. 语言与投影的处理顺序

先确认某字段是人类说明、机器枚举还是参与匹配/指纹的值。注释与说明用中文，并保留[工程术语](engineering-glossary.md)；字段、ID、command、path、schema 与状态固定值保持原样。即使名为 description/reason，也先检查消费者。例如 path_ownership.resolution 看似英文句子，但 planning_check 会匹配 most-specific，因此保留原值并用中文注释解释。文件注释变化也会改变 byte hash，不能据此复用旧的 Verify 或 receipt。

policy 改动只从真源开始，显式 write 投影后 check。对格式或注释的调整不等于获得修改模型、预算、权限、触发或 receipt contract 的授权。

## 1.3. 产品与运行配置

API/worker 的 application.yaml、扩展 manifest 与构建参数由相应运行模块拥有。开发容器见[运行与环境](../operations.md)；测试地址必须显式隔离，不从个人配置推断。Secrets、真实字幕、观看历史、模型输入输出和本地运行数据不能进入共享配置。
