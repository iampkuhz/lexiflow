# LexiFlow 架构总览

LexiFlow 的架构围绕两件事组织：**观看时及时提供少量帮助，观看后积累可解释的个人学习状态**。第一件事要求英文字幕独立于网络和模型；第二件事要求用户行为先成为服务端事实，再生成可重建的个人词汇档案。

因此，我们推荐一个 **Java 25 模块化单体**：薄 Chrome 扩展承载观看交互；服务端通过规则快路和语义慢路生成提示；PostgreSQL 保存权威事实；`api` 与 `worker` 分别承载短交互和后台工作。两种进程共享同一套领域边界。

> 任务 id：`LF-ARCH-P1-001`。架构状态：Proposed；第一阶段继续优化。Java 基础骨架已存在，下面的业务链路是待实现设计。SQL、具体业务端点、提示词和部署编排仍在后续阶段。

## 1. 从用户体验理解系统

用户看到的仍是英文，只有可能不熟悉且值得提示的词语得到简短中文解释。例如：

```text
This could materially impair（实质性损害） settlement finality（结算最终性）.
```

这里有三个连续判断：先定位单词、短语和术语；再结合个人状态判断是否需要帮助；最后为选中的表达确定语境含义。**规则决定是否提示，模型决定语境含义**。模型结果回来后，规则还要再检查一次展示资格。

### 第一版目标与非目标

首个入口是有英文字幕的普通 YouTube 点播视频；同一账户的多个 Chrome 实例共享服务端个人档案。英文字幕始终是主内容，任何提示注释不能阻塞观看。

非目标是整句双语字幕、全平台接入、完整复习系统，以及在没有实际规模证据时引入微服务、Kafka 或 Kubernetes。YouTube 字幕可获取性、提示质量和时延仍是待验证假设（假设），见 [详细假设与预算](contracts/architecture-invariants.md#3-assumptions)。

## 2. 先看整体边界

<a id="diagram-system-context"></a>

图：系统上下文：YouTube、扩展、模块化单体、PostgreSQL 与外部模型。

```plantuml
@startuml
skinparam backgroundColor #FFFFFF
skinparam shadowing false
skinparam nodesep 48
skinparam ranksep 52
skinparam defaultFontName "Noto Sans CJK SC"
skinparam defaultFontSize 14
skinparam ArrowColor #475569
skinparam roundcorner 12
skinparam packageStyle rectangle
skinparam componentStyle rectangle
skinparam noteBackgroundColor #FFF7DB
skinparam noteBorderColor #D4B45D
skinparam sequenceMessageAlign center
title LexiFlow 的系统边界
top to bottom direction
package "观看环境" #DBEAFE {
  cloud "YouTube 英文字幕" as youtube
  component "Chrome 扩展" as extension
}
package "LexiFlow 服务" #DCFCE7 {
  component "模块化单体" as backend
}
package "事实与外部能力" #FEF3C7 {
  database "PostgreSQL" as postgres
  cloud "语义供应商" as provider
}
youtube --> extension : S1 提供英文片段
extension --> backend : S2 请求提示与提交行为
backend --> postgres : S3 保存权威事实
backend --> provider : S4 异步获取语义证据
legend bottom
  实线：本图标注的公开交互或依赖
  第一阶段提案；实现状态见工程审查
endlegend
@enduml
```

按 S1–S4 阅读这张上下文图：YouTube 提供英文片段；扩展向服务端请求提示和提交行为；服务端保存权威事实，并在后台按需获取语义证据。**S1 的英文显示不等待 S2–S4**。

扩展拥有当前视频、当前字幕、可见叠加层和本机缓存；服务端拥有个人状态与业务决策；外部供应商只返回不可信的语义证据。浏览器缓存和 Redis 都可以清空重建，不能取代 PostgreSQL。

## 3. 用两条主链路建立认识

### 观看链路：英文先出现，帮助逐步补充

扩展先显示英文，再尝试复用有效 L1 提示。服务端快路读取词库与个人档案，执行候选和提示需求规则，返回已有可靠帮助。语义缺口由后台补齐，不进入快速路径的必需等待。

只有工作 **持久提交已确认**，服务端才可返回 `pending`。明确提交失败返回已有快速结果 + `no-pending`；确认不明要沿用原身份查询或幂等重试，不能凭空另开工作。后台最终结果由提示编排持久化，再经投递独立投递。

晚到结果必须仍匹配当前字幕、修订号、个人档案和权限，才有资格显示。收到载荷、实际显示和点击是不同事实。

<a id="7-youtube-caption-到-annotation-的完整链路"></a>
完整图解见 [字幕提示链路](caption-and-learning-flows.md#字幕链路先显示英文再补充语境提示)，逐步合同见 [原 §7 详细参考](caption-and-learning-flows.md#7-youtube-caption-到-annotation-的完整链路)。

### 学习链路：行为先保存，状态再投影

用户显式标记 known/unknown，以及真实显示、点击、暂停、重播等行为，先由学习归约幂等保存。学习归约把事实归约为带来源和规则版本的证据；个人词汇档案幂等应用证据，形成跨设备当前状态。

显式动作尝试同步投影，成功的单次响应携带新档案版本；投影失败只承诺事件已接受、投影待处理。隐式信号异步批处理并最终一致。事实保留重放能力，不把重复上传当作多次学习。

<a id="8-behavior-到-learning-再到-vocabulary-profile-的完整链路"></a>
完整图解见 [学习反馈链路](caption-and-learning-flows.md#学习链路从行为事实到个人状态)，冲突和重放见 [生命周期](phase-1-lifecycle-guarantees.md#learning-顺序与显式冲突)。

### 两条链路如何连接

上一条链路读取个人档案决定提示；下一条链路改变个人档案，影响之后的提示。连接点是可比较的档案版本和公开合同，任何模块都不能直接读取其他领域的私有表。

<a id="9-同步与异步边界"></a>
同步与异步边界按交互承诺划分：英文显示在本地；事实接受等到持久提交；显式投影等到版本或待处理结论；外部模型、隐式归约和重放由后台完成。所有等待都有截止时间，失败采取明确降级。见 [同步与异步的判断方法](caption-and-learning-flows.md#同步与异步边界按承诺划分)。

## 4. 再理解业务如何分工

<a id="5-bounded-context-与责任"></a>
七个限界上下文可以按三组记忆：**字幕与播放上下文 + 中英词汇语料库提供材料；个性化字幕提示 + 语境释义与模型调用形成帮助；学习行为与证据 + 个人词汇掌握档案积累学习资产**。账号与设备权限提供授权边界。中英词语/短语的基础译义语料由 `:modules:lexicon` 维护；模型语境结果和个人掌握程度分别归属不同模块，见 [具体责任与 Java 模块](modules-and-dependencies.md)。

投递 / 同步属于应用层，负责投递与同步，不额外成为业务领域。基础只放稳定身份、时间与版本合同，不变成共享业务杂物箱。

<a id="61-推荐模块布局"></a>
<a id="62-静态依赖方向"></a>
具体职责、推荐模块布局、静态依赖方向与禁止依赖见 [模块与依赖](modules-and-dependencies.md)。依赖由组合根指向应用层，再指向领域/端口；适配器实现端口，领域不反向依赖适配器。

## 5. 为什么是同一应用、两个运行入口

<a id="diagram-runtime-deployment"></a>

图：部署责任视图：薄客户端、api 与工作进程、事实存储、缓存和外部能力。

```plantuml
@startuml
skinparam backgroundColor #FFFFFF
skinparam shadowing false
skinparam nodesep 48
skinparam ranksep 52
skinparam defaultFontName "Noto Sans CJK SC"
skinparam defaultFontSize 14
skinparam ArrowColor #475569
skinparam roundcorner 12
skinparam packageStyle rectangle
skinparam componentStyle rectangle
skinparam noteBackgroundColor #FFF7DB
skinparam noteBorderColor #D4B45D
skinparam sequenceMessageAlign center
title 同一应用的两个进程与外部依赖
top to bottom direction
node "用户设备" as client_zone {
  node "Chrome 扩展" as extension
}
node "LexiFlow 服务区" as server_zone {
  node "api 进程" as api
  node "后台工作进程" as worker
  database "PostgreSQL" as postgres
  database "Redis 缓存" as redis
}
node "外部能力区" as provider_zone {
  node "模型供应商" as provider
}
extension --> api : E1
api --> postgres : E2
worker --> postgres : E3
api --> redis : E4
worker --> provider : E5
legend bottom
  E1 浏览器请求；E2/E3 事实与工作
  E4 缓存；E5 外部模型
  运行责任图，不承诺具体部署编排
endlegend
@enduml
```

这张部署图回答“在哪里执行”，前面的上下文图回答“谁和系统交互”。`api` 处理有界交互，`worker` 处理语义尝试、隐式归约和恢复任务；二者通过事实存储和公开用例协作。

E1 是浏览器请求，E2/E3 是事实与持久工作，E4 是可重建缓存，E5 是外部模型调用。图只画关键依赖，未展开工作进程的所有缓存访问或投递的具体传输；实例数量和编排方案尚未确定。

两进程隔离长尾延迟，领域仍在同一代码库内保持事务与所有权边界。模块化单体及两入口的取舍见 [ADR-001/002](decisions.md#系统组织用业务边界控制复杂度)。

## 6. 用四条保证检查所有细节

| 保证 | 对用户和实现意味着什么 | 下一层阅读 |
|---|---|---|
| 英文独立显示 | 网络、模型、工作进程失败不阻塞观看；英文优先是硬边界 | [核心流程](caption-and-learning-flows.md) |
| 事实与副本分离 | PostgreSQL 负责权威状态；Redis/L1 丢失只能影响速度 | [缓存合同导读](contracts/cache.md) |
| 每种事实有唯一负责人 | 生成、投递、显示、点击、接受意图、完成投影分别证明 | [生命周期](phase-1-lifecycle-guarantees.md) |
| 外部输入不能直接决定业务 | 网页、客户端声明、供应商输出均要验证与最小化 | [信任边界](contracts/trust-boundaries.md) |

完整 17 条架构约束、必须/应当/后续条件和稳定验收 ID 在 [架构不变量参考](contracts/architecture-invariants.md)。它是实现检查清单，总览不重复平铺每条规则。

## 7. 从设计走向工程与阶段决定

Java 25 产品骨架由 Gradle/Java 的格式、静态分析、架构、测试和覆盖率工具检查。Python 负责规划、运行器和证据治理，不重复实现 Java 源码规则。实际工具责任和验收分层见 [质量验收分层](../development/quality-gate-layering.md)。

设计状态、工程构建通过、正式任务收据和用户阶段决定分别记录。目前仍有正式证据上下文和 Java Gradle 适配器的边界，不能由文档或构建成功推断 G1 通过。

下一步评审需要确认系统组织、快慢路径、显式/隐式一致性、持久工作及来源隔离；理由见 [十项决策](decisions.md)，出口条件见 [G1 决策包](../reviews/g1-decision-package.md)。阶段准入以明确用户决定和正式收据为准。
