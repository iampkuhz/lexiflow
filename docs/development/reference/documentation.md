# 1. 图文维护：先建立模型，再写细节

> 位置：[工程地图](../overview.md) → [Reference](../reference.md) → 图文维护。规则真源为 [documentation-policy.yaml](../../../harness/documentation-policy.yaml)，本页解释操作顺序与阅读复核。

## 1.1. 按读者问题安排正文

Overview 先讲目的和主图，再给阶段与下钻；workflow 说明前置、输出、交接和失败位置；Reference 只解释局部参数或能力。短小独立工具不强套长模板，复杂 contract 也不为追求短页而切碎上下文。

一个页面只保留一个主要阅读任务。术语以[工程词表](engineering-glossary.md)和[产品词表](../../product/glossary.md)为准；语句与注释用中文，机器值保持原样。源码注释解释不变量、边界与原因，不用“由前置条件限定”代替具体约束。

## 1.2. 图型选择与制作

| 要解释什么 | 适用图型 |
| --- | --- |
| 阅读层级、主题拆解 | mindmap |
| owner 和依赖方向 | architecture / component |
| 阶段、分支、终点 | activity |
| 调用顺序和角色交接 | sequence |
| 运行状态与允许转移 | state |
| 本机进程、网络与资源隔离 | deployment |
| 聚合与数据身份关系 | class / 对象关系图 |

修改图时先读取 `feipi-plantuml-generate-diagram`，在 ignored `tmp/diagrams/<document>/<diagram>/source.puml` 写草稿，按 profile 校验并真实渲染、查看。只有通过后才将完整源码原样复制到 Markdown 的 plantuml 围栏，再核对来源 hash。state/class 等 fallback 必须明确其校验边界，不冒充 typed coverage。

图源改变就重新从草稿验证。正文不能依赖调试 PUML、SVG、PNG；无工具或不能查看预览记 BLOCKED，不声称视觉通过。仅维护 skill 本身才使用 skill 治理能力。

## 1.3. 先做阅读走查，再看静态结果

检查读者是否能从首页理解观看链路、独立验收、失败恢复，并从阶段找到对应文件。图中箭头必须说明是调用、数据还是阶段顺序；不能用一张图混合所有层级。读者从详情页应能回到上级与下一步。

静态检查使用 `python3 -m scripts.repository.docs_check`，投影另用 `policy_projection --check`。这不证明图文语义或实际用户体验。只维护每主题的最新正文；重排时同步链接并保留仍需使用的锚点，状态只在状态页。
