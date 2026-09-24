# 1. Verify：选择、执行与报告

> 位置：[工程地图](../overview.md) → [开发交付 S2](../change-delivery.md) → Verify。前置是实际源码和检查声明；输出是 verification report，不是正式 receipt。

## 1.1. 两个入口共享一个执行内核

Change Verify 按 diff 选择 `change-targeted` 检查；Repository Verify 执行 `repository-baseline`。没有 diff 时，当前 Change 入口会选择全部声明，不会把“工作区干净”当成无需验证。

先执行变更自查，阅读选中原因、覆盖缺口和 scope_review：

```bash
python3 scripts/check_changes.py
```

再检查完整仓库基线；不读取 Task、身份或批准记录：

```bash
python3 scripts/check_repository.py
```

Change 的 base 优先使用显式 `--base`，否则取上游 merge-base，再否则取 HEAD。`--expected-path` 仅用于范围自审，不授予编辑权限。Repository 的 `--check-id` 可定向诊断，但部分选择不构成完整仓库 PASS。

## 1.2. 从阶段到子能力

```plantuml
@startuml
skinparam backgroundColor white
skinparam defaultFontName SansSerif
skinparam defaultFontSize 14
skinparam shadowing false
skinparam nodesep 40
skinparam ranksep 45
title Verify 内部：从选择到报告
start
:S1 选择检查;
:S2 冻结输入;
:S3 核对运行环境;
:S4 执行模块检查;
:S5 复核输入;
:S6 汇总并持久化;
stop
legend bottom
图示正常检查路径；缺项和失败仍进入结果报告
S3 缺环境时，该项 S4 不执行，不能返回 PASS
S2、S5 绑定同一输入；漂移不能沿用结论
endlegend
@enduml
```

冻结前选定完整检查闭包；执行前后和结束时核对输入。单个检查缺运行环境时不执行其命令，仍汇总 BLOCKED。命令失败、覆盖不足、输入漂移均不能被其他检查的成功抵消。

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
title 日常 Verify 能力到文件

+[#E2E8F0] 日常 Verify：从能力定位文件
++[#DBEAFE] 选择与编排
+++[#DBEAFE] scope.py\n选择与依赖闭包
+++[#DBEAFE] declarations.py\n检查声明合同
+++[#DBEAFE] scenarios.py\n组织冻结与执行
++[#D1FAE5] 执行与判定
+++[#D1FAE5] kernel.py\n子进程、结果、超时
+++[#D1FAE5] environment 公共 API\n运行环境诊断
+++[#D1FAE5] 模块检查入口\nGradle / npm / Python
++[#FEF3C7] 报告与读取
+++[#FEF3C7] reports.py\n校验与持久化报告
+++[#FEF3C7] verification 公共 API\n供 CLI 与 Acceptance 使用
@endmindmap
```

## 1.3. 从子能力定位文件

- [check_changes.py](../../../scripts/check_changes.py)、[check_repository.py](../../../scripts/check_repository.py)：薄 CLI，解析参数、调用公开场景、持久化报告；不理解测试类。
- [declarations.py](../../../scripts/verification/declarations.py)：加载 module-checks，验证声明与 result contract；不执行模块。
- [scope.py](../../../scripts/verification/scope.py)：diff、triggers、依赖闭包与覆盖。要问“为何这个文件触发另一个模块”，从这里查。
- [scenarios.py](../../../scripts/verification/scenarios.py)：组织选择、冻结、执行和最终复核。它负责顺序，不替模块编写检查规则。
- [Environment API](../../../scripts/environment/__init__.py)：诊断必需运行环境及受控子进程变量，不安装依赖。
- [kernel.py](../../../scripts/verification/kernel.py)：进程、超时、输出 artifact 与 result contract。Java 内容只由 Gradle 检查。
- [reports.py](../../../scripts/verification/reports.py)：报告完整性与不可变持久化。外部从 [public API](../../../scripts/verification/__init__.py) 读取，而不是信任手写 PASS JSON。

完整内部职责表见[Scripts Reference](../reference/scripts.md)。模块的直接测试见 `tests/verification/`；它们证明引擎行为，不代表所有产品检查都已运行。

## 1.4. 去重、结果和交接

同次验证中，命令、配置及输入闭包相同的检查可以只执行一次，结果映射到相应 check ID；不能仅按命令字符串去重，也不能复用旧报告冒充独立验证。

报告在 ignored `tmp/quality/verification-reports/`。`PASS` 只证明该报告冻结的输入；必需环境缺失是 `BLOCKED`，检查断言或输入冲突是 `FAIL`。未执行、skip、零测试和缺失结果不能 PASS。具体 code 以结果字段为准，定位步骤见[排障](../troubleshooting.md)。

下一步：需要正式验收时进入 [submit](acceptance.md#12-submit冻结送验输入)，并交出可核验的 Change report；否则回到[交付主干](../change-delivery.md)完成本地自查说明。

同一能力的 baseline/change 声明应维护相同执行 contract。去重键包含 result_contract，连 completeness_guarantee 说明的漂移也会使同一命令重复执行；只比较命令文本并不充分。
