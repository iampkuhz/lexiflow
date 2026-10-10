# 1. 当前交付状态

本页只保留仍需行动的状态，不保存阶段流水账或旧 receipt。目标与验收分别见[阶段入口](../phase2.md)、[验收要求](acceptance.md)；单次检查结论必须与当前冻结输入相符。

## 1.1. 已有实现

- 观看链路消费已发布资料，英文不等待后端，不调用模型；字幕切换、版本冲突和依赖故障保持安全降级。本机抑制偏好不上传。
- 最新词库存储采用精简 entry、form 与 dataset 模型；对外词条 ID 使用十进制字符串，协议为 `caption-hints.v2`。结构变化仍须显式重建重导，不能把源码更新当作运行资料已更新。
- M 芯片 macOS 使用 Podman 本机安装与更新入口；操作保留引擎和资源归属、锁、资料身份及失败恢复检查。实际命令见[用户指南](../../../user/podman-macos.md)，不复述旧安装过程。
- 工程工作流已实现风险驱动开发自检、独立验收分层、同事务检查复用和证据查询优化。开发 PASS 不代表正式发行或用户机器验收。

## 1.2. 当前未完成项

- 最近完成的独立开发检查为 25 项 PASS、22 次物理执行、757 项冻结输入无漂移，墙钟 21 分 37 秒；仅证明当时的累计混合变更。配置精简已继续修改输入，最终结论须读本轮冻结报告，不继承先前 PASS；Formal、完整 Repository baseline 与正式发行未在本轮验收。
- 用户机器上的真实 Chrome/YouTube 最终体验仍需确认，包括连续字幕、跳转、原生换行、同词不同位置、常见短词误提示和旧提示失效；合成 E2E 不能代替真实观看。
- 正式发行仍需同一真实候选的构建、安装/更新/恢复、完整资源窗口与资料许可证据，以及可核验的独立验收链。短采样、单项机制测试或本机启动成功均不代替这些条件。
- Git 集成与发布按本次明确授权执行；不存在由旧阶段记录继承的自动提交、合并或推送许可。

## 1.3. 当前入口

- 工作项、版本和依赖：[任务清单](tasks.md)与 [Catalog](../../../../planning/workstreams.yaml)。
- 工程验证与身份交接：[交付主干](../../../reference/checks.md)。
- 当前运行边界：[发布与使用](release.md)、[架构与数据](../../../system.md)。
- 后续能力不阻断基础观看，见[待办池](../future.md)。

## 1.4. 工程架构与治理问题

本节是未决问题的唯一记录，操作页只解释现行逻辑并回链。**文档表达不满足阅读要求，与实际架构／流程设计未获认可，是两类问题。** 只有修改图文可解决的，不据此宣称架构有缺陷；需要改变合同或执行行为的，先决策并形成 OpenSpec 工作项，不以文档改写代替批准。

| 标识／类别 | 当前问题与证据 | 需要确认的设计及验收边界 |
| --- | --- | --- |
| ARCH-GATE-01：执行层次设计 | [submit](../../../../scripts/acceptance/task_operations/submit_task_for_acceptance.py) 要求开发来源报告；[validate](../../../../scripts/acceptance/task_operations/run_independent_task_validation.py) 仍独立执行计划。高风险开发与正式验证可能覆盖相近昂贵 Check；[复用合同](../../../../harness/agent-policy.md) 禁止自检到 Formal 及跨交付复用 | 评估两次完整覆盖是否都必要、各自提供何种不可替代证明，以及能否在独立身份／输入冻结／必需覆盖均保留的前提下缩短关键路径。现有证据支持设计评估，不证明重复本身是 bug，不授权省略任一层 |
| ARCH-GATE-02：环境责任设计 | [Hook](../../../../scripts/triggers/run_stop_checks.py) 自动拥有隔离服务 lease；[development CLI](../../../../scripts/verification/commands/run_development_checks.py) 与 [Formal validate](../../../../scripts/acceptance/task_operations/run_independent_task_validation.py) 依赖调用者确认环境，详见[环境说明](../../../scenarios/verification-environment.md) | 评估“执行同一检查能力”是否需要统一受控环境准备与清理的接口，或维持不同职责但建立一致用户入口。不得复用已清理 lease、开发库／未知资源，也不默认安装或下载；需用失败／取消／归属回归证明 |
| ARCH-GATE-03：开发检查预算 | 释放应用内存后的独立 Stop 为 1462.68 秒（24 分 23 秒）；23 次物理命令、18 项同事务复用、741 项输入无漂移。实测比同代码主要对照少 118.50 秒，不能把单次差异全归因于环境 | Docker 合同 448.04 秒、Verify 与 Gate 工具自测合计 251.60 秒；Java delivery 与构建合同共 283.52 秒。外层未细分差额仅 21.05 秒。选择范围与内部执行成本都需处理，不减断言、不放宽超时、不跨交付复用；逐项证据在 round5-memory-freeze 的 ignored 目录 |
| ARCH-GATE-04：检查取消边界 | Verify 对整个 Check 进程组使用 0.5 秒 TERM 宽限；Docker 夹具内存在独立进程组 | 本轮不采用额外 worker 并行：无法在该边界内证明取消后回收。原型仅留临时证据，源码已恢复；后续若重做并行，应先定义和验证完整进程归属与取消协议，不能扩大超时掩盖问题 |
| ARCH-GATE-05：Docker helper 回收 | 已从 [真实 timer 源码](../../../../ops/release/runtime/release-installation-docker.sh) 在后台启动与 PID 登记之间加入同步探针：旧 handler 会遗弃存活 helper；现行逻辑在登记后兑现取消并完整回收 | [直接回归](../../../../ops/docker/tests/test_docker_bounds.py) 保留全部原断言，确定性覆盖 budget 与 grace 两个窗口及旧 handler 反例；10 项直接测试及完整独立 Stop 中相同 10 项均通过，独立源码／冻结证据审查无阻断。原故障 PID 属于哪个 helper 无法追溯；这是已修复机制的开发验证，不是永不重现或整轮提速保证 |
| ARCH-GATE-06：性能测量环境 | 经用户明确授权删除 LexiFlow 容器、保留卷，并终止 Chrome 与 IntelliJ 后完成一次整轮复测；运行期间采集 26 个内存样本，swap 约 12.5–13.6 GiB，压缩内存仍有波动 | 释放内存有改善但不足以解决整体等待，不能归咎单个应用或以环境变化代替源码优化。仅精确处理已获授权的资源，不关闭未知进程；原始样本位于 round5-memory-freeze 临时证据 |
| ARCH-GATE-07：风险等级与默认检查范围耦合 | 日常入口已改为按变化选择，不因高风险追加全仓基线。消费审查发现的 API→扩展 E2E、Verification/策略/环境/Catalog→Gate 与 Verification fixture 输入遗漏已补齐；21 对声明、精确冻结场景及四个 Python 工程模块的递归静态导入均纳入直接回归 | LF-TSK-QLT-2118 v2 的定向回归及独立源码复核通过；最新完成的开发检查已覆盖选择矩阵和 Docker 固定分组，无覆盖缺口。这不是 Formal 验收。Formal 高风险基线与 review、显式完整 Verify 和发行仍保留 |
| ARCH-GATE-08：生命周期检查耗时与诊断 | 原入口复测仍曾触发 120 秒上限，主要成本在模型之外。测试客户端现已将两次逐流计数合为一次 `wc`、两步清理合为显式非递归 `rm -d`；每请求少启动两个工具进程，未减少身份查询、模型执行或断言 | 最新完整生命周期检查 7 项 PASS、0 跳过，747 项冻结文件无漂移，独立只读复核通过；包装墙钟 114.22 秒包含末尾哈希快照，不是精确子进程时长。距原上限仍近，单次样本不证明稳定裕量或整轮收益。原六项用例全部保留，新增长度损坏／响应缺失／未知残留回归；已纳入最新完成的正常 Stop；不代表正式发行验收 |
| ARCH-GATE-09：测试入口超时后的后代回收 | [runTests](../../../../ops/release/test_tools/run-release-tool-tests.mjs) 已用本次 spawn 创建的私有进程组回收测试后代，timeout／输出超限及上级 TERM／INT 都立即强杀该组；不增加原取消宽限 | LF-TSK-QLT-2120 的 5 项定点回归及独立只读复核通过。真实系统信号测试保持上级 0.5 秒强杀边界，核对 supervisor／worker 已退出、独立哨兵仍活与监听器恢复；原测试保留。证据仅覆盖当前 POSIX 环境，已纳入最新完成的正常 Stop；此修复不证明生命周期耗时问题已解决 |
| ARCH-SCRIPT-01：脚本入口设计 | [delivery_gate/__main__.py](../../../../scripts/acceptance/__main__.py) 统一分派子命令；`submit.py`、`validate.py`、`review.py`、`check.py` 分别提供场景 API，不是独立 CLI | 按文件优先说明职责，区分公开 CLI、内部函数与人工步骤。是否进一步要求每种流程独立可执行文件、一个公开入口，需单独评估；本次只如实标注入口，不改文件结构或接口 |
| ARCH-CONFIG-01：固定协议与运行参数 | [Stop 入口](../../../../scripts/triggers/run_stop_checks.py) 固定期限与进度间隔；事件、停用变量名、中间标记也由入口实现 | 配置精简已实现，最终开发验证以本轮冻结证据为准；不改变期限上限、进度、取消、停用未验证及独立验收语义，不宣称分钟级提速 |
| ARCH-CONFIG-02：配置职责与失效范围 | [Check 声明](../../../../config/engineering/module-checks.yaml) 统一 Check、场景资格与 triggers，CI 集合共用 Registry；人类规则在 Harness Markdown，机器配置在 config/engineering，固定合同归实现；仍按完整声明字节绑定输入 | 继续评估剩余声明归实际实现模块、Catalog 规约与任务数据分离、路径输入去重；需逐消费者证明覆盖完整，不能只搬目录或扩大自由配置。见[配置地图](../../../reference/configuration.md) |
| ARCH-GATE-10：变更类型与验收范围的分类标准 | 用户认可开发反馈、任务验收、正式发行三种结果；倾向规约 Markdown 归说明类，而不是仅因目录位置触发高风险全仓验收。当前机器判据仍按现行路径与内容执行 | 说明与规约、模块内行为、执行契约与工程机制的分类，以及正式高风险基线的必要性，先形成有覆盖证明的方案；不把阅读分类当作现有选择算法已改变。正式范围调整与目录改名尚未授权直接实施 |

## 1.5. 文档整理与验证边界

目录与合同整理已落地：唯一工程入口是 [docs/README](../../../README.md)，系统主题、真实任务和查阅分别位于 system、scenarios、reference；roadmap 与 user 保留独立职责。原三个视角目录已合并删除，Harness、Catalog、风险路径及引用者同步，未降低门禁覆盖。

本轮文档按深度精简实现同步：开发只保留唯一 diff 报告与精确诊断，Registry 合并 canonical Check/scene 和 CI policy，Git 提醒入口删除，环境与来源身份保持真实认证，Codex 每 Task 只引用不可变父合同，Gate 使用唯一活动 namespace 和最新 schema。六个受影响图已从 ignored 草稿实际校验、渲染、查看并原样复制；文档静态检查通过只证明链接和结构，不构成独立验收。用户已确认无外部旧运行或收据需要续用，Qoder 委派与旧事实消费说明已退役；仅保留手工客户端 Stop 与权限 profile。源码集成与独立验收按后续真实证据报告。

| 用户要求 | 当前实现 | 核对入口 |
| --- | --- | --- |
| 一个生命周期，阶段清晰 | 通用范围与验收合并；实现自检和交付证明分层；维护包括文档，无额外反馈阶段 | [总入口](../../../README.md) |
| 场景不是组件 | 修改导入、执行门禁、优化门禁分别成页；优化页回链执行页，不复制运行子图 | [功能变更](../../../scenarios/change-lexicon-import.md)、[执行](../../../scenarios/run-repository-gates.md)、[优化](../../../scenarios/optimize-repository-gates.md) |
| 阅读分层；目的先于实现清单 | 总览区分三体系；操作入口按目标选择；逐项检查库存侧链到 Reference，不把用途、场景、范围、入口混为层次 | [工程总览](../../../system/engineering.md)、[执行场景](../../../scenarios/run-repository-gates.md)、[检查清单](../../../reference/verification/check-catalog.md) |
| 原则属于 Harness | 文档人类规则在 Harness Markdown，docs 只保留导航与维护操作，不另存原则清单 | [documentation-policy](../../../../harness/documentation-policy.md)、[维护步骤](../../../scenarios/maintain-documentation.md) |
| 唯一正文及关联引用 | 来源／字幕合并；决策理由随系统主题维护；总入口和本机操作索引合并；未改图保留字节，修订的共用检查图重新校验、渲染与视觉核对后复制正文 | [系统](../../../system.md)、[本机开发](../../../scenarios/local-development.md) |
| IDE 链接和加粗 | 主导航采用相对文件链接；结束加粗标记与后文分隔 | 相对链接与片段已静态核对；本轮未重新执行 IDE 实点，阅读效果由用户 review 确认 |

文档内容与文件地图已做独立只读复核。最新完成的静态检查覆盖 58 个文件、25 张内嵌图；现有正文图源有校验与预览证据。配置精简同步两张相关图，按 skill 重新渲染与视觉核对后复制正文，最终检查以统一冻结后的报告为准，不借文档自检声称 Formal 或真实发行通过。

## 1.6. 待优化清单与已实现机制的验收缺口

只保留现行机制、仍需决策的边界和最新完成的整轮证据；原始运行日志留在 ignored 目录，不在此维护轮次流水账。

| 状态 | 已实现或待处理内容 | 核对与验收边界 |
| --- | --- | --- |
| 已完成开发验证 | 日常按变更与消费依赖选择；Docker 固定为 surface、shell、local、node，保留聚合基线 | [选择与检查声明](../../../../config/engineering/module-checks.yaml)；未知输入、删除、遗漏和重复失败关闭。单行 Java 不因“高风险”自动触发全部 Docker，实际范围仍取决于同工作区累计 diff |
| 已完成开发验证 | 非安装场景改用每例独立准备，首次安装覆盖保留在专门测试；真实状态对照、损坏拒绝与隔离检查共 43 例 | [准备合同](../../../../openspec/changes/gate-test-performance/ready-precondition.md)；最新完成整轮中这 43 例合计 181.85 秒，包含新增对照成本，不缓存 PASS |
| 已完成开发验证 | 批量写 Git 索引、合并 Git 元数据读取、按内容复用纯 YAML 解析、有限测试模型常驻及心跳虚拟时钟 | [性能优化合同](../../../../openspec/changes/gate-test-performance/proposal.md)；保留源身份重读、可变状态隔离、原测试断言、失败与取消回收，不用微基准比例外推整轮 |
| 已完成开发验证 | fake Docker 正常响应清理合并一次显式文件删除 | [清理合同](../../../../openspec/changes/gate-test-performance/ownership-cleanup.md)；正常 Stop 已覆盖 5 项新增回归；保留未知文件拒绝、部分失败、清理间隙取消与私有进程回收，不为小收益引入通用框架 |
| 不采纳 | Gradle 并行实验、共享 Git fixture seed | 并行未证明整轮收益且有取消边界成本；单次 build fixture 的 Git 准备仅约 0.11 秒，没有引入跨例共享复杂度的收益证据 |
| 实施待独立验收 | 仓库 Git 提醒 Hook 已删除；Stop 通过精确中间标记区分真正非交付回合 | 普通交付不使用中间标记，不据此声明 PASS；客户端真实触发仍须独立确认 |
| 待决策 | 正式高风险验收范围、剩余配置职责分离、脚本目录命名与环境入口统一 | 见 1.4；先给具体调整及覆盖证明，再由用户确认。独立验证不消费实现者 PASS 代替执行 |

最近完成的正常 Stop 为 `cd075d6d-026b-4307-95e8-a8b9c5015af9`：25 项逻辑 PASS、22 次物理执行、3 项合法同事务别名；Verification 571 例、Gate 122 例、Docker Python 197 例与 Node 130 例均无失败或跳过。757 项输入无漂移，隔离 PostgreSQL／Redis 已清理，窗口再次取得与释放通过；原始证据及独立审查在 ignored `tmp/quality/gate-test-performance/round24-cleanup-freeze/`。

该次墙钟 1296.69 秒，物理检查合计 1282.60 秒，外层差额 14.09 秒；主要成本为 Docker local 258.20 秒、shell 176.12 秒、Verify 126.76 秒、Gate 120.78 秒、生命周期 96.68 秒。单次结果不能作为稳定时限或优化因果证据；本轮配置精简主要减少维护成本，不承诺整轮提速。

submit／冻结只绑定输入；正式 validate 执行检查；review 和 check 只核对证据。quick CI、开发检查、完整 Repository 与 Formal 的集合不同，不能互相替代。

## 1.7. 文件地图与配置职责审查

[工程目录与生命周期](../../../system/engineering.md)和[脚本索引](../../../reference/scripts.md#16-文件编号索引)只引用实际存在的文件；开发反馈、完整 Repository、正式 Task 与发行交接范围保持分离。当前深度精简实现及逐包直接证据留在 ignored `tmp/quality/deep-simplification/`；独立集成验证、Formal 与公开发布尚未完成，不继承旧输入 PASS。

文档消费关系见[配置地图](../../../reference/configuration.md)，操作接口见[执行场景](../../../scenarios/run-repository-gates.md)。父任务冻结最终输入后交给真实独立执行者运行所需检查，review/catalog 只读核对证据，不重跑命令。已退役链的 ignored 原始记录保留字节，不再消费。
