# 步骤 2：检查架构与图解

先看设计能否解释产品，再检查精确条款。这里主要是人工阅读；文档合同命令只提供有限的确定性诊断，不能替代语义审查。

## 1. 看总览，复述两条主链路

依次打开 [阅读入口](../../architecture/README.md) 与 [架构总览](../../architecture/phase-1.md)。不先翻所有合同，尝试回答：

- 用户首先看到什么？为什么网络和模型失败不能阻塞英文字幕？
- 规则与模型各决定什么？慢路为什么要先持久提交才承诺待处理？
- 用户行为、证据与个人档案为什么是三个不同的东西？
- 为什么 `api` / `worker` 两个进程仍是同一个模块化单体？

如果必须靠猜测才能回答，记录缺少解释的段落。这一步得到的是可理解性结论，不是产品行为测试。

## 2. 按问题查看专题与合同

| 需要核对的问题 | 先读的解释 | 再看的精确条款 | 人工检查重点 |
|---|---|---|---|
| 谁拥有数据、谁能依赖谁？ | [模块与依赖](../../architecture/modules-and-dependencies.md) | [模块合同](../../architecture/modules-and-dependencies.md)、[机器边界](../../../harness/module-boundaries.yaml) | 业务负责人唯一；领域不反向依赖应用/适配器；跨领域走公开合同 |
| 一句字幕怎样得到帮助？ | [字幕与学习流程](../../architecture/caption-and-learning-flows.md) | [完整流程](../../architecture/caption-and-learning-flows.md) | 英文先显示；可靠缓存与后台语义分离；明确失败与确认不明不能混用 |
| 重试、乱序、删除会怎样？ | [生命周期](../../architecture/phase-1-lifecycle-guarantees.md) | 同页详细保证 | 终态不复活；意图修订号、负责人代次、晚到与重放都有检查点 |
| 外部输入如何成为可靠证据？ | [横切导读](../../architecture/contracts/README.md) | [六项合同索引](../../architecture/contracts/README.md) | 授权、缓存资格、截止时间、降级、最小上下文及删除约束明确 |
| 为什么选择当前方案？ | [十项 ADR](../../architecture/decisions.md) | 每项结论、替代方案、后果与复审条件 | 能比较备选方案；Proposed 状态与实现现状不混淆 |

用三个具体案例推演：正常提示；模型超时且用户已换字幕；用户标记 known 后删除数据、旧工作进程结果再到达。沿负责人、版本和失败分支逐步说明结果；记录没有依据的跳步。推演不是端到端实测。

## 3. 看图、对图源、核对正文

从 [图源索引](../../architecture/diagrams/README.md) 找到正文的围栏 `plantuml` 代码块。先读图下箭头说明，再对照源码的参与者、连线、分支与终态；需要视觉核对时，在本地忽略的图包渲染并查看 SVG/PNG，不从 Git 读取生成图片。

在 IDEA 中打开 Markdown 正文，启用其 Markdown PlantUML 扩展并使用正文预览；必须看到图形而非代码或死链接。独立 `.puml` 插件窗口不是这项验收。按架构、组件、部署、活动、时序、思维导图、类图和状态图分别检查中文、布局与截断，无法预览则单独记 `BLOCKED`。

重点核对：上下文图的交互不能当作编译依赖；收到载荷不能当作显示；成功投影返回版本前要完成对应缓存失效；部署图简化没有取消模块负责人；概念类图不承诺具体 SQL 或 API。

新增或移动页面后，逐个点击受影响页面的本地链接，确认目标 Markdown、章节锚点和图源代码块可访问。只审本轮受影响链接时，记录这个范围，不能声称全仓链接已经验证。

图解审查的 `PASS` 要有正文与完整合同依据，并且本地图包哈希对应当前代码块。类型化图与兜底图的证明范围不同，不能把兜底宣称为已通过类型化覆盖/布局；历史图数量不能替代当前检查。

仅阅读不需要重新渲染。修改图时，先改同页代码块，再提取 PUML、准备图表需求并按图索引的顺序用 `feipi-plantuml-generate-diagram` 验证；中间产物全部留本地忽略的图包。该工具由本机安装提供，先按 [Harness 接入说明](../../../harness/README.md) 执行 `local_skills check` 或 `link`；仓库不复制实现，也没有第二个公共渲染 CLI。没有工具时记渲染 `BLOCKED`，不能用旧图片充当新源码结果。

## 4. 必要时运行文档合同诊断

下面以总览任务为例，可直接执行：

```bash
python3 -m scripts.harness.docs_check
python3 -m scripts.harness.policy_projection --check
python3 -m scripts.gates.task_contracts --task-id LF-TSK-ARCH-0001
git diff --check
git diff --cached --check
```

任务输出看 `status`、`assertions[].missing_terms` 与 `inputs`；精确输入列表在 [G1 配置档案](../../../harness/g1-task-contract-profiles.yaml)。只对受影响且运行器为 `task-contract` 的稳定任务运行，不对所有任务猜测同一个命令。

这个检查器检查必需输入与词项，不证明流程语义、图像布局、所有链接或正式独立验收。模块边界与核心流程的精确条款现与图解同页，相关配置已绑定合并页和专题合同；检查总览 PASS 仍不能代替逐条语义审阅或正式收据。`git diff --check` 也只检查差异中的空白字符；尚未跟踪的新文件不在普通差异范围，要人工检查行尾空白、补丁标记和围栏代码闭合。

## 判断与下一步

保存语义审查的结论与问题所在文件。图文矛盾、负责人不明或关键失败分支缺失记 `FAIL`；必需材料/渲染缺失记 `BLOCKED`。只有明确审查范围内全部完成，才称该范围 `PASS`。

设计评审完成后，正式任务验收走 [步骤 5](05-receipts-and-acceptance.md)；看 G1 是否具备决定条件走 [步骤 6](06-phase1-decision.md)。不因文档看懂了就进入产品实现。
