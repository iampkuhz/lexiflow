# 1. 工程术语：保留英文概念，用中文解释

> 位置：[工程地图](../overview.md) → [Reference](../reference.md) → 工程术语。产品对象另见[产品术语](../../product/glossary.md)。本表统一用词，不替代各主题的行为 contract。

## 1.1. 质量与交付

| 规范写法 | 中文解释及使用边界 | 定义位置 |
| --- | --- | --- |
| Harness | 仓库的共享约束与工程执行支撑；不是业务 Domain，也不是某一个 Gate | [Harness](../../../harness/README.md) |
| Quality Gate | 根据明确输入、检查与证据作出质量结论的关口；不要把每个 helper 都称为 Gate | [交付主干](../change-delivery.md) |
| Check | 一项具体检查能力；与命令 `acceptance check` 的条件核对场景区分 | [Verify](../change-delivery/verification.md) |
| Change Verify | 针对最终 diff 的日常检查与范围自审，不授予编辑权限 | [Verify](../change-delivery/verification.md) |
| Repository Verify | 不读取 Task identity 的全仓 baseline 检查 | [Verify](../change-delivery/verification.md) |
| Acceptance / Formal Gate | 独立身份下的正式送验、验证、审查与条件核对，不等同于本地命令成功 | [Acceptance](../change-delivery/acceptance.md) |
| validation | 执行冻结输入的必需检查；不要与只读 review 混用 | [独立验证](../change-delivery/acceptance.md#13-validate执行独立验证) |
| review | 复核 frozen diff 和 evidence，不重新运行交付检查 | [独立审查](../change-delivery/acceptance.md#14-review复核差异与证据) |
| contract | 能力双方约定的输入、输出、边界和不变量；不是普通描述性注释 | [配置地图](configuration.md) |
| standalone checker | 可由显式输入完成局部判断的检查器；仍可能被 workflow 调用 | [独立工具](standalone-tools.md) |
| workflow | 有上下文、前后条件和交接关系的流程；不等同于文件夹或一个 CLI | [工程地图](../overview.md) |
| readiness | 必需环境和资源是否具备，不证明业务断言正确 | [隔离验证环境](../operations/verification-environment.md) |

## 1.2. 身份、执行与证据

| 规范写法 | 中文解释及易混淆点 | 定义位置 |
| --- | --- | --- |
| Agent | 执行工作包的代理；角色名不证明真实身份或验收独立性 | [Agent workflow](../agent-workflow.md) |
| Task / Catalog | Task 是计划中的任务，Catalog 是任务清单；一个工作包可覆盖多个 Task | [配置地图](configuration.md) |
| WorkStream | 长期职责和 ownership 的组织单位，不等于一次 run 或某个 workflow 阶段 | [配置地图](configuration.md) |
| Work Package | 同一 owner/边界下可有界验收的工作单元，不是每次 run 的别名 | [Agent workflow](../agent-workflow.md) |
| Session / run | Session 是实际宿主会话，run 是一次具体执行；重试不创造新的 Task 身份 | [身份与事实](../agent-workflow/identity.md) |
| producer / validator / reviewer | 分别产出实现、执行验证、独立审查；不同名称不等于不同真实身份 | [Acceptance](../change-delivery/acceptance.md) |
| callback / ack | callback 是终态信号投递，ack 是消费确认；两者均不等于验收 PASS | [Agent 交接](../agent-workflow.md) |
| submission | 绑定 Task、producer、报告和冻结输入的正式送验对象 | [submit](../change-delivery/acceptance.md#12-submit冻结送验输入) |
| report / record / receipt | report 描述检查事实；record 是持久化记录；receipt 在正式链中指被核对的证据记录，不随意互换 | [Acceptance](../change-delivery/acceptance.md) |
| evidence / artifact | evidence 是支持结论的可核验事实；artifact 是具体产物，存在不自动代表可信 | [记录模板](record-template.md) |
| frozen input / snapshot | 前者是被固定的输入闭包，后者是该输入某一刻的内容描述；不代表锁住编辑 | [Verify](../change-delivery/verification.md) |
| fingerprint / hash DAG | 指纹绑定内容；hash DAG 绑定证据和依赖关系，不能靠文件名相同替代 | [条件核对](../change-delivery/acceptance.md#15-check核对条件而非重跑) |
| PASS / BLOCKED / FAIL | 唯一结果枚举，保持原词；具体判定由所属场景负责 | [排障](../troubleshooting.md) |
| fail closed | 缺少可信输入或证据时不放行，不意味着所有错误都归同一状态 | [Acceptance](../change-delivery/acceptance.md) |

## 1.3. AI 与配置语言

| 规范写法 | 使用约定 |
| --- | --- |
| Prompt / Context / Token | 分别指模型输入指令、上下文和模型处理单位；保留英文，不用产品 Hint 替代 Prompt |
| Model / Provider | Model 是模型，Provider 是提供模型能力的实现或服务；不能因观看缺提示而要求配置 Provider |
| inference | 说明模型推理时使用英文术语；与普通推断解释区分，观看链路不执行模型 inference |
| schema / enum / API / CLI | 保留技术原词及具体字段拼写；说明句使用中文 |
| Composition Root / Bounded Context / port | 分别指组装入口、业务边界与抽象接口；与部署进程和具体实现区分 |
| Structured Output / Context Window | 分别指受 schema 约束的模型输出与上下文窗口；保留英文，避免与普通日志输出或 UI 窗口混淆 |
| Domain / Application / Adapter | 架构职责名称保持英文；准确边界见[架构总览](../../architecture/overview.md) |

源码标识符、schema field、状态值、路径、命令和机器匹配的固定值不翻译。首次使用易混淆工程术语时给出短解释或链接，之后使用规范写法。普通连接语自然使用中文，不把整句写成英文词堆。新增术语必须有真实概念边界，不为一个普通动作另造名称。
