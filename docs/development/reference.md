# 1. Reference：按需查阅，不是额外交付阶段

> 位置：[文档首页](../README.md) → [工程地图](overview.md) → Reference。这里回答精确含义、入口与副作用；想理解先后关系请回到[交付主干](change-delivery.md)。

## 1.1. 先选问题类型

| 要查什么 | 入口 | 不应误解为 |
| --- | --- | --- |
| Harness、Quality Gate、receipt 等含义 | [工程术语](reference/engineering-glossary.md) | 产品词条清单 |
| 一个独立检查器怎样使用 | [独立工具](reference/standalone-tools.md) | 每次都必须执行的完整 workflow |
| 配置字段谁拥有、能否修改 | [配置地图](reference/configuration.md) | 所有英文值都可以翻译 |
| 哪个脚本负责当前问题 | [Scripts 职责](reference/scripts.md) | 文件目录即执行顺序 |
| Java 聚合与精确任务 | [Java 检查](reference/java-checks.md) | Python 另行扫描 Java |
| 怎样维护图和可读文档 | [图文维护](reference/documentation.md) | 格式通过等于读者理解 |
| 如何说明执行证据 | [记录模板](reference/record-template.md) | 手写正式 receipt |

## 1.2. 配置与事实各有真源

工程约束在 AGENTS.md/Harness，长期产品 contract 在 OpenSpec；当前状态在路线图状态页，原始运行证据在 ignored tmp。Reference 解释其含义和读取入口，不再维护一份参数副本。

产品概念请查[产品术语表](../product/glossary.md)。同一个 Hint 不能在工程文档中又被当作 Prompt 的别名。
