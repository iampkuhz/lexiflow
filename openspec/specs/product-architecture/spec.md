# 产品架构规范

## 1. 要求

### 1.1. 英文字幕不等待语义服务

客户端必须立即显示英文字幕；后端、缓存或模型故障不得阻断原字幕。

#### 场景：语义供应商超时

- **假定** 当前英文字幕已到达客户端。
- **当** 语义结果超过预算仍未返回。
- **则** 客户端保持英文字幕可见。
- **并且** 系统静默降级或仅使用已缓存的通用提示。

### 1.2. 模块化单体拥有明确领域

Content、Lexicon、Semantic、Enrichment、Client Delivery 与 Platform 必须有明确 owner；模块不得直接读写其他 Domain 拥有的数据。

#### 场景：提示编排查询词汇材料

- **假定** Enrichment 需要判断一个候选 span 是否值得提示。
- **当** 它读取词频、词义或短语资料。
- **则** 它通过 Lexicon 的公开 contract 获取版本化材料。
- **并且** 不直接查询 Lexicon 的持久化表。

### 1.3. Rules 与 Models 分离

确定性策略负责是否值得提示；Semantic Provider 负责上下文消歧和中文表达。提示决策不得依赖账号、设备、个人档案、行为反馈或用户学习状态。

#### 场景：常见词语

- **假定** 词库与规则把候选判为普通且缺少语境价值。
- **当** Pipeline 评估该候选。
- **则** NeedHintPolicy 可直接抑制该候选。
- **并且** 不为该决策强制调用远端模型。

### 1.4. 生成、投递与展示分离

Annotation 生成与投递不得自动产生 `HintDisplayed` 或 `HintClicked`。首版不采集展示、点击、known/unknown 或其他个人学习行为。

#### 场景：慢语义结果到达已切换的字幕

- **假定** worker 已生成并投递一个 hint。
- **当** 客户端发现原 caption 已失效而没有渲染它。
- **则** 客户端丢弃该提示。
- **并且** 不上报展示或点击事件。

### 1.5. Pending work 有持久证据

Enrichment fast result 可以标示语义结果 pending，但只有 durable handoff 成功后才能把它标为已排队；异步入队失败必须降级为没有 pending work。

#### 场景：语义 handoff 提交失败

- **假定** 规则 fast lane 已产生确定性结果。
- **当** durable semantic handoff 未能提交。
- **则** fast result 仍可返回英文安全的确定性结果。
- **并且** 不声称存在已排队的 slow completion。

### 1.6. YouTube 是 Content Adapter

YouTube 特有字幕与播放器细节必须停留在客户端/适配器边界，核心 Enrichment 与 Semantic 使用平台无关 contract。

#### 场景：接入 PDF

- **假定** 后续提供一个 PDF Content Adapter。
- **当** 它产生规范化内容片段和上下文。
- **则** 现有 Lexicon、Semantic 与 Enrichment 核心可以复用。
