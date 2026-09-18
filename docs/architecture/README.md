# LexiFlow 架构阅读入口

LexiFlow 帮助用户在看英文内容时理解少量关键表达。**英文先显示，规则决定是否提示，模型提供语境含义，服务端维护个人学习状态**。下面的文档按这个故事逐层展开。

当前只优化第一阶段。业务架构和十项 ADR 仍是 **Proposed**；Java 25 构建及质量工具已有工程验证，真实提示、跨设备同步和数据删除尚未交付。

## 先花十分钟建立整体认识

从 [架构总览](phase-1.md) 开始。它回答系统要解决什么问题、有哪些边界、两条主链路如何配合、为什么采用当前结构。读完再选择专题，不需要先记住全部合同。

<a id="diagram-reading-map"></a>

图：三层阅读地图：整体认识、专题解释、详细合同。

```plantuml
@startmindmap
skinparam backgroundColor #FFFFFF
skinparam shadowing false
<style>
mindmapDiagram {
  node {
    FontColor #1E293B
    FontSize 14
    LineColor #94A3B8
    LineThickness 1
    RoundCorner 12
    Padding 10
    Margin 8
    MaximumWidth 180
  }
  rootNode {
    FontSize 18
    FontStyle bold
    LineColor #475569
  }
  arrow {
    LineColor #94A3B8
    LineThickness 1.2
  }
}
</style>

title 先理解整体，再逐层展开
*[#DBEAFE] LexiFlow 架构
**[#DBEAFE] 第一层：整体认识
***[#DBEAFE] 产品目标与系统边界
***[#DBEAFE] 快慢路径与事实来源
**[#DCFCE7] 第二层：专题解释
***[#DCFCE7] 模块与依赖
***[#DCFCE7] 字幕与学习流程
***[#DCFCE7] 取消、投递、冲突、删除
***[#DCFCE7] 决策与工程交付
**[#F3E8FF] 第三层：详细合同
***[#F3E8FF] 语义能力与结果
***[#F3E8FF] 缓存与来源适配
***[#F3E8FF] 信任、隐私与观测
@endmindmap
```

这张图表示阅读层次。上层给结论和直觉；中层解释机制与取舍；下层保留实现必须遵守的精确条款。

## 带着一个问题进入专题

| 想弄清的问题 | 阅读页面 | 看完应能解释什么 |
|---|---|---|
| 每个模块拥有什么，代码能依赖谁？ | [模块与依赖](modules-and-dependencies.md) | 七个领域、应用边界、组合根与端口的关系 |
| 一句字幕和一次用户行为如何流过系统？ | [字幕与学习流程](caption-and-learning-flows.md) | 快慢提示链路和事实→证据→个人档案链路 |
| 重试、取消、晚到和删除会不会破坏状态？ | [生命周期与一致性](phase-1-lifecycle-guarantees.md) | 工作、尝试、投递观察、意图修订号和删除屏障 |
| 哪些跨模块语义必须共同遵守？ | [横切合同导读](contracts/README.md) | 语义、缓存、信任、观测和来源适配各自的责任 |
| 为什么做这些选择，什么时候重新考虑？ | [架构决策](decisions.md) | 十项 ADR 的推荐、替代方案、代价和复审条件 |
| 设计如何落实到 Java 和验收？ | [质量验收分层](../development/quality-gate-layering.md) | 唯一工具负责人、三层验收和当前证明边界 |

## 实现时再查详细参考

[合同目录](contracts/README.md) 是条款索引。它保留完整输入/结果语义、失败分类、缓存有效性、安全约束、符合性和验收条件。合同中 `Must`、`Should`、`Later` 的原有含义保持不变。

[图源索引](diagrams/README.md) 指向正文内的 PlantUML 代码块；源码随 Markdown 提交，导出 PUML、图表需求、PNG 和 SVG 只在本地校验目录保留。图是正文的解释视角，细节简化不放宽正式合同。

## 设计、执行证据与阶段决定分别查看

[校验指导手册](../development/validation/README.md) 将这里的设计转成可操作的审查路线，并说明工程指令、报告与正式收据的不同证明范围。

本目录只讲架构；工程检查见[校验手册](../development/validation/README.md)，阶段决定见[G1 决策包](../reviews/g1-decision-package.md)。修改正文不自动更新旧收据，也不激活下一阶段。
