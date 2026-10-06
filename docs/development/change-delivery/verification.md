# 1. Verify：选择、执行与报告

> 位置：[工程地图](../overview.md) → [开发交付 S2](../change-delivery.md) → Verify。前置是实际源码和检查声明；输出是 verification report，不是正式 receipt。

## 1.1. 风险驱动的开发入口与完整基线

公开日常开发入口按真实 diff、文件内容和闭包自动评估风险，不接受 root、base、check ID 或风险覆盖参数：

```bash
python3 -m scripts.verification.development
```

机械或局部功能改动执行 `development-change`（按变更触发检查）；高风险工程改动额外执行 `development-baseline`。分类取决于真实 diff/闭包和内容，不信任 Task 名称。高风险不等于正式发行验证。

完整 Change/Repository Verify 原语仍供显式完整基线与发行 CI 使用：Change Verify 按 diff 选择 `change-targeted` 检查；Repository Verify 执行 `repository-baseline`。它们不是每项日常开发的无条件要求。无 diff 时，Change Verify 会选择全部声明，不把“工作区干净”当成无需验证。

需要显式完整 Change 自查时，阅读选中原因、覆盖缺口和 scope_review：

```bash
python3 scripts/check_changes.py
```

再检查完整仓库基线；不读取 Task、身份或批准记录：

```bash
python3 scripts/check_repository.py
```

比较基线与风险/检查闭包由开发入口固定并冻结。定向诊断只用于定位；开发自检、完整 Verify 与正式 Gate 的 PASS 各自绑定不同输入和证明范围，不可互代。

### 1.1.1. 日常功能研发不等待正式发行

执行边界以 [Harness](../../../harness/README.md) 为准。功能开发阶段先取得相关模块反馈，不必先完成 ZIP、镜像、双架构发布或真实资料分发验收；源码启动与扩展加载见[本地体验](../operations/local-experience.md)。

开发入口自动推导所需范围；需要定位单个模块问题时，可使用诊断入口：

```bash
# 后端完整模块检查；仍需要 Java 25 和隔离 PostgreSQL/Redis 测试环境。
.local/lexiflow-python/bin/python -m scripts.verification.diagnose repository --check-id eng.backend.delivery

# 扩展质量检查；声明依赖会同时纳入后端检查，不是仅做前端单测。
.local/lexiflow-python/bin/python -m scripts.verification.diagnose repository --check-id eng.extension.quality
```

测试环境按[验证环境](../operations/verification-environment.md)准备，不能接入真实用户数据库。诊断不会自动安装依赖，也不会因为缺少环境而跳过所选模块的必需检查。

诊断仅用于定位，不证明发布可用；报告须说明实际范围，不可作为完整 Verify 报告。正式发行检查由 Task `required_check_ids` 命中 `harness/ci-policy.yaml` formal check 集合触发，并仍执行完整 Repository baseline。

### 1.1.2. 实现者直接自检

只先运行与本次修改相关的静态检查和直接回归，不把完整 adapter、development 和 Stop Hook 惯例性地叠加。检查按实际改动选择；跨模块可运行多类检查，任何失败先修复，不交出虚假的通过结论。

| 检查 | 核心作用 | 入口链接 |
| --- | --- | --- |
| 文档 | 文档规则与引用静态检查 | `python3 -m scripts.repository.docs_check`；[docs_check.py](../../../scripts/repository/docs_check.py) |
| Python | Ruff／Pylint 与所改模块直接回归 | `python3 -m scripts.repository.python_quality`、`python3 -m unittest <测试模块>`；[python_quality.py](../../../scripts/repository/python_quality.py) |
| Java | Java 25 下相关 Gradle 定点测试 | `python3 -m scripts.environment.java_exec backend/gradlew -p backend <Test> --tests <测试类>`；[Java 检查](../reference/java-checks.md) |
| 扩展 | 先构建，再跑相关 Node 直接回归 | `npm --prefix extension run build`、`node --test extension/tests/<测试>.mjs`；[package.json](../../../extension/package.json) |
| 差异 | 检查本次差异空白，不 stage／commit | `git diff --check` |

这不是新的自动执行器；Java Test 路径和测试类由实际修改决定。自检不签发 receipt。需要交付时再进入[Stop 交付能力](hooks.md)，不要拿直接测试替代风险要求的完整检查。

## 1.2. 从阶段到子能力

| Verify 能力 | 核心作用 | 入口链接 |
| --- | --- | --- |
| Change Verify | 按 diff 选择 `change-targeted` 完整变化闭包；无 diff 时选择全部声明 | `python3 scripts/check_changes.py`；[check_changes.py](../../../scripts/check_changes.py) |
| Repository Verify | 执行全部 `repository-baseline`，不读取 Task、身份或批准 | `python3 scripts/check_repository.py`；[check_repository.py](../../../scripts/check_repository.py) |
| 定向诊断 | 按模式或 Check ID 定位问题；输出不作完整 Verify 报告 | `python3 -m scripts.verification.diagnose change\|repository`；[diagnose.py](../../../scripts/verification/diagnose.py) |
| 冻结与执行 | 取得非等待窗口，冻结选中声明和真实输入，再逐项预检／执行 | [scenarios.py](../../../scripts/verification/scenarios.py)、[scope.py](../../../scripts/verification/scope.py)、[kernel.py](../../../scripts/verification/kernel.py) |
| 报告与摘要 | 完整入口保存报告；诊断只输出范围摘要 | [reports.py](../../../scripts/verification/reports.py)、[summaries.py](../../../scripts/verification/summaries.py) |



冻结前选定完整检查闭包；执行前后和结束时核对输入。单个检查缺运行环境时不执行其命令，仍汇总 BLOCKED。命令失败、覆盖不足、输入漂移均不能被其他检查的成功抵消。

声明源码通过仓库目录描述符逐层读取，不跟随文件或祖先目录的符号链接；特殊文件、私有配置入口及读取期间的身份变化均拒绝。文件快照绑定相对路径、内容摘要、字节数和执行位，缺失或不安全输入不能冻结为 PASS。报告消费时重新核对完整快照，不能仅保留指纹而改写文件描述。这是声明输入的安全读取合同，不证明构建的实际读取闭包、外部候选或跨主机来源已经完整绑定。

同窗多 profile 仅在输入、配置、环境、window、runner 和 context 一致时可复用；未知副作用默认不可复用，跨视图须显式 `transaction_reuse=true`。runtime transport、失败及跨交付结果不复用；自检不复用为 Formal validation，同视图 alias 保持原语义。

跨视图工具身份由共用 Environment helper 解析：绑定实际解释器、声明工具在子进程 PATH 中的解析目标、真实路径及内容摘要，而不只比较 PATH 字符串。有限能力 `python-package-yaml`、`posix-lock-tool`、`sha256-tool` 分别绑定实际 PyYAML 包、当前平台锁工具和脚本所选摘要工具。执行前后与报告消费时重新核对；工具或包字节变化、来源日志损坏、输入漂移均不能投影旧 PASS。只记录身份描述与摘要，不记录秘密环境值；这不等于任意外部服务或所有操作系统状态的认证。

声明工具与冻结源码必须覆盖测试实际读取；合成 fixture 的私有输出若只在同一检查内消费，并不天然要求每个视图重跑。真实服务、浏览器安装或未完整绑定的质量工具依赖保持不可复用，不能仅凭测试名称放行。

### 1.2.1. 共用检查执行链与耗时

日常开发、完整 Verify、Formal validation 和 CI 复用这些能力，但选择范围和身份结论不同。先由真实 `selection` 过滤节点，再按依赖闭包执行；本图不是要求每次把全部节点都跑完。子编号仅分组，不表示父 Check 调用子 Check。

时间取自本机 Apple Silicon、项目 Python／Java 25 环境中的一个完整成功开发批次（`development-change` 和 `development-baseline`；报告 ID `00fd7d32-8132-4bc1-adca-9a104e804f58`、`3ebc011b-a56b-4317-bf5e-97a2530d1c10`）。它们是单次参考，不是稳定区间、CI 预期或本次输入的验收；环境准备、下载、人工等待另计。图中淡橙只强调分钟级耗时，不表示 FAIL。

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
:S2.1 Policy 投影检查;
note right
入口：scripts/repository/quality.py
作用：共享规则投影
<color:#995A00><b>预期：约 0.3 秒（单次样本参考）</b></color>
end note
:S2.2 Planning 合同检查;
note right
入口：scripts/repository/planning_check.py
作用：Catalog 与任务合同
<color:#995A00><b>预期：约 6 秒（单次样本参考）</b></color>
end note
:S2.3 Hook 配置检查;
note right
入口：scripts/repository/hooks.py
作用：客户端入口与 Hook 资产
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
入口：ops/release/manifest-check.mjs
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
入口：ops/release/runtime-entry-check.mjs
作用：发行运行入口负例
<color:#995A00><b>预期：约 26 秒（单次样本参考）</b></color>
end note
:S9.1 安装生命周期合同;
note right
入口：ops/release/lifecycle-install-check.mjs
作用：首装与安装恢复 fixture
<color:#995A00><b>预期：约 14 秒（单次样本参考）</b></color>
end note
:S9.2 生命周期合同;
note right #FFF0D6
入口：ops/release/lifecycle-check.mjs
作用：升级与资源生命周期 fixture
<color:#995A00><b>预期：约 1 分 54 秒（单次样本参考）</b></color>
end note
:S10 发行构建合同;
note right #FFF0D6
入口：ops/release/build-check.mjs
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
| S2.1 | 确定性投影 | `python3 -m scripts.repository.quality --mode policy`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 0.3 秒 |
| S2.2 | Catalog 合同 | `python3 -m scripts.repository.quality --mode planning`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 6 秒 |
| S2.3 | Hook 入口资产 | `python3 -m scripts.repository.quality --mode hooks`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 0.1 秒 |
| S2.4 | scripts 静态质量 | `python3 -m scripts.repository.quality --mode python`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 3 秒 |
| S3 | Java 完整模块检查 | [scripts/environment/java_exec.py](../../../scripts/environment/java_exec.py) → `backend/gradlew deliveryFull`；[Java 检查](../reference/java-checks.md)；单次约 59 秒（含聚合子任务） |
| S4 | 构建模型与前置故障回归 | [tests/verification/gradle_build_contract.py](../../../tests/verification/gradle_build_contract.py)；单次约 14 秒 |
| S5 | 验证器自身回归 | [tests/verification/module_check_adapter.py](../../../tests/verification/module_check_adapter.py)；单次约 5 分 28 秒 |
| S5.1 | 派发／身份／结果合同 | [scripts/agents/module_tests.py](../../../scripts/agents/module_tests.py)；单次约 3 秒 |
| S5.2 | 验收证据链回归 | [scripts/delivery_gate/quality.py](../../../scripts/delivery_gate/quality.py)；单次约 2 分 36 秒 |
| S5.3 | 仓库工具回归 | `python3 -m scripts.repository.quality --mode tests`；入口 [quality.py](../../../scripts/repository/quality.py)；样本约 17 秒 |
| S6 | 构建、单测、真实跨端检查 | `node extension/scripts/quality-check.mjs`；单次约 56 秒（含子步骤） |
| S7 | 清单与制品身份 | `node ops/release/manifest-check.mjs`；单次约 58 秒 |
| S8 | 部署、取消、归属与恢复回归 | [ops/docker/tests/module_check_adapter.py](../../../ops/docker/tests/module_check_adapter.py)；单次约 6 分 46 秒（含 Python／Node 套件） |
| S9 | 运行入口负例 | `node ops/release/runtime-entry-check.mjs`；单次约 26 秒 |
| S9.1 | 首装 fixture | `node ops/release/lifecycle-install-check.mjs`；单次约 14 秒 |
| S9.2 | 升级／资源 fixture | `node ops/release/lifecycle-check.mjs`；单次约 1 分 54 秒 |
| S10 | 构建、制品与候选 fixture | `node ops/release/build-check.mjs`；单次约 2 分 2 秒（非发行总时间） |
| S10.1 | 真实源码发行生命周期 | [scripts/environment/release_runtime_check.py](../../../scripts/environment/release_runtime_check.py)；Formal only，预留 20 分钟（规划，未测） |
| S10.2 | 真实候选首装、升级、恢复 | [scripts/environment/candidate_runtime_check.py](../../../scripts/environment/candidate_runtime_check.py)；Formal only，预留 40 分钟（规划，未测） |



计算一次交付的等待时间，应以实际物理执行及窗口为准；同事务复用不再次累加，父入口与子节点不相加，不跨交付复用旧 PASS。声明或实现变化后须重新判断样本适用性，不把节点样本总和当作所有场景的预计完成时间。


## 1.3. 从子能力定位文件

- [check_changes.py](../../../scripts/check_changes.py)、[check_repository.py](../../../scripts/check_repository.py)：薄 CLI，解析参数、调用公开场景、持久化报告；不理解测试类。
- [declarations.py](../../../scripts/verification/declarations.py)：加载 module-checks，验证声明与 result contract；不执行模块。
- [scope.py](../../../scripts/verification/scope.py)：diff、triggers、依赖闭包与覆盖。要问“为何这个文件触发另一个模块”，从这里查。
- [scenarios.py](../../../scripts/verification/scenarios.py)：组织选择、冻结、执行和最终复核。它负责顺序，不替模块编写检查规则。
- [Environment API](../../../scripts/environment/__init__.py)：诊断必需运行环境及受控子进程变量，不安装依赖。
- [kernel.py](../../../scripts/verification/kernel.py)：进程、超时、输出 artifact 与 result contract。Java 内容只由 Gradle 检查。
- [input_snapshot.py](../../../scripts/verification/input_snapshot.py)：以不跟随链接的目录描述符读取声明源码，绑定字节和执行位并拒绝读取竞态；由既有内核调用，不是独立 Gate 或公开命令。
- [reports.py](../../../scripts/verification/reports.py)：报告完整性与不可变持久化。外部从 [public API](../../../scripts/verification/__init__.py) 读取，而不是信任手写 PASS JSON。

完整内部职责表见[Scripts Reference](../reference/scripts.md)。模块的直接测试见 `tests/verification/`；它们证明引擎行为，不代表所有产品检查都已运行。

## 1.4. 去重、结果和交接

同次验证中，命令、配置及输入闭包相同的检查可以只执行一次，结果映射到相应 check ID；不能仅按命令字符串去重，也不能复用旧报告冒充独立验证。

报告在 ignored `tmp/quality/verification-reports/`。`PASS` 只证明该报告冻结的输入；必需环境缺失是 `BLOCKED`，检查断言或输入冲突是 `FAIL`。未执行、skip、零测试和缺失结果不能 PASS。具体 code 以结果字段为准，定位步骤见[排障](../troubleshooting.md)。

下一步：需要正式验收时进入 [submit](delivery-gate.md)，并交出风险绑定的开发验证报告；否则回到[交付主干](../change-delivery.md)记录实际自查范围。

baseline/change 声明须保持相同执行 contract；去重键含 result_contract，故 completeness_guarantee 漂移也会触发重跑，不能只比较命令文本。

## 1.5. 交付触发与语言检查

交付使用风险驱动的开发入口或客户端 Stop；完整 Verify 只在显式要求时运行，不在每次工具调用后重复扫描。backend 变更选择 Gradle deliveryFull，保留 Spotless、Checkstyle、PMD、Java source/Javadoc、架构与业务测试；scripts 变更选择 Ruff、Pylint 中文 docstring 检查和模块测试。规则真源分别是 [python-quality.toml](../../../harness/python-quality.toml) 与 [python-docstrings.toml](../../../harness/python-docstrings.toml)，工具与环境固定在 requirements-dev。

交付 Hook 自动执行风险所需 profiles 并返回真实结果，但不替代检查声明、结果语义约束或独立正式验收；启用、阶段边界与恢复方式见[交付 Hook](hooks.md)。
