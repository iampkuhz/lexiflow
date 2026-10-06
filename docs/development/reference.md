# 1. Reference：按需查入口和含义

> 上级：[仓库生命周期](overview.md)。想知道先后步骤，先看[执行门禁场景](scenarios/run-repository-gates.md)；Reference 不是新增交付阶段。

## 1.1. 选择要查的内容

| 问题 | 入口 | 证明边界 |
| --- | --- | --- |
| 哪个文件负责这一步？ | [Scripts 职责](reference/scripts.md) | 文件索引不是执行顺序 |
| 怎样使用局部检查器？ | [独立工具](reference/standalone-tools.md) | 局部结果不代替完整验收 |
| 哪个配置是修改真源？ | [配置地图](reference/configuration.md) | 派生配置不是第二份规则 |
| Java 任务包括什么？ | [Java 检查](reference/java-checks.md) | Gradle 执行，不另造 Python 扫描 |
| 怎样更新文字、链接与图？ | [图文维护](reference/documentation.md) | 静态通过不证明阅读效果 |
| 怎样说明本次检查事实？ | [记录模板](reference/record-template.md) | 说明模板不是正式 receipt |
| 工程术语指什么？ | [工程术语](reference/engineering-glossary.md) | 产品对象另见[产品术语](../product/glossary.md) |

## 1.2. 回到真源

工程约束见 [Harness](../../harness/README.md)，长期产品规范见 [OpenSpec](../../openspec/project.md)，未决问题见[状态页](../roadmap/master-plan/phase2/status.md)。本页只索引，不保存参数或状态副本。
