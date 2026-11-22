# 1. 模块边界

[架构总览](../system.md) → 模块边界。本文说明各领域由哪个模块维护、模块依赖和跨域访问边界。

LexiFlow 后端是共享两个业务领域的 Modular Monolith，只有 `:api` 一个 Spring Boot 运行项目。判断模块归属先看**独立事实与规则由谁负责**，再看用例和技术怎样调用它们。仅被多处引用、在后台运行或调用模型，都不是新建领域模块的理由。

## 1.1. 两个领域回答不同问题

**Lexicon：这个词或短语有哪些可复用知识？** `:lexicon` 拥有词项、词形、固定短语、基础义项、频率／难度、领域元数据、来源和版本，以及规范化与查询合同。它不拥有当前字幕采用哪个义项、是否提示或供应商输出。[词库合同](lexicon.md)展开 lemma、sense、alias 与发布版本。

**Enrichment：这一段字幕是否需要什么提示？** `:enrichment` 声明 `CaptionIncrementalRequest` 输入合同，拥有候选选择、重叠／密度／价值规则、已发布语义资料的适用性判断、提示结果与版本。它不拥有词库事实、HTTP、DOM、模型 SDK 或工作租约。`settlement` 的基础义项与频率可跨视频复用，属于 Lexicon；在当前一句是否显示“结算”属于 Enrichment。词库命中不等于展示，单次语义结果也不自动变成词库义项。[字幕合同](captions.md)与[语义合同](semantic-materials.md)说明输入和资料门槛。

### 1.1.1. 看似领域、实际上不是模块的概念

分组可见字幕与上次确认的片段是 Enrichment 的 `CaptionIncrementalRequest` 输入值，不是内容库。主题、轨道、时间窗、DOM 与来源专用类型留在扩展。观看只读已发布资料；第三阶段的独立事后分析才可由基础设施适配模型。不预建内容库、语义服务或 shared kernel。

<a id="3-java-module-的完整划分"></a>
## 1.2. 依赖从组合根走向公开 contract

以下是四个产品 Gradle 项目的直接依赖，不是运行时调用顺序。客户端、服务端和存储关系见[系统总览](../system.md)。

| 项目 | 直接依赖的产品项目 |
| --- | --- |
| `:api` | `:enrichment`、`:lexicon`、`:adapters` |
| `:adapters` | `:lexicon` 与 `:enrichment`，用于实现各自公开的持久化端口 |
| `:enrichment` | `:lexicon` 的公开领域合同 |
| `:lexicon` | 无 |

`api` 装配 Enrichment 字幕用例、Lexicon 查询服务和持久化实现；未来后台用例也经这一组合根装配与调度，但不进入观看等待链路。Enrichment 只从 Lexicon 的 `domain.port` 与 `domain.model` **公开合同**读取已发布知识；Lexicon 不反向依赖 Enrichment。`:adapters` 依赖实际使用的 Lexicon，并可直接依赖 Enrichment 仅实现其公开本机偏好与提示计数存储 contract；不发起提示决策、不直接读写其他领域数据。

### 1.2.1. 四个产品项目与三个测试项目

`backend/product/` 与 `backend/verification/` 只按交付职责组织物理目录，不是 Gradle 项目；下列七个项目都使用扁平 Gradle 路径。`backend/gradle/build-logic/` 是独立的 included build，构建逻辑不与产品或验证项目混排。

| Gradle 项目 | 职责与内部结构 |
| --- | --- |
| `:lexicon` | 词库知识、导入与查询；`domain.model/port/catalog`、`application.importing/query/port` |
| `:enrichment` | 字幕提示领域规则与用例；`domain.model/policy`、`application.caption` |
| `:adapters` | 来源文件及持久化等技术实现；实现 Lexicon 与 Enrichment 公开端口 |
| `:api` | 唯一 Spring Boot 入口，负责 HTTP 映射及未来后台任务装配；请求／响应在 `hints.model` |
| `:architecture-tests` | 编译类架构边界及隔离反例 |
| `:quality-gates` | Java 源码工程规则及其直接测试 |
| `:integration-tests` | SQLite、Redis 与 API 的运行 smoke 测试 |

[`backend/gradle/build-logic`](../../backend/gradle/build-logic) 是 Gradle convention 与依赖护栏的独立构建，不是产品模块。测试项目仅在检查时依赖受测模块，不参与运行。扫描覆盖所有业务模块内部的领域与应用源码，不依赖已经不存在的独立 application 项目。

### 1.2.2. 功能在前，职责在后

以 Lexicon 为例，领域实体和值对象在 `domain.model`，查询合同在 `domain.port`。导入服务与计划在 `application.importing`，请求、来源行和批次值对象在其 `model` 子包；查询服务在 `application.query`，存储端口在 `application.port`。Enrichment 的字幕用例和结果分别在 `application.caption` 与 `application.caption.model`。

类名表达角色：行为使用 `Service`、`UseCase`、`Policy` 等，边界输入输出使用 `Request`、`Result`、`Response`；领域实体与值对象保留业务名词。record 是实现语法，不形成统一的 `record/` 目录，也不机械改名为 DTO。嵌套的辅助结果仍可与所属行为类型就近保存。

`domain` 不依赖 `application`，`model` 不依赖服务、用例或领域策略。导入值对象的构造校验委托 `importing.validation` 纯 Java 辅助函数，该包不得依赖其他应用类型；不把必需校验移到可被绕过的服务调用点。持久化的 DO、DAO、Mapper 在同包协作并保留包私有可见性，不为文件夹对称而公开内部类型。上述边界由 [ArchUnit 测试](../../backend/verification/architecture/src/test/java/io/lexiflow/architecture/LayerArchitectureTest.java)与 [Gradle 项目护栏](../../backend/gradle/build-logic/src/main/kotlin/io/lexiflow/buildlogic/VerifyProjectDependenciesTask.kt)验证。

## 1.3. 当前字幕怎样经过边界

来源适配器在本机产生不含平台私有类型的有界输入，`api` 交给 Enrichment 的字幕应用用例；用例经 Lexicon 公开合同查询候选和发布版本，再调用本领域规则，再按价值、密度和适用性决定返回可靠提示或空结果。没有当前字幕模型调用、后台补全或 `pending` 承诺；扩展只显示匹配当前字幕的结果。第三阶段规划的事后分析即使由同一应用调度，产物仍须经公开发布合同后才可供后续观看读取。[观看时序](flows.md)标出每次调用和迟到丢弃。

Chrome 扩展提交显式偏好与实际提示的词条身份，Enrichment 拥有公开存储 contract，`:adapters` 将偏好和每日计数持久化到独立可写 SQLite。已发布词库 SQLite 始终只读，不与本机数据共库。偏好和计数不上传、不用于推断熟悉度、建立账号身份或跨设备合并。[产品说明](experience.md)解释这种低打扰取舍。

## 1.4. 静态边界与变更入口

模块不得直接读写其他 Domain 拥有的数据；来源专有类型不得进入 `CaptionIncrementalRequest`；infra 不得向领域发起决策；组合根之外不得直接依赖具体技术适配。准备修改时先确定所属模块与公开 contract，再看[持久化边界](lexicon/data-model.md)或[来源适配](captions.md)；需要改变长期约束时从[产品架构规范](../../openspec/specs/product-architecture/spec.md)与 OpenSpec 变更进入，不以文档叙述代替验证。

## 1.5. 划分理由与复审条件

| 设计取舍 | 原因 | 何时重新评估 |
| --- | --- | --- |
| 单体内模块隔离、单一运行入口 | 避免尚无独立后台用例时维护第二套部署、监控与启动验证 | 实际资源、吞吐或故障隔离证据证明单进程无法满足观看预算 |
| 技术适配合为一个 Gradle 项目 | Java 包足以隔离技术；按每种技术建项目会增加空壳与依赖噪声 | 出现独立发布、第三方依赖治理或协作边界的真实需求 |
| 产品后端统一 Java 25 | Python 承载产品领域会拆散统一构建与质量边界；扩展仍用 TypeScript，Python 限工程工具 | 工具链兼容性或可复现性出现具体冲突 |
| 业务边界用项目、逻辑层次用包 | 同一用例与领域共同演进，ArchUnit 保护包边界；不为目录对称公开内部类型 | 独立发布、不同依赖治理或协作边界已有证据 |
| 不预建内容域、语义服务或 shared kernel | 先保持词库事实与提示决策的稳定分工，避免没有消费者的抽象 | 出现独立内容／语义产品或具体稳定的跨域合同，再经 OpenSpec 决策 |
