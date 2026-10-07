# 1. 场景：编码后执行仓库门禁

> 上级：[仓库生命周期](../overview.md)。前置：实现基本完成、修改范围清楚、相关输入停止写入。优化门禁是[另一项任务](optimize-repository-gates.md)。

**先直接自检，再取得开发报告；需要时继续正式验收。** 不要求每次都运行全部 Verify、Hook、CI 和发行检查。

## 1.1. 先看执行与交接

执行节点的三行注释固定为：入口（文件／无脚本）、作用、预期耗时。总图 S3 [B] 和 S5 [C] 分别展开到本页 1.3、1.4。

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
title 编码后执行门禁：先判断当前需要哪一层证明
start
:S1 确认待交付输入;
note right
入口：无脚本（确认本次修改）
作用：确认 diff、交付范围与停止相关写入
<color:#995A00><b>预期：1–3 分钟（Agent 规划）</b></color>
end note
:S2 完成实现者直接自检;
note right
入口：所改模块的原生检查（见 A 表）
作用：检查相关静态／编译／直接回归，失败先修复
<color:#995A00><b>预期：1–5 分钟（轻量机器规划）</b></color>
end note
:S3 执行 B 开发交付检查;
note right
入口：scripts/verification/delivery_hook.py [B]
作用：执行开发风险检查，交出报告与清理结果
<color:#995A00><b>预期：高风险 25–30 分钟；局部 1–5 分钟</b></color>
end note
:S4 分析开发结果;
note right
入口：无脚本（读取开发报告）
作用：确认检查覆盖与真实结果，不提升为 Formal
<color:#995A00><b>预期：30–60 秒（Agent 规划）</b></color>
end note
if (开发报告完整且有效？) then (PASS)
:S5 落实 C 正式验收要求;
note right
入口：""scripts/delivery_gate/~_~_main~_~_.py"" [C]
作用：按任务要求取得正式链；不需要时不调用 C
<color:#995A00><b>预期：高风险 35–50 分钟；无需时核对 30–60 秒</b></color>
end note
if (本次要求的证据齐备？) then (齐备)
:S6 交接当前层证据;
note right
入口：无脚本（整理交接材料）
作用：交出开发报告及本次要求的正式证据
<color:#995A00><b>预期：1–2 分钟（Agent 规划）</b></color>
end note
stop
else (FAIL / BLOCKED)
:S8 交还正式验收问题;
note right
入口：无脚本（分析正式验收失败）
作用：补条件或修复；相关输入变化后重新送验
<color:#995A00><b>预期：2–5 分钟（分析规划，不含修复）</b></color>
end note
stop
endif
else (FAIL / BLOCKED)
:S7 交还开发问题;
note right
入口：无脚本（分析开发失败）
作用：将失败或缺环境交回对应修改／环境步骤
<color:#995A00><b>预期：2–5 分钟（分析规划，不含修复）</b></color>
end note
stop
endif
legend bottom
结果决定真实交接：开发是否通过、本次要求的证据是否齐备。
[B] 展开 1.3：delivery_hook.py；[C] 展开 1.4：""delivery_gate/~_~_main~_~_.py""。
不需要 Formal 时不进入 [C]；只交接本次要求的开发证据。
S2 通过才可交接到 S3；直接失败停在实现者修复，不触发后续重检查。
本图是人工 / Agent 交接，不是脚本自动串联；父节点预算包含子图。
endlegend
@enduml
```

| 图中节点 | 核心作用 | 入口／说明 |
| --- | --- | --- |
| S2 [A] 直接自检 | 实现者先修相关错误 | 下方 A 表的原生检查 |
| S3 [B] 开发检查 | 取得风险所需开发报告 | [delivery_hook.py](../../../scripts/verification/delivery_hook.py) → 1.3 [B] |
| S5 [C] 正式验收 | 需要时取得独立验收链 | [delivery_gate/__main__.py](../../../scripts/delivery_gate/__main__.py) → 1.4 [C] |

其他节点是确认、读报告或交接材料，没有对应自动脚本。直接自检失败先修实现；开发报告失败不送正式验收；没有授权不操作 Git。

## 1.2. A／B／C 的区别与 A 的入口

| 层次 | 证明范围 | 执行边界 |
| --- | --- | --- |
| A：直接自检 | 本次实现的相关静态／编译／定点测试 | 实现者执行；不代替完整开发报告或正式验收 |
| B：开发检查 | 真实 diff 与风险所需完整检查闭包 | 工作包安排未参与实现的执行者；Hook 只产开发报告，不签 Formal 身份收据 |
| C：正式验收 | 冻结 Task／输入／计划的独立执行及适用审查 | validator 与 producer／submitter 分离；reviewer 再分离；review／check 不重跑测试 |

身份与分工以 [agent-policy](../../../harness/agent-policy.manifest.yaml) 为准；开发报告不能直接升级为 Formal validation。

**对应总图 S2 [A]。** 以下是实现者选择和交接过程，不是新增自动脚本；S2 的实际命令按图下语言表选择。

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
title A｜实现者直接自检：选择、执行、交接
start
:S1 选择相关检查;
note right
入口：无脚本（核对实际改动）
作用：按所改语言／模块选择静态、编译和直接回归
<color:#995A00><b>预期：1–3 分钟（Agent 规划）</b></color>
end note
:S2 执行选定命令;
note right
入口：python_quality.py／docs_check.py／java_exec.py／npm 等（下表）
作用：只执行所选命令；不叠跑全仓聚合入口
<color:#995A00><b>预期：1–5 分钟（轻量机器规划）</b></color>
end note
if (所选检查真实通过？) then (PASS)
:S3 核对自检范围;
note right
入口：无脚本（读取实际输出）
作用：区分通过、未运行和不适用，不推断完整覆盖
<color:#995A00><b>预期：30–60 秒（Agent 规划）</b></color>
end note
:S4 交出直接反馈;
note right
入口：无脚本（整理检查证据）
作用：交出命令和结果，再进入 B 风险检查
<color:#995A00><b>预期：30–60 秒（Agent 规划）</b></color>
end note
stop
else (FAIL / BLOCKED)
:S5 定位修复入口;
note right
入口：无脚本（分析失败日志）
作用：交回对应实现或环境；修复后从相关检查重入
<color:#995A00><b>预期：2–5 分钟（分析规划，不含修复）</b></color>
end note
stop
endif
legend bottom
下表按语言列实际命令；不是所有命令都运行。
预算不含下载、环境安装和修复；直接结果不代替 B 或 Formal。
endlegend
@enduml
```

| 节点 | 核心作用 | 入口／交接 |
| --- | --- | --- |
| S1–S2 选择并执行 | 按实际改动运行相关检查 | 下方语言入口表，不要求全跑 |
| S3–S4 核对并交接 | 明确真实结果与未覆盖范围 | 人工／Agent 核对输出，再进入本页 B |
| S5 定位失败 | 先修实现或环境，之后重入相关检查 | 对应源码、直接测试或[环境准备](../operations/verification-environment.md) |

| A：改动类型 | 直接检查 | 入口文件／说明 |
| --- | --- | --- |
| Python 工具 | `python3 -m scripts.repository.python_quality`；相关 `python3 -m unittest <测试模块>` | [python_quality.py](../../../scripts/repository/python_quality.py)、对应[直接测试](../../../tests/) |
| Java 产品 | 经 `java_exec` 运行相关 Gradle 静态／编译／测试任务 | [java_exec.py](../../../scripts/environment/java_exec.py)、[原生任务](../reference/java-checks.md) |
| 扩展 | `npm --prefix extension run build`；相关 `node --test` | [package.json](../../../extension/package.json)、[直接测试](../../../extension/tests/) |
| 文档 | `python3 -m scripts.repository.docs_check` | [docs_check.py](../../../scripts/repository/docs_check.py) |

按修改选择，不把所有语言检查都跑一遍。

## 1.3. 展开 B：Stop 交付检查怎样执行

**对应总图 S3 [B]。** 公开入口：[delivery_hook.py](../../../scripts/verification/delivery_hook.py)::`main()`，接收客户端 Stop 输入；手工调用方式见 [Hook 说明](../change-delivery/hooks.md)。

图中其余文件是内部实现，不是需要依次手动运行的脚本。

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
title B｜Stop：先判断是否交付，再检查与收尾
start
:S1 分类入口与回合;
note right
入口：scripts/verification/delivery_hook.py::main / evaluate
作用：校验输入、事件、启用状态、末行标记与真实 diff
<color:#995A00><b>预期：1–5 秒（规划；输入等待上限 3 秒）</b></color>
end note
if (有效 Stop、启用、交付且有 diff？) then (需要检查)
:S2 取得窗口并冻结风险;
note right
入口：delivery_hook.py → coordination.py / development.py
作用：取得非等待窗口，再冻结真实范围与 profiles
<color:#995A00><b>预期：1–10 秒（机器规划）</b></color>
end note
:S3 准备隔离服务;
note right
入口：test_services.py::isolated_test_services
作用：按风险闭包一次准备本次 PostgreSQL／Redis
<color:#995A00><b>预期：10–30 秒（引擎／镜像就绪规划）</b></color>
end note
:S4 执行风险检查;
note right
入口：development.py::execute_development
作用：首个失败早停，未执行项不当作通过
<color:#995A00><b>预期：高风险 25–28 分钟（单次样本推估）</b></color>
end note
:S5 清理自有资源;
note right
入口：test_services.py::isolated_test_services
作用：finally 清理本次 lease，不清理未知资源
<color:#995A00><b>预期：10–30 秒（机器规划）</b></color>
end note
:S6 分析执行结果;
note right
入口：delivery_hook.py::execute_delivery / evaluate
作用：合并准备、检查、漂移与清理结果
<color:#995A00><b>预期：1–5 秒（机器规划）</b></color>
end note
if (必需检查与清理全部通过？) then (PASS)
:S7 交出开发报告;
note right
入口：delivery_hook.py::decision
作用：输出开发 PASS 及报告，不签正式 validation
<color:#995A00><b>预期：不超过 1 秒（机器规划）</b></color>
end note
stop
else (FAIL / BLOCKED)
:S8 交还阻断原因;
note right
入口：delivery_hook.py::decision
作用：普通首次阻断；续轮或窗口冲突停止自动重试
<color:#995A00><b>预期：不超过 1 秒（机器规划）</b></color>
end note
stop
endif
else (不运行)
:S9 输出未检查原因;
note right
入口：delivery_hook.py::evaluate
作用：中间／无 diff 不签 PASS；停用或异常为 BLOCKED
<color:#995A00><b>预期：不超过 1 秒（机器规划）</b></color>
end note
stop
endif
legend bottom
非 Stop 返回空响应；精确末行标记表示中间回合，不能用于普通交付。
S2–S6 表示结果处理主干，不保证每项执行：前置失败短路检查，只清理已获资源。
窗口冲突／取消等直接停止续轮；普通首次失败请求修复，续轮失败 continue=false。
endlegend
@enduml
```

| 节点 | 核心作用 | 实现文件 |
| --- | --- | --- |
| S1／S9 入口与未执行 | 区分真实交付、中间回合、无 diff、停用和异常输入；不签虚假 PASS | [delivery_hook.py](../../../scripts/verification/delivery_hook.py) |
| S2 窗口与冻结 | 非等待窗口内固定真实风险及检查范围 | [coordination.py](../../../scripts/verification/coordination.py)、[development.py](../../../scripts/verification/development.py) |
| S3／S5 准备与清理 | 一次准备并清理本次隔离资源 | [test_services.py](../../../scripts/environment/test_services.py) |
| S4 检查执行 | 执行所需 Check；首个失败早停 | [development.py](../../../scripts/verification/development.py)、[检查声明](../../../harness/module-checks.yaml) |
| S6–S8 结果与协议 | 分析真实结果；输出开发报告或阻断，续轮失败终止重试 | [delivery_hook.py](../../../scripts/verification/delivery_hook.py)、[Stop 策略](../../../harness/delivery-hooks.json) |

准备失败不执行检查；清理不确定不能 PASS。无 diff 或中间回合也不签 PASS。每个 Check 的内部命令与耗时见[共用检查链](../change-delivery/verification.md)。

显式开发入口是 [development.py](../../../scripts/verification/development.py)::`main()`，不自动创建服务。按[隔离环境说明](../operations/verification-environment.md)准备后运行：

```sh
.local/lexiflow-python/bin/python -m scripts.verification.development
```

显式 CLI 与正常 Stop 不会自动跨调用去重，普通交付不能绕过 Stop。完整 Verify／diagnose／quick CI 按目的另选，入口见[Verify](../change-delivery/verification.md)与[CI](../operations/github-actions.md)。

## 1.4. 展开 C：正式验证、审查与条件核对

**对应总图 S5 [C]。** CLI 入口：[delivery_gate/__main__.py](../../../scripts/delivery_gate/__main__.py)::`main()`；命令为 `python3 -m scripts.delivery_gate <子命令> ...`，参数见 [Delivery Gate](../change-delivery/delivery-gate.md)。

`submit.py`、`validate.py`、`review.py`、`check.py` 是独立职责的实现文件，不是各自可直接运行的 CLI。各阶段由不同职责的执行者交接，不是一个 actor 连续自签。

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
title C｜""delivery_gate/~_~_main~_~_.py""：正式验收
start
:S1 提交冻结输入;
note right
入口：submit.py（CLI 子命令：submit）
作用：绑定 Task、producer 与报告，冻结送验输入
<color:#995A00><b>预期：1–10 秒（规划估算）</b></color>
end note
:S2 确认隔离环境;
note right
入口：无脚本（确认隔离环境）
作用：确认工具和隔离资源，不继承 Hook lease
<color:#995A00><b>预期：1–3 分钟（Agent 规划）</b></color>
end note
:S3 独立执行验证;
note right
入口：validate.py（CLI 子命令：validate）
作用：独立执行冻结计划，失败不进入审查
<color:#995A00><b>预期：高风险 25–30 分钟（同能力推估）</b></color>
end note
if (独立 validation PASS？) then (PASS)
:S4 落实计划审查要求;
note right
入口：review.py（发布意见；审阅无脚本）
作用：按计划安排独立审查；不适用时不发布意见
<color:#995A00><b>预期：审阅 5–15 分钟＋发布 1–10 秒；不适用 1–10 秒</b></color>
end note
if (计划审查要求满足？) then (满足)
:S5 核对条件与依赖;
note right
入口：check.py（CLI 子命令：check）
作用：只核对证据、依赖与批准，不重跑测试
<color:#995A00><b>预期：1–10 秒（规划估算）</b></color>
end note
:S6 分析验收结果;
note right
入口：无脚本（读取 check 输出）
作用：确认验收结论或证据缺项
<color:#995A00><b>预期：10–30 秒（Agent 规划）</b></color>
end note
if (证据与必要条件完整？) then (PASS)
:S7 交出完整验收链;
note right
入口：无脚本（整理正式证据）
作用：交出当前输入的完整有效链，不自动操作 Git
<color:#995A00><b>预期：1–2 分钟（Agent 规划）</b></color>
end note
stop
else (FAIL / BLOCKED)
:S8 交还证据缺项;
note right
入口：无脚本（分析证据缺项）
作用：补条件后重核；输入改变须重新送验
<color:#995A00><b>预期：2–5 分钟（分析规划，不含修复）</b></color>
end note
stop
endif
else (FAIL / BLOCKED)
:S9 交还审查问题;
note right
入口：无脚本（接收审查意见）
作用：交回真实 findings，先修复再推进
<color:#995A00><b>预期：2–5 分钟（分析规划，不含修复）</b></color>
end note
stop
endif
else (FAIL / BLOCKED)
:S10 交还验证阻断;
note right
入口：无脚本（分析验证失败）
作用：交回执行／环境问题，不开展审查
<color:#995A00><b>预期：2–5 分钟（分析规划，不含修复）</b></color>
end note
stop
endif
legend bottom
通过链向左继续深入，右侧失败链提前结束；不画服务是否存在的空分支。
S4 按冻结计划落实审查：要求 review 才发布意见；不适用时不伪造 review。
本页预算针对高风险工程且非正式发行；低风险按实际计划，发行另看发行流程。
endlegend
@enduml
```

| 节点／子命令 | 核心作用 | 实现文件 |
| --- | --- | --- |
| S1 `submit` | 冻结真实 Task、来源报告与送验输入 | [submit.py](../../../scripts/delivery_gate/submit.py) |
| S3 `validate` | 独立执行冻结计划并发布验证证据 | [validate.py](../../../scripts/delivery_gate/validate.py) |
| S4 `review` | 只发布独立审阅形成的真实意见；不适用不发布 | [review.py](../../../scripts/delivery_gate/review.py) |
| S5 `check` | 核对证据、依赖与必要批准，不重跑测试 | [check.py](../../../scripts/delivery_gate/check.py) |

S2 环境确认、S6 读结果、S7–S10 交接或分析均没有统一自动脚本。缺条件可补齐后重核；相关输入改变须重新送验；已清理的 Hook lease 不能传给 validate。

## 1.5. 预算依据与维护位置

预算前提：工具、浏览器依赖、引擎与镜像已就绪，无相关并发写入。安装、下载、用户响应和排期另计。

| 预算 | 依据 |
| --- | --- |
| B：高风险 25–30 分钟 | 同批完整成功报告 `00fd7d32-8132-4bc1-adca-9a104e804f58`、`3ebc011b-a56b-4317-bf5e-97a2530d1c10`：物理 Check 1508.127 秒，窗口 1510.083 秒，约 25 分 10 秒；仅一个样本 |
| A／B：轻量 1–5 分钟 | 规划估算；依赖纳入昂贵模块时重新估计，不是局部风险的固定时长 |
| C：高风险非发行 35–50 分钟 | 相近计划的 validation 推估 25–30 分钟，另含主动准备、审阅与交接；不是 Formal 实测，发行另估 |
| 秒级处理与 Agent 工作 | 均为规划预算；父阶段包含子图，不重复相加；两次独立执行分别计时 |

预期不是 SLA、timeout 或 PASS 条件，也不证明优化收益。执行步骤只在本页完整维护；改进任务见[优化场景](optimize-repository-gates.md)，字段与参数见各能力页，未决设计见[问题台账](../../roadmap/master-plan/phase2/status.md)。返回[总入口](../overview.md)。
