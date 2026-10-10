# 1. 共用 Check 清单

> 位置：[Verify](../verification.md) → 共用 Check。此清单解释单项实现入口、作用与样本预算；实际触发、范围、依赖、输入、环境及结果合同以 [module-checks.yaml](../../../harness/module-checks.yaml) 为准。它不是要求每次运行全部节点的顺序清单。

## 1.1. 执行链与节点

日常开发、完整 Verify、Formal validation 和 CI 复用同一检查能力，但选择范围、冻结输入和身份结论不同。先由真实 `selection` 过滤节点，再按依赖闭包执行；子编号只分组显示，不代表父 Check 自动调用子 Check。

时间来自本机 Apple Silicon、项目 Python／Java 25 环境中的一个完整成功开发批次（`development-change` 和 `development-baseline`；报告 ID `00fd7d32-8132-4bc1-adca-9a104e804f58`、`3ebc011b-a56b-4317-bf5e-97a2530d1c10`）。它们是单次参考，不是稳定区间、CI 预期或本次输入的验收；环境准备、下载、人工等待另计。淡橙只强调分钟级耗时，不表示 FAIL。

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName SansSerif
skinparam defaultFontSize 14
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 45
skinparam activityBackgroundColor #EAF2FF
skinparam activityBorderColor #46699A
skinparam noteBackgroundColor #F3F4F6
skinparam noteBorderColor #94A3B8
title 共用 Check：声明顺序与主要耗时
start
:S1 版本合同检查;
note right
入口：ops/release/check.mjs
作用：版本与构建身份
<color:#995A00><b>预期：约 9 秒（单次样本参考）</b></color>
end note
:S2 文档规则检查;
note right
入口：scripts/repository/quality.py
作用：文档结构与引用
<color:#995A00><b>预期：约 0.2 秒（单次样本参考）</b></color>
end note
:<U+0053>2<U+002E>1 Policy 合同检查;
note right
入口：scripts/repository/quality.py
作用<U+FF1A>唯一策略结构与身份边界
<color:#995A00><b>预期：约 0.3 秒（单次样本参考）</b></color>
end note
:S2.2 Planning 合同检查;
note right
入口：scripts/repository/planning_check.py
作用：Catalog 与任务合同
<color:#995A00><b>预期：约 6 秒（单次样本参考）</b></color>
end note
:S2.3 Git Hook 资产检查;
note right
入口：scripts/repository/quality.py --mode hooks
作用：Git 提醒脚本存在且可执行
<color:#995A00><b>预期：约 0.1 秒（单次样本参考）</b></color>
end note
:S2.4 Python 规则检查;
note right
入口：scripts/repository/python_quality.py
作用：Ruff / Pylint 静态质量
<color:#995A00><b>预期：约 3 秒（单次样本参考）</b></color>
end note
:S3 Java 完整交付;
note right
入口：scripts/environment/java_exec.py → backend/gradlew deliveryFull
作用：静态质量、架构与业务测试
<color:#995A00><b>预期：约 59 秒（单次样本参考）</b></color>
end note
:S4 Gradle 构建合同;
note right
入口：tests/verification/gradle_build_contract.py
作用：真实构建模型与故障前置回归
<color:#995A00><b>预期：约 14 秒（单次样本参考）</b></color>
end note
:S5 Verification 自检;
note right #FFF0D6
入口：tests/verification/module_check_adapter.py
作用：验证框架直接回归
<color:#995A00><b>预期：约 5 分 28 秒（单次样本参考）</b></color>
end note
:S5.1 Agent 工具自检;
note right
入口：scripts/agents/module_tests.py
作用：派发、身份与事实记录
<color:#995A00><b>预期：约 3 秒（单次样本参考）</b></color>
end note
:S5.2 Delivery Gate 自检;
note right #FFF0D6
入口：scripts/delivery_gate/quality.py
作用：验收证据链回归
<color:#995A00><b>预期：约 2 分 36 秒（单次样本参考）</b></color>
end note
:S5.3 Repository 工具自检;
note right
入口：scripts/repository/quality.py
作用：仓库工具直接回归
<color:#995A00><b>预期：约 17 秒（单次样本参考）</b></color>
end note
:S6 扩展完整质量;
note right
入口：extension/scripts/quality-check.mjs
作用：构建、单测与浏览器跨端检查
<color:#995A00><b>预期：约 56 秒（单次样本参考）</b></color>
end note
:S7 制品清单合同;
note right
入口<U+FF1A>ops<U+002F>release<U+002F>check<U+002E>mjs <U+002D><U+002D>suite manifest
作用：清单与制品身份
<color:#995A00><b>预期：约 58 秒（单次样本参考）</b></color>
end note
:S8 部署合同检查;
note right #FFF0D6
入口：ops/docker/tests/module_check_adapter.py
作用：部署、归属、取消与恢复
<color:#995A00><b>预期：约 6 分 46 秒（单次样本参考）</b></color>
end note
:S9 运行入口合同;
note right
入口<U+FF1A>ops<U+002F>release<U+002F>check<U+002E>mjs <U+002D><U+002D>suite runtime<U+002D>entry
作用：发行运行入口负例
<color:#995A00><b>预期：约 26 秒（单次样本参考）</b></color>
end note
:S9.1 安装生命周期合同;
note right
入口<U+FF1A>ops<U+002F>release<U+002F>check<U+002E>mjs <U+002D><U+002D>suite lifecycle<U+002D>install
作用：首装与安装恢复 fixture
<color:#995A00><b>预期：约 14 秒（单次样本参考）</b></color>
end note
:S9.2 生命周期合同;
note right #FFF0D6
入口<U+FF1A>ops<U+002F>release<U+002F>check<U+002E>mjs <U+002D><U+002D>suite lifecycle
作用：升级与资源生命周期 fixture
<color:#995A00><b>预期：约 1 分 54 秒（单次样本参考）</b></color>
end note
:S10 发行构建合同;
note right #FFF0D6
入口<U+FF1A>ops<U+002F>release<U+002F>check<U+002E>mjs <U+002D><U+002D>suite build
作用：构建、制品与候选合同
<color:#995A00><b>预期：约 2 分 2 秒（单次样本参考）</b></color>
end note
:S10.1 源码发行真实运行;
note right
入口：scripts/environment/release_runtime_check.py
作用：真实源码发行生命周期
<color:#995A00><b>预期：20 分钟（规划预留，未测）</b></color>
end note
:S10.2 候选制品真实运行;
note right
入口：scripts/environment/candidate_runtime_check.py
作用：真实候选首装、升级与恢复
<color:#995A00><b>预期：40 分钟（规划预留，未测）</b></color>
end note
stop
legend bottom
先按真实 selection 过滤本图节点，再按依赖闭包执行。
只有 PASS 才沿后续实线继续；交付失败时停止并记录 BLOCKED。
子编号只分组显示，不表示父 Check 自动调用子 Check。
除 S10.1／S10.2 的未测规划预留，其余是单次成功样本参考。
规划预留参考 Registry 20／40 分钟上限；非实测或完成时间保证。
endlegend
@enduml
```

图与表列出实现文件、作用及预期参考；公开执行命令以表为准，内部实现文件不是要求额外运行的 CLI。S10.1／S10.2 尚无完整成功实测，各预留 20／40 分钟，参考 Registry 单项上限；这是规划预算，不保证实际可在预算内完成。其他节点使用单次样本参考；真正的触发路径、依赖、输入、环境和结果合同仍由 [module-checks.yaml](../../../harness/module-checks.yaml) 维护，表格不定义第二套规则。

| 节点 | 核心作用 | 入口链接／样本 |
| --- | --- | --- |
| S1 | 版本与身份合同 | `node ops/release/check.mjs`；[check.mjs](../../../ops/release/check.mjs)；单次约 9 秒 |
| S2 | 文档结构、链接与规则 | `python3 -m scripts.repository.quality --mode docs`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 0.2 秒 |
| S2.1 | 唯一策略结构与身份边界 | `python3 -m scripts.repository.quality --mode policy`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 0.3 秒 |
| S2.2 | Catalog 合同 | `python3 -m scripts.repository.quality --mode planning`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 6 秒 |
| S2.3 | Git Hook 资产存在且可执行 | `python3 -m scripts.repository.quality --mode hooks`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 0.1 秒 |
| S2.4 | scripts 静态质量 | `python3 -m scripts.repository.quality --mode python`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 3 秒 |
| S3 | Java 完整模块检查 | [scripts/environment/java_exec.py](../../../scripts/environment/java_exec.py) → `backend/gradlew deliveryFull`；[Java 检查](../java-checks.md)；单次约 59 秒（含聚合子任务） |
| S4 | 构建模型与前置故障回归 | [tests/verification/gradle_build_contract.py](../../../tests/verification/gradle_build_contract.py)；单次约 14 秒 |
| S5 | 验证器自身回归 | [tests/verification/module_check_adapter.py](../../../tests/verification/module_check_adapter.py)；单次约 5 分 28 秒 |
| S5.1 | 派发／身份／结果合同 | [scripts/agents/module_tests.py](../../../scripts/agents/module_tests.py)；单次约 3 秒 |
| S5.2 | 验收证据链回归 | [scripts/delivery_gate/quality.py](../../../scripts/delivery_gate/quality.py)；单次约 2 分 36 秒 |
| S5.3 | 仓库工具回归 | `python3 -m scripts.repository.quality --mode tests`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 17 秒 |
| S6 | 构建、单测、真实跨端检查 | `node extension/scripts/quality-check.mjs`；单次约 56 秒（含子步骤） |
| S7 | 清单与制品身份 | `node ops/release/check.mjs --suite manifest`；单次约 58 秒 |
| S8 | 部署、取消、归属与恢复回归 | [ops/docker/tests/module_check_adapter.py](../../../ops/docker/tests/module_check_adapter.py)；单次约 6 分 46 秒（含 Python／Node 套件） |
| S9 | 运行入口负例 | `node ops/release/check.mjs --suite runtime-entry`；单次约 26 秒 |
| S9.1 | 首装 fixture | `node ops/release/check.mjs --suite lifecycle-install`；单次约 14 秒 |
| S9.2 | 升级／资源 fixture | `node ops/release/check.mjs --suite lifecycle`；单次约 1 分 54 秒 |
| S10 | 构建、制品与候选 fixture | `node ops/release/check.mjs --suite build`；单次约 2 分 2 秒（非发行总时间） |
| S10.1 | 真实源码发行生命周期 | [scripts/environment/release_runtime_check.py](../../../scripts/environment/release_runtime_check.py)；Formal only，预留 20 分钟（规划，未测） |
| S10.2 | 真实候选首装、升级、恢复 | [scripts/environment/candidate_runtime_check.py](../../../scripts/environment/candidate_runtime_check.py)；Formal only，预留 40 分钟（规划，未测） |



## 1.2. 预算与边界

计算一次交付等待时间，以实际物理执行及窗口为准；同事务复用不再次累加，父入口与子节点不相加，不跨交付复用旧 PASS。声明或实现变化后须重新判断样本适用性，节点样本总和不代表所有场景的预计完成时间。

S2.3 仅核对受管 Git 提醒脚本存在且可执行，不检查客户端是否加载或信任 Stop 配置。Codex／Qoder 配置与协议回归属于 [Verification 工具测试](../../../tests/verification/test_delivery_hook.py)；真实宿主加载仍按 [Hook 接入说明](../hooks.md)确认，不能由资产检查 PASS 推断。
