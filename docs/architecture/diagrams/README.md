# PlantUML 图源索引

所有文档图源直接写在正文的 `plantuml` 围栏代码块中。**Markdown 是唯一真源**；打开下表的正文锚点即可查看与修改，不依赖预生成 PNG/SVG。支持 PlantUML 的阅读器可按代码渲染；普通 Markdown 阅读器会展示源码，不能据此声称阅读器已经渲染。

## 从问题找到代码块

| 问题 | 正文内图源 | 类型 |
|---|---|---|
| 应该先看什么？ | [阅读地图](../README.md#diagram-reading-map) | 思维导图 |
| 谁与 LexiFlow 交互？ | [系统上下文](../phase-1.md#diagram-system-context) | 系统上下文 |
| 谁维护中英语料、提示与学习状态？ | [业务职责](../modules-and-dependencies.md#diagram-domain-responsibility) | 业务组件 |
| 当前 Java 模块实际声明哪些依赖？ | [Java 模块依赖](../modules-and-dependencies.md#diagram-java-module-dependencies) | Gradle 模块架构 |
| 目标代码依赖朝哪里收敛？ | [目标依赖方向](../modules-and-dependencies.md#diagram-dependency-direction) | 目标分层架构 |
| 哪些工作在哪里执行？ | [运行部署](../phase-1.md#diagram-runtime-deployment) | 部署 |
| 候选如何选择快慢路径？ | [提示选择](../caption-and-learning-flows.md#diagram-annotation-selection) | 活动 |
| 已提交慢路结果如何到客户端？ | [异步提示](../caption-and-learning-flows.md#diagram-annotation-async) | 时序 |
| 显式动作的版本与待处理有何区别？ | [学习反馈](../caption-and-learning-flows.md#diagram-learning-feedback) | 时序 |
| 事实、证据与状态分别是什么？ | [学习概念](../caption-and-learning-flows.md#diagram-learning-concepts) | 概念类图 |
| 合格语义请求有哪些职责？ | [语义职责](../contracts/semantic-capability.md#diagram-semantic-outcome) | 活动 |
| 取消和重试还能不能提交？ | [工作生命周期](../phase-1-lifecycle-guarantees.md#diagram-work-lifecycle) | 状态 |
| 收到、显示与点击能否互推？ | [投递观察关系](../phase-1-lifecycle-guarantees.md#diagram-delivery-lifecycle) | 观察状态关系 |
| 旧任务为什么不能复活删除数据？ | [删除屏障](../phase-1-lifecycle-guarantees.md#diagram-delete-barrier) | 时序 |
| 十项 ADR 回答什么？ | [架构决策地图](../decisions.md#diagram-decision-map) | 思维导图 |
| Java / Python 工具分别负责什么？ | [质量工具归属](../../development/quality-gate-layering.md#diagram-quality-ownership) | 工具架构 |
| 交付、复核、目录决定如何分层？ | [分层验收](../../development/quality-gate-layering.md#diagram-gate-layers) | 活动 |

图的一次定义位于上述架构正文；其他手册优先链接该代码块，避免复制后发生漂移。业务协作、Java 编译依赖、部署连接与状态观察分别有不同箭头含义，图下解释及完整合同不能省略。

## 修改与本地校验顺序

1. 先修改正文解释与同页代码块，记录图型、问题、范围和稳定图标识。
2. 将代码块提取到新的被忽略的 `tmp/` 图包，必要时为注册图型准备图表需求；它们是输入/校验中间文件，不是第二份源代码。
3. 通过 `feipi-plantuml-generate-diagram` 的统一入口校验；每批次一次渲染器预检，未变成功包复用，失败按既有修复上限处理。
4. 本地查看 SVG/PNG 核对中文、分支、箭头、终态与布局；确认回执的来源哈希对应当前代码块。
5. 将修改后的 Markdown 交付。PUML 导出、图表需求、渲染图、校验回执和预览留在本地，不提交，不在正文链接这些文件。

类型化图型配置的覆盖与布局证明、兜底的视觉与语义证明分别校验。没有渲染器、需求或必需产物时保持 BLOCKED；旧图成功不能证明修改后的图源。

规则唯一入口是 [AGENTS.md](../../../AGENTS.md)，中间产物忽略范围见 [.gitignore](../../../.gitignore)。外部工具不是本仓库公共 CLI，缺失时不伪造渲染成功。
