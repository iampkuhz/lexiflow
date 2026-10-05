# 1. 开发交付：从实现到可信验收

> 位置：[文档首页](../README.md) → [工程地图](overview.md) → 开发交付。先读本页建立阶段关系，再进入 Verify 或 Delivery Gate；精确参数到 Reference 查阅。

日常开发自检按真实改动风险选择检查；完整 Change/Repository Verify 是显式完整基线原语，正式 **Delivery Gate** 回答“同一冻结输入是否经过独立验证、必要审查及依赖/批准核对”。它们复用检查能力，不共享身份结论。一次 Agent 执行完成不等于验收通过。

## 1.1. 先看交付主干

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName SansSerif
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 45
skinparam activityBackgroundColor #EAF2FF
skinparam activityBorderColor #46699A
skinparam defaultFontSize 15
title 风险驱动开发与独立验收
start
:S1 完成实现;
:S2 风险评估与开发自检;
if (需要正式验收？) then (是)
  :S3 submit 并冻结输入;
  :S4 独立 validate;
  if (计划要求独立 review？) then (是)
    :S5 按计划独立 review;
  else (否)
  endif
  :S6 check 依赖与批准;
else (否)
  stop
endif
stop
legend bottom
S2 日常自检：机械/局部为 development-change；高风险另加 development-baseline
正式路径都必须独立 validate；仅 high-risk-engineering/formal-release 要求 review
Formal release 由 Task required_check_ids 命中 CI formal-check 集合触发完整 repository-baseline
自检 PASS 不是 Formal PASS；完整 Change/Repository Verify 仍可显式运行
箭头表示人工交接；不需要 Formal 时止于 S2；无 review 时 S4 直接到 S6
endlegend
@enduml
```

图中的箭头是交接顺序，不表示前一个 CLI 自动调用下一个。日常工作不需要正式验收时，止于本地自查；本地检查失败则停在对应问题，不能把“结束”理解成 PASS。正式链的后续操作以当前步骤满足要求为前提。

| 阶段 | 要解决的问题 | 入口与交接 | 深入阅读 |
| --- | --- | --- | --- |
| S1 实现 | 谁负责本次范围？ | 人工或 Agent 产出代码与真实执行事实 | [可选 Agent workflow](agent-workflow.md) |
| S2 本地 Verify | 真实改动风险要求哪些检查？ | development report；完整基线原语按需显式运行 | [选择、执行与报告](change-delivery/verification.md) |
| S3 submit | 哪些输入与哪个 producer 被送验？ | submission | [正式 Delivery Gate](change-delivery/delivery-gate.md#12-submit冻结送验输入) |
| S4 validate | 独立身份是否执行了相同输入的检查？ | validation record | [独立验证](change-delivery/delivery-gate.md#13-validate执行独立验证) |
| S5 review | 风险计划是否要求独立审查？ | 高风险/发行发布 review record；低风险不适用 | [独立审查](change-delivery/delivery-gate.md#14-review复核差异与证据) |
| S6 check | receipt、依赖和用户批准是否满足？ | check record | [条件核对](change-delivery/delivery-gate.md#15-check核对条件而非重跑) |

## 1.2. 哪些不是主干阶段

文档链接、格式和 policy projection 是模块检查里的 **standalone checker**，无需把每项都画成新的阶段。缺环境、身份冲突、输入漂移和缺批准会阻止交接，因此必须从主干进入[排障页](troubleshooting.md)。

`status` 是已有 submission 的观察入口，不是 S7。Hook 安装和 skill 链接是有副作用的本机维护，不是编辑或提交的许可步骤。日常 Verify 不读取 Task identity，不签发正式 receipt，也不阻断 commit。

## 1.3. 从本次工作开始

先确认最终 diff、他人修改与本次负责范围，不在编辑前预锁文件。日常完成时运行风险驱动开发入口并阅读风险/范围结论；只有需要完整 Change/Repository 基线或正式发行时才运行对应完整入口。自检不能代替正式独立验证。

完整原生检查被聚合入口包含时，不再机械补跑每个子任务。正式 validate 由独立身份执行；机械/局部风险无需 review，高风险工程与正式发行要求独立 review。review/check 不运行交付命令。

下一步：进入 [Verify](change-delivery/verification.md)。需要准备环境时进入[隔离验证环境](operations/verification-environment.md)；只查某个工具时进入 [Reference](reference.md)。
