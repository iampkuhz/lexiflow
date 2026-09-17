# Product Architecture Spec

## Requirements

### Requirement: 英文字幕不等待语义服务

客户端 MUST 立即显示英文字幕；后端、缓存或模型故障不得阻断原字幕。

#### Scenario: Semantic Provider 超时

- **Given** 当前英文 caption 已到达客户端
- **When** 语义结果超过预算仍未返回
- **Then** 客户端 SHALL 保持英文字幕可见
- **And** 系统 SHALL 静默降级或仅使用已缓存提示

### Requirement: 模块化单体拥有明确 Domain

Vocabulary、Learning、Enrichment、Content、Lexicon、Semantic、Identity/Devices、Client Delivery 与 Platform SHALL 有明确 owner；模块 SHALL NOT 直接读写其他 Domain 拥有的数据。

#### Scenario: Enrichment 查询用户熟悉度

- **Given** Enrichment 需要决定是否提示一个候选 span
- **When** 它读取用户状态
- **Then** 它 SHALL 通过 Vocabulary read contract 获取版本化快照
- **And** SHALL NOT 直接查询 Vocabulary 持久化表

### Requirement: Rules 与 Models 分离

确定性策略 SHALL 负责是否值得提示；Semantic Provider SHALL 负责上下文消歧和中文表达。

#### Scenario: 已掌握词语

- **Given** Vocabulary Profile 对当前语境给出高熟悉度
- **When** Pipeline 评估候选
- **Then** NeedHintPolicy SHALL 可直接抑制该候选
- **And** SHALL NOT 为该决策强制调用远端模型

### Requirement: 学习行为可重放且幂等

客户端行为 SHALL 先作为不可变事件可靠保存，再由 Learning 生成可解释证据和 Vocabulary 更新；重复事件 SHALL NOT 重复改变 Profile。

#### Scenario: 多设备重复上传

- **Given** 两个请求携带相同稳定 event identity
- **When** Event Ingress 接收请求
- **Then** 系统 SHALL 只保存一次有效事件
- **And** Profile 更新 SHALL 只应用一次

### Requirement: 生成、投递、展示与点击分离

Annotation 生成与投递 SHALL NOT 自动产生 `HintDisplayed` 或 `HintClicked`。客户端仅在提示确实渲染给当前用户后 SHALL 发出展示事实。

#### Scenario: 慢语义结果到达已切换的 caption

- **Given** worker 已生成并投递一个 hint
- **When** 客户端发现原 caption 已失效而没有渲染它
- **Then** 客户端 SHALL 丢弃该次展示
- **And** SHALL NOT 上传 `HintDisplayed`

### Requirement: Pending work 有持久证据

Enrichment fast result MAY 标示语义结果 pending，但只有 durable handoff 成功后才能把它标为已排队；异步入队失败 SHALL 降级为没有 pending work。

#### Scenario: Semantic handoff 提交失败

- **Given** 规则 fast lane 已产生确定性结果
- **When** durable semantic handoff 未能提交
- **Then** fast result SHALL 仍可返回英文安全的确定性结果
- **And** SHALL NOT 声称存在已排队的 slow completion

### Requirement: 显式纠正的读己之写

`WordMarkedKnown` 与 `WordMarkedUnknown` 的最终响应 SHALL 反映事件提交和同步 Profile 投影的共同结果；投影失败时 SHALL 明确报告 pending，不能以较早的事件 ACK 冒充 Profile 已生效。

#### Scenario: 用户标记已掌握后立即请求下一字幕

- **Given** 用户上传了 `WordMarkedKnown`
- **When** 系统返回 Profile 更新成功
- **Then** 同一用户后续 Enrichment SHALL 读取到新 Profile 版本

### Requirement: YouTube 是 Content Adapter

YouTube 特有字幕与播放器细节 SHALL 停留在客户端/适配器边界，核心 Enrichment 与 Learning SHALL 使用平台无关 contract。

#### Scenario: 接入 PDF

- **Given** 后续提供一个 PDF Content Adapter
- **When** 它产生规范化内容片段和上下文
- **Then** 现有 Vocabulary、Learning 与 Enrichment 核心 SHALL 可复用
