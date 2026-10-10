# 1. 交付验收体系

[三体系工程总览](engineering.md) → 交付验收。交付验收围绕一个 Task 及其冻结输入，判断证据是否充分、独立、当前，并满足依赖与批准要求。一次实现完成、Stop 结束或某个检查通过，都不单独构成交付验收结论。

## 1.1. 证据判定

验收使用[质量检查体系](quality-checks.md)产出的事实报告；要求独立验证时，由独立 actor 调用同一检查引擎，不另建一套 checks。Formal 验收还按适用合同核对 Task、冻结输入、验证身份、必要审查、批准与依赖。审查和 receipt 核对消费冻结证据，不代替检查执行。

## 1.2. 核心关系与边界

| 能力 | 消费什么／交出什么 | 与检查体系的关系 |
| --- | --- | --- |
| 送验 `submit` | 开发来源报告、Task 与真实变化 → 冻结 submission | 绑定已有报告和输入，不执行第二轮测试 |
| 独立验证 `validate` | 冻结计划与真实 validator → validation 证据 | 在独立身份下调用共用检查能力，范围来自验收计划 |
| 适用审查 `review` | 冻结 diff、validation 与审查意见 → review 证据 | 只读复核，不重跑命令；并非每个风险等级都要求 |
| 条件核对 `check` | validation、适用 review、依赖与批准 → receipt | 核对证据链，不把退出零或作者自检改称验收 |

开发报告证明某个输入上的检查事实；独立验证另外提供来源与身份隔离证明。即使两次命中同一个 Check，也不能从集合相同推导可以共享验收结论。验收 receipt 又不等于 Git 集成或公开发行许可。具体身份、不变量和风险计划只在 [Harness](../../harness/README.md) 维护；各命令的核对字段见下方 Reference。

## 1.3. 操作入口

正式验收的送验、独立验证、审查与依赖核对步骤见[编码后执行门禁的正式验收部分](../scenarios/run-repository-gates.md#14-展开-c正式验证审查与条件核对)；相关命令和字段见[Delivery Gate Reference](../reference/delivery-gate.md)。判断是否进入正式验收也从[编码后执行门禁](../scenarios/run-repository-gates.md)进入。身份与证据合同以 [Harness](../../harness/README.md) 为准。
