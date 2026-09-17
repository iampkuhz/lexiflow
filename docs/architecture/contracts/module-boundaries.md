# 模块边界详细参考

> 状态：Proposed；这是详细参考，不是已实现或已验收的声明。

先读 [模块与依赖](../modules-and-dependencies.md)，理解职责与方向；本页用于逐项核对 owner、allowed / forbidden 依赖和公开边界。

## 如何使用本页

先查所关心的边界，再核对对应后续验证场景。原章节编号保留，方便查阅历史评审引用；总览和专题页提供当前解释与实际工程状态。

<!-- retained-contract:start -->
## 5. Bounded Context 与责任

| Context | 拥有的业务责任 | 对外能力 | 明确不负责 |
|---|---|---|---|
| **Identity & Access** | 用户、设备身份、会话认证、数据归属和授权边界。 | 认证后的 `UserContext`、设备/客户端归属检查。 | 词汇评分、字幕处理、模型路由。 |
| **Content** | 来源无关的内容、片段、caption、播放位置和上下文窗口；把 YouTube 输入归一为通用语义。 | 解析或读取当前 Content Context，维护稳定内容引用。 | 判断用户是否认识词；生成中文提示。 |
| **Lexicon** | 全局共享的 term/phrase、lemma、变形、难度、频率、领域与基础义项知识。 | 规范化 term、短语匹配、全局 lexical facts 查询。 | 存储个人掌握度；决定最终 annotation。 |
| **Vocabulary Profile** | 每个用户的当前词汇掌握投影、领域熟悉度、暴露汇总和学习状态。 | 按用户读取 profile snapshot；应用带来源的 Learning evidence；发布 profile 版本变化。 | 保存原始行为事实；直接调用模型。 |
| **Learning** | 接收并保存行为事实；校验行为语义；按版本化、可解释规则把事件归约成 Vocabulary evidence；支持重放。 | durable event intake、evidence 生成、projection/replay 协调。 | 拥有 Lexicon；把前端任意数值直接写入 profile。 |
| **Semantic** | 提供供应商无关的语境消歧、语境翻译、短语语义和解释能力；按任务路由 provider。 | `SemanticProvider` 类端口、路由、预算、超时和标准结果。 | 决定用户是否需要提示；持有用户 profile。 |
| **Enrichment** | 针对 User + Content + Context + Vocabulary Profile 决定提示 span、级别和内容；形成并持久化 annotation result。 | fast enrichment、slow enrichment completion、durable annotation result、annotation 版本/置信度。 | 获取 YouTube DOM；投递客户端结果；更新 profile；绑定具体模型 SDK。 |

### 5.1 关键归属规则

- 原始 `Learning Event` 的历史归 Learning；Vocabulary Profile 只保留可解释投影和来源引用，避免同一事实有两个 owner。
- `Contextual Meaning` 是 Content Context 与 Lexicon/Semantic 结果的组合，由 Enrichment 在一次决策中使用；它不是 Global Lexicon 的永久唯一含义。
- Annotation 的生成及 durable result 归 Enrichment；传输状态、增量投递和客户端同步归 `Client Delivery / Sync` 应用模块。客户端只按 caption identity、annotation revision 和 profile version 合并，不能把 annotation 当作永久词义。
- YouTube 解析代码属于客户端 source adapter；后端 Content Context 不出现 YouTube DOM、CSS selector 或播放器私有对象。

## 6. 模块化单体

### 6.1 推荐模块布局

下表表达目标逻辑职责。Java 25 的 Gradle 骨架已存在，具体公开 API 与领域类型仍未实现；runtime/build/toolchain 决策由 [ADR-010](../decisions.md#adr-010采用-jvm-后端typescript-extension-与独立-python-工具链) 提交 G1 评审，细化业务项目、依赖与公开 API 的拆分仍由对应阶段任务决定。

| 模块组 | 模块 | 责任与可见性 |
|---|---|---|
| Kernel | `foundation` | 极少量稳定标识、时间和版本语义；禁止成为通用工具杂物间。 |
| Domain API | `identity-api`, `content-api`, `lexicon-api`, `vocabulary-api`, `learning-api`, `semantic-api`, `enrichment-api` | 每个 Context 自己拥有的公开 command/query/event/port 契约。外部模块只能依赖这些公开面。 |
| Domain implementation | `identity-domain`, `content-domain`, `lexicon-domain`, `vocabulary-domain`, `learning-domain`, `semantic-routing`, `enrichment-domain` | 聚合、策略、领域服务与 Context 内 application service；实现细节默认不可见。 |
| Cross-context application | `workflow-application` | 鉴权后的 enrichment 与 learning 用例编排、事务边界、durable handoff、跨 Context 协调；不承载领域规则。 |
| Client application | `client-delivery-sync` | 增量结果投递、delivery status、cache version contract、行为上传和 profile sync；只通过 Context 公开 contract 读取结果或提交行为。 |
| Inbound adapters | `transport-http`, `worker-consumers` | 把外部请求或 durable job 转换成 application command/query；只做协议、校验映射和错误映射。 |
| Outbound adapters | `persistence-postgres`, `cache-redis`, `semantic-providers`, `content-source-adapters` | 实现各 Context 声明的 outbound port。按业务 owner 分包，禁止绕过 port 横向读取。 |
| Bootstrap | `bootstrap-api`, `bootstrap-worker` | 唯一 composition roots；装配配置、adapter 和 runtime 生命周期。不得包含业务判断。 |
| Clients | `extension` | YouTube source adapter、英文渲染、L1 cache、增量合并、行为采集。不是服务器单体的内部模块。 |

早期可以在同一构建模块内用强制 package 边界承载一个 Context 的 API 与实现；一旦边界测试不足、团队并行冲突上升或需要独立发布契约，再物理拆成 `*-api` 与 `*-domain`。逻辑依赖规则从第一天生效，不能等待物理拆分后才建立。

### 6.2 静态依赖方向


箭头含义是“源模块可依赖目标模块”。额外规则如下：

1. 所有 Domain implementation 只能依赖 `foundation`、自己的 API，以及上图列明的其他 Context API。
2. `Learning` 通过 `content-api` 校验行为的内容引用，通过 `vocabulary-api` 提交 evidence；`Content` 和 `Vocabulary Profile` 都不能反向依赖 Learning 实现，因此不存在环。
3. `Enrichment` 只读 Content、Lexicon 和 Vocabulary 的公开查询，并使用 Semantic port；它不能调用具体数据库或 provider。
4. outbound adapter 实现 port，但业务模块不依赖 adapter。只有 bootstrap 可以同时看见抽象和实现。
5. `workflow-application` 依赖 `content-api` 完成来源无关的输入编排；`client-delivery-sync` 依赖 Enrichment 公开 contract 读取 durable result，不能直接读取 Enrichment 表。
6. `api` 与 `worker` 不是业务 owner。任何被两个 runtime 复用的业务逻辑必须位于 Application/Domain。

### 6.3 运行入口

| 入口 | 延迟目标 | 主要工作 | 故障影响 |
|---|---|---|---|
| `api` | 低延迟、有限等待 | 鉴权、请求校验、fast enrichment、事件 durable intake、读取当前结果、提交异步工作。 | 不得因模型或 worker 卡住请求线程；仍能提供英文和缓存结果。 |
| `worker` | 吞吐优先、可重试 | semantic cache miss、下一句预取、Learning projection、重放、回填和维护任务。 | 延迟 annotation 或 profile 收敛，不应阻止英文字幕与已缓存 fast path。 |

两个入口可以独立扩容和重启，但属于同一版本化应用。拆成网络微服务前，不引入分布式内部 API。
<!-- retained-contract:end -->
