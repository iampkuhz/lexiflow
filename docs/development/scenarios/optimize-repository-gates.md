# 1. 场景：优化仓库门禁脚本

> 上级：[仓库生命周期](../overview.md)。只需运行现有检查时，进入[执行门禁场景](run-repository-gates.md)。

**目标：更快反馈，不削弱检查证明。** 本页讲定位与改动；交付执行引用共用流程。

## 1.1. 先看改进工作与交接

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName SansSerif
skinparam defaultFontSize 14
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 35
skinparam activityBackgroundColor #EAF2FF
skinparam activityBorderColor #46699A
skinparam noteBackgroundColor #F5F6F8
skinparam noteBorderColor #A0AAB8
title 优化门禁：只展开改进工作，共用执行路径
start
:S1 定位优化目标;
note right
入口：无脚本（阅读报告）
作用：定位慢节点，明确要保留的检查证明
<color:#995A00><b>预期：5–10 分钟（Agent 规划）</b></color>
end note
:S2 明确修改范围与验收;
note right
入口：无脚本（阅读规范并定义任务）
作用：确定改哪些文件及验收标准，不运行规范
<color:#995A00><b>预期：10–20 分钟（Agent 规划）</b></color>
end note
:S3 实现最小调整;
note right
入口：无统一脚本（编辑代码／测试／说明）
作用：实现最小调整，不增加规则副本
<color:#995A00><b>预期：20–60 分钟（Agent 规划）</b></color>
end note
:S4 执行直接自检;
note right
入口：scripts/repository/python_quality.py + 相关 unittest
作用：检查静态质量和直接回归，失败先修复
<color:#995A00><b>预期：1–5 分钟（机器规划）</b></color>
end note
:S5 执行 B 开发交付检查;
note right
入口：scripts/verification/delivery_hook.py [B]
作用：执行风险所需开发检查，产生开发报告
<color:#995A00><b>预期：25–30 分钟（高风险样本推估）</b></color>
end note
:S6 完成 C 正式独立验收;
note right
入口：""scripts/delivery_gate/~_~_main~_~_.py"" [C]
作用：取得独立验证、适用审查与条件核对的证据
<color:#995A00><b>预期：35–50 分钟（高风险规划）</b></color>
end note
:S7 交接成果与说明;
note right
入口：无脚本（整理交接材料）
作用：交出 diff、有效证据与受影响说明
<color:#995A00><b>预期：2–5 分钟（Agent 规划）</b></color>
end note
stop
legend bottom
本图是各阶段成功后的交接；失败先修责任节点，不继续推进。
S4 / S5 / S6 分别是直接反馈、开发风险覆盖、正式独立验收。
[B] / [C] 进入“编码后执行仓库门禁”的同名展开图。
endlegend
@enduml
```

本例修改核心门禁逻辑，预算按高风险工程路径估计；实际风险由 diff 和依赖闭包决定。失败先修对应步骤，不继续推进。

| 阶段 | 作用／产物 | 入口／说明 |
| --- | --- | --- |
| S1 定位 | 慢节点与需要保留的证明范围 | 阅读既有报告；[检查声明](../../../harness/module-checks.yaml) |
| S2 定界 | 修改文件列表、非目标与验收要求 | 人工／Agent 定义任务；[现行合同](../../../harness/agent-policy.manifest.yaml)、[OpenSpec](../../../openspec/changes/) |
| S3 实现 | 代码、测试和说明的最小 diff | [Scripts 文件职责](../reference/scripts.md) |
| S4 直接自检 | 静态质量与相关回归结果 | [python_quality.py](../../../scripts/repository/python_quality.py)、[编排直接测试](../../../tests/verification/test_lightweight_orchestration.py) |
| S5 [B] 开发检查 | 风险所需开发报告 | [delivery_hook.py](../../../scripts/verification/delivery_hook.py)；[执行页 1.3 B](run-repository-gates.md) |
| S6 [C] 正式验收 | 独立验证与适用审查的完整证据链 | [delivery_gate/__main__.py](../../../scripts/delivery_gate/__main__.py)；[执行页 1.4 C](run-repository-gates.md) |
| S7 交接 | diff、有效证据与准确说明 | 人工／Agent 整理；Git 另需[授权](../../../AGENTS.md) |

S2 只确定任务，不运行 OpenSpec 或 Harness，也不执行代码扫描。涉及 Java 的修改在 S4 选择 Gradle 直接检查；本例的 Python 改动选择静态质量与相关 `unittest`。

<a id="12-再展开-s5开发检查的执行与失败交接"></a>

## 1.2. S4、S5、S6 的边界

| 节点 | 检查范围 | 输出 |
| --- | --- | --- |
| S4 直接自检 | 实现者选择相关静态／编译／定点测试，尽早修错 | 直接反馈，不是完整风险报告 |
| S5 [B] 开发检查 | 按真实 diff 与风险闭包执行必需检查 | 开发报告，不是正式 validation |
| S6 [C] 正式验收 | 独立执行冻结计划；review 只读审查；check 核证据 | 正式验收链，不等于已获 Git／发布许可 |

涉及 Hook 行为时选择[对应直接测试](../../../tests/verification/test_delivery_hook.py)，不机械叠跑整个 adapter、development 和 Hook。

<a id="13-再展开-s6独立验证只读审查条件核对"></a>

## 1.3. 共用执行与设计问题

S5 [B]、S6 [C] 分别进入[执行页](run-repository-gates.md)的 1.3、1.4。CLI 参数留在[能力说明](../change-delivery/delivery-gate.md)；重复覆盖、环境职责与入口设计留在[问题台账](../../roadmap/master-plan/phase2/status.md)，本页不自行改变规则。

<a id="14-怎样读预期预算怎样维护这个场景"></a>

## 1.4. 维护定位

| 修改主题 | 规则真源 | 实现／直接测试 |
| --- | --- | --- |
| 检查选择、输入与复用 | [module-checks](../../../harness/module-checks.yaml)、[agent-policy](../../../harness/agent-policy.manifest.yaml) | [scope.py](../../../scripts/verification/scope.py)、[scenarios.py](../../../scripts/verification/scenarios.py)、[编排回归](../../../tests/verification/test_lightweight_orchestration.py) |
| Stop 与服务清理 | [delivery-hooks](../../../harness/delivery-hooks.json)、[test-services](../../../harness/test-services.json) | [delivery_hook.py](../../../scripts/verification/delivery_hook.py)、[test_services.py](../../../scripts/environment/test_services.py)、[Hook 回归](../../../tests/verification/test_delivery_hook.py) |
| 正式身份与证据链 | [agent-policy](../../../harness/agent-policy.manifest.yaml) | [Delivery Gate 文件](../../../scripts/delivery_gate/)、[直接测试](../../../tests/delivery_gate/) |
| CI 与发行集合 | [ci-policy](../../../harness/ci-policy.yaml) | [ci.py](../../../scripts/verification/ci.py)、[CI 回归](../../../tests/verification/test_ci.py) |

预期是规划预算；S5／S6 已包含子步骤，不能重复累加。样本与估算前提只在[执行页](run-repository-gates.md)维护。返回[总入口](../overview.md)。
