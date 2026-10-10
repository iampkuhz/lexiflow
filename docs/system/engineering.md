# 1. 工程三体系与文件生命周期

[仓库生命周期](../README.md) → 工程治理总览。本文面向首次接手仓库的开发者：先理解治理模型，再从真实文件目录和使用生命周期定位实现。Markdown 负责解释与跳转，不是编号对象。

## 1.1. 工程治理的职责

- **质量检查。** 选择并执行适用于输入的 checks，产出检查事实报告；报告本身不裁定 Task 是否可信或获批。
- **交付验收。** 核对 Task 证据的充分性、独立性、当前性、依赖与批准；需要独立验证时复用质量检查能力，不另建检查引擎。
- **工程协作与运行支撑。** 提供工作包、身份、工具链与隔离资源，使检查和验收按边界执行；支撑能力不是先后阶段。

开发反馈用于尽早修错，任务验收需要独立证据，正式发行针对确定候选取得发布资格。操作入口见[编码后执行门禁](../scenarios/run-repository-gates.md)。

## 1.2. 核心文件目录

F 编号只绑定一个真实实现、机器配置或人类规则文件；目录节点用于定位，所有叶子均为真实文件。下图覆盖开发、验证、验收和发行交接的 20 个核心文件，不是全仓脚本清单。具体命令和类型见[脚本索引](../reference/scripts.md)。

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
title 目录视角<U+FF1A>叶子节点是实际文件

+[#E2E8F0] D0 LexiFlow 工程主干文件
++[#DBEAFE] D1 scripts<U+002F>
+++[#DBEAFE] D2 verification<U+002F>
++++[#DBEAFE] F03 development<U+002E>py
++++[#DBEAFE] F04 delivery<U+005F>hook<U+002E>py
++++[#DBEAFE] F05 diagnose<U+002E>py
++++[#DBEAFE] F06 ci<U+002E>py
++++[#DBEAFE] F07 scenarios<U+002E>py
+++[#DBEAFE] D3 delivery<U+005F>gate<U+002F>
++++[#DBEAFE] F08 <U+005F><U+005F>main<U+005F><U+005F><U+002E>py
++++[#DBEAFE] F09 submit<U+002E>py
++++[#DBEAFE] F10 validate<U+002E>py
++++[#DBEAFE] F11 review<U+002E>py
++++[#DBEAFE] F12 check<U+002E>py
+++[#DBEAFE] D4 repository<U+002F>
++++[#DBEAFE] F13 docs<U+005F>check<U+002E>py
++++[#DBEAFE] F14 python<U+005F>quality<U+002E>py
++++[#DBEAFE] F15 planning<U+005F>check<U+002E>py
+++[#DBEAFE] D5 environment<U+002F>
++++[#DBEAFE] F17 java<U+005F>exec<U+002E>py
++++[#DBEAFE] F18 start<U+005F>api<U+002E>py
++++[#DBEAFE] F19 test<U+005F>services<U+002E>py
+++[#DBEAFE] F02 check<U+005F>repository<U+002E>py
++[#D1FAE5] D6 harness<U+002F>
+++[#D1FAE5] F22 agent<U+002D>policy<U+002E>md
++[#D1FAE5] D9 config<U+002F>engineering<U+002F>
+++[#D1FAE5] F20 module<U+002D>checks<U+002E>yaml
++[#FEF3C7] D7 ops<U+002F>
+++[#FEF3C7] D8 release<U+002F>
++++[#FEF3C7] F23 workflow<U+002E>mjs
@endmindmap
```

文件编号对应的完整唯一映射见[文件编号索引](../reference/scripts.md#16-文件编号索引)；图中编号与索引保持一致。

## 1.3. 生命周期：同一文件编号贯穿使用

下图从准备与实现走到任务交接；F 编号与目录图指向同一文件。只需开发反馈时可在直接自检后交接，后续图示用于需要开发报告的任务。完整验证和正式验收按本次要求进入，正式发行在交接处分流，不串成每次必跑的长链。

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName SansSerif
skinparam defaultFontSize 14
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 40
skinparam activityBackgroundColor #EAF2FF
skinparam activityBorderColor #46699A
skinparam noteBackgroundColor #F5F6F8
skinparam noteBorderColor #A0AAB8
title 生命周期视角：同一文件编号挂接开发阶段
start
:S1 确认开发环境;
note right
输入：初次接手或既有开发环境
动作：[F17] java_exec.py 核对原生Java执行
      按需用 [F18] start_api.py 本机调试
输出：可用的开发环境
end note
:S2 定义变更任务;
note right
输入：开发目标与 [F22] agent-policy.md
动作：人工定界；改Catalog时按需核对
      [F15] planning_check.py
输出：任务定义与工作边界
end note
:S3 实现并直接自检;
note right
输入：任务与实现修改
动作：按所改内容选择直接检查
      [F13] docs_check.py / [F14] python_quality.py
      Java 经 [F17] java_exec.py 调用 Gradle
输出：修改与直接反馈
end note
if (直接自检通过？) then (通过)
else (失败：F05 diagnose.py 定位后回 S3)
stop
endif
:S4 取得开发报告;
note right
输入：停止相关写入的修改
动作：[F03] development.py（CLI）
      或 [F04] delivery_hook.py（Stop适配器）
输出：绑定本次输入的开发报告
end note
note left
内部共用链：
[F20] config/engineering/module-checks.yaml
→ [F07] scenarios.py
Stop资源：
[F19] test_services.py（内部库）
end note
if (开发检查通过？) then (通过)
else (失败或阻断：F05 diagnose.py 定位)
stop
endif
if (本次明确要求完整验证？) then (是)
:S5 执行完整验证;
note right
输入：本次明确要求的完整验证范围
动作：[F02] check_repository.py
输出：相应范围的完整报告
end note
if (完整验证通过？) then (通过)
else (失败或阻断：F05 diagnose.py 定位)
stop
endif
else (否)
endif
if (本次要求正式 Task 验收？) then (是)
:S6 送验并冻结输入;
note right
输入：Task 与当前有效开发报告
动作：[F08] ""~_~_main~_~_.py"" 分派 submit
      → [F09] submit.py（内部API）
输出：冻结的送验记录
end note
if (送验条件满足？) then (满足)
else (不足：补充当前送验输入)
stop
endif
:S7 取得独立验证;
note right
输入：冻结计划与未参与实现的验证子代理
动作：[F08] ""~_~_main~_~_.py"" 分派 validate
      → [F10] validate.py → [F07] scenarios.py
输出：独立验证证据
end note
if (独立验证通过？) then (通过)
else (失败或阻断：修复后重新送验)
stop
endif
if (验收计划要求独立审查？) then (是)
:S8 完成独立审查;
note right
输入：冻结修改与验证证据
动作：[F08] ""~_~_main~_~_.py"" 分派 review
      → [F11] review.py；只读复核
输出：独立审查意见
end note
if (审查通过？) then (通过)
else (失败或阻断：处理审查问题)
stop
endif
else (否)
endif
:S9 核对验收条件;
note right
输入：验证、适用审查、依赖与批准
动作：[F08] ""~_~_main~_~_.py"" 分派 check
      → [F12] check.py；只核对证据
输出：Task验收结果
end note
if (验收条件满足？) then (满足)
else (不足：补条件或修复)
stop
endif
else (否)
endif
:S10 交接当前成果;
note right
输入：本次要求的有效报告或验收结果
动作：授权集成后 PR 用 [F06] ci.py quick
      正式发行另进入 [F23] workflow.mjs 生命周期
输出：交接或集成成果
end note
stop
legend bottom
F编号指向目录树中的同一实际文件；S编号只表示开发阶段。
失败后用 F05 diagnose.py 按需定位；缺环境回到环境准备，修复后重进相关阶段。
F03与F04选入口，不要求重复跑；Stop与精确diagnose自动调用F19准备并清理资源。
正式命令通过F08的模块CLI进入；F09—F12是内部API，不直接执行文件。
endlegend
@enduml
```

阶段说明与深入位置如下。

| 阶段 | 输入 → 动作 → 输出 | 关联文件 |
| --- | --- | --- |
| 准备与定界 | 开发目标／环境 → 定义变更边界并准备需要的本机工具 → 可执行工作范围 | F17、F18、F22；必要时 F15 |
| 实现与直接反馈 | 任务与修改 → 按受影响部分做静态、编译或直接回归 → 修改及反馈 | F13、F14、F17；必要时产品模块原生命令 |
| 开发报告 | 停止相关写入的完整 diff → 按显式 triggers 与消费依赖选择日常 checks → 绑定本次输入的开发报告 | F03 或 F04；F20、F07；Stop 资源路径涉及 F19；失败定位 F05 |
| 可选完整验证 | 明确要求的完整范围 → Repository baseline → 完整验证报告 | F02；失败定位 F05 |
| 任务验收 | Task 与当前有效报告 → 冻结输入、独立验证、适用审查与条件核对 → Task 验收证据 | F08 分派 F09–F12；独立验证复用 F07 |
| 正式发行 | 授权集成源码与发行请求 → 准备候选、完整验证、绑定候选的独立验收及显式晋升 → 发行交接 | F23；F03／F02；F08–F12 |

失败或阻断回到相应实现、环境准备或证据补齐；相关输入变化后重新执行受影响步骤。F05 用于定位，不代替报告。开发报告不是 Task 验收；Formal review 与 check 只读冻结证据，不重跑检查。Stop 会准备并清理隔离服务，显式 development CLI 调用者则须自行准备环境。

## 1.4. 深入入口

- [质量检查体系](quality-checks.md)：检查能力和报告边界。
- [交付验收体系](delivery-acceptance.md)：Task 证据与独立性。
- [工程协作与运行支撑](engineering-support.md)：工作包、身份、环境与工具链。
- [编码后执行门禁](../scenarios/run-repository-gates.md)：按开发反馈、任务验收或发行目标选实际操作入口。
- [脚本索引](../reference/scripts.md)：公开 CLI、触发适配器、内部库及声明的完整索引。

长期人类规则只在 [Harness](../../harness/README.md) 与 [工程规则](../../AGENTS.md) 维护；本文解释模型和导航，不复制机器合同。
