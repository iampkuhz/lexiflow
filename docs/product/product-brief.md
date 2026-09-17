# LexiFlow Product Brief

> 来源：项目发起人于 2026-09-16 提供的初始需求；后续 Accepted OpenSpec/ADR 若与本页冲突，以较新的显式决策为准。

## Problem

传统整句中英双语字幕会遮挡英文、增加阅读切换，并且无法区分用户已经掌握与真正需要帮助的内容。词典最高频释义又经常忽略当前专业领域和上下文。

LexiFlow 要在英文内容上提供一层低打扰帮助：保留英文，只在用户可能不熟悉的核心单词、短语、术语旁给出简短、语境化的中文提示。

示例：

```text
This could materially impair（实质性损害） settlement finality（结算最终性）.
```

## Product goals

- 英文字幕始终是主内容，annotation 不能阻塞观看。
- 系统结合用户、内容、上下文和 Vocabulary Profile 决定提示哪些 span 以及提示深度。
- 多设备、多个浏览器实例共享同一服务端 Vocabulary Profile。
- 行为事实驱动可解释、可重算的学习状态，而不是只有 known/unknown。
- YouTube 作为首个 Content Adapter；Web、PDF、Podcast 与其他视频平台可复用核心域。
- Semantic Provider 可替换，不把业务绑定到一家模型 API。

## Product non-goals for the first release

- 整句双语字幕或每句完整翻译。
- 概率模型、Vector DB、Kafka、Kubernetes、Service Mesh 或大规模微服务。
- PDF、Podcast、网页和其他视频平台的产品实现。
- 完整复习系统、内容推荐或社交能力。

## First MVP boundary

- Chrome Extension + 有英文字幕的普通 YouTube 点播视频。
- 单词、短语、专业术语三类 annotation。
- 一个账户跨多个 Chrome 实例同步。
- 一个真实 Semantic Provider 和确定性 fake。
- 规则型 familiarity，以及显式 known/unknown。
- 浏览器 L1、Redis L2、PostgreSQL 事实存储。
- 模型、worker、Redis 或后端失败时，英文观看仍可继续。

## Product evidence to establish

- YouTube 字幕在人工/自动字幕、长视频、直播回放等样本中的获取稳定性。
- 当前句和有限前后文是否足以支持高质量消歧。
- Need-hint 的 precision/recall，以及提示密度、出现时机和闪烁体验。
- 显式与隐式行为对熟悉度的信号质量。
- 模型时延、cache hit、单位观看小时成本和隐私边界。

这些需要通过 spike、人工标注集和实际用户观察验证，当前均不能当成已证实事实。

## Delivery sequence

1. Architecture
2. Data Model
3. API and walking skeleton
4. Enrichment Engine
5. Chrome Extension
6. Learning Model
7. Productionization

每一 Phase 先明确决策、替代方案与 trade-off，再进入下一层。当前只激活 Phase 1；SQL 字段、具体 endpoint、Compose、Prompt 和业务代码等待架构确认。

