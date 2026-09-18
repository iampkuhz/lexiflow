# G1 任务与证据索引

本页只提供中文导航，不是收据，不复制目录中的英文机器合同，也不维护运行状态。任务版本、负责人、验收条目索引与精确依赖以 [目录](../../planning/workstreams.yaml) 为准；当前结果见[阶段状态](../roadmap/phase-1-status.md)。

## 判定边界

ARCH-0008 的阻断闭包包含 30 项任务、60 条硬性/合同边；29 项前置证据完成后才请求用户决定，退出收据在批准后签发。直接前置为 ARCH-0007、QLT-0003、QLT-0005、QLT-0006、OPS-0001，以及 QLT-0013 的目录决策合同；精确版本从目录读取，不在本页再次手写。

每项需核对自己的 `acceptance_criteria[]` 与 `acceptance_evidence[]`，不是只完成下表摘要。文件必须进入冻结的 `required_inputs`；普通链接不构成哈希绑定。OpenSpec 勾选、回调、ACK、退出零、未运行、跳过和引导收据都不能替代正式验收。

## 逐任务导航

| 稳定任务 ID | 中文职责与现役材料 | 需证明的重点 |
|---|---|---|
| `LF-TSK-ARCH-0001` | [假设、不变量与非目标](../architecture/contracts/architecture-invariants.md) | 评审假设登记与非目标 |
| `LF-TSK-ARCH-0002` | [领域职责与数据归属](../architecture/modules-and-dependencies.md) | 检查单一负责人及跨领域约束 |
| `LF-TSK-ARCH-0003` | [模块依赖与端口](../architecture/modules-and-dependencies.md) | 检查无环依赖及禁止边 |
| `LF-TSK-ARCH-0004` | [字幕到提示的完整链路](../architecture/caption-and-learning-flows.md) | 推演成功、超时、缓存命中和降级 |
| `LF-TSK-ARCH-0005` | [行为到个人档案](../architecture/caption-and-learning-flows.md) | 检查幂等、重放与最终一致性 |
| `LF-TSK-ARCH-0006` | [同步异步边界](../architecture/phase-1-lifecycle-guarantees.md) | 检查延迟、重试、取消与降级预算 |
| `LF-TSK-ARCH-0007` | [架构决策与备选比较](../architecture/decisions.md) | 比较十项 ADR 的代价及复审条件 |
| `LF-TSK-PRD-0001` | [英文优先观看体验](../product/product-brief.md) | 推演观看旅程及打扰预算 |
| `LF-TSK-SEM-0001` | [语义能力端口](../architecture/contracts/semantic-capability.md) | 核验四类任务能力与供应商无关性 |
| `LF-TSK-SEM-0002` | [语义结果与失败](../architecture/contracts/semantic-result.md) | 检查格式错误、低置信、超时与拒绝 |
| `LF-TSK-PRF-0002` | [缓存正确性与隐私](../architecture/contracts/cache.md) | 检查键、版本、存活时间、失效与清除 |
| `LF-TSK-SEC-0001` | [威胁与信任边界](../architecture/contracts/trust-boundaries.md) | 推演滥用与跨边界验证 |
| `LF-TSK-OBS-0001` | [观测与脱敏](../architecture/contracts/observability.md) | 检查跨流程关联、有限指标及禁止字段 |
| `LF-TSK-ADP-0001` | [来源适配与符合性](../architecture/contracts/source-adapters.md) | 对照视频、网页、PDF 与音频样例 |
| `LF-TSK-QLT-0001` | [任务、运行、归属与证据合同](../development/validation/04-harness-and-dispatch.md) | 检查合同、结构定义及非法输入样例 |
| `LF-TSK-QLT-0002` | [规划验证器](../development/dispatch-preflight-design.md) | 验证重复、缺失、归属冲突与环 |
| `LF-TSK-QLT-0003` | [验收控制面合同](../development/gate-control-plane-design.md) | 核验计划/运行/状态/收据与单一执行层 |
| `LF-TSK-QLT-0004` | [验收案例追踪](../acceptance-cases/phase-1.md) | 拒绝孤立、重复及陈旧案例映射 |
| `LF-TSK-QLT-0005` | [派发预检](../development/dispatch-preflight-design.md) | 验证路径、归属、写入与合同冲突 |
| `LF-TSK-QLT-0006` | [回调优先生命周期](../development/validation/04-harness-and-dispatch.md) | 验证单活跃、身份、完成先落盘与 共享策略规定的兜底 |
| `LF-TSK-QLT-0007` | [显式结果证据及规范产物](../development/gate-control-plane-design.md#current-execution-evidence) | 验证六字段、真实身份、逐任务产物、不可变发布和哈希 |
| `LF-TSK-QLT-0014` | [受信签发者](../development/gate-control-plane-design.md) | 验证四类身份来源、授权、重放及签发独立性 |
| `LF-TSK-QLT-0008` | [纯规划与注册表](../development/gate-control-plane-design.md) | 验证零副作用、精确版本、固定命令和完整指纹 |
| `LF-TSK-QLT-0009` | [冻结检查执行与三态](../development/quality-gate-layering.md) | 验证实际程序绑定、捕获、超时及未执行不通过 |
| `LF-TSK-QLT-0010` | [验证收据与状态](../development/gate-control-plane-design.md) | 验证 START 顺序、不可变发布及单次读取 |
| `LF-TSK-QLT-0011` | [独立审阅](../development/quality-gate-layering.md) | 拒绝自审、写入重叠、过期输入和隐藏修改 |
| `LF-TSK-QLT-0012` | [证据哈希图](../development/gate-control-plane-design.md) | 拒绝自环、回边、别名、缺节点及不同字节身份冲突 |
| `LF-TSK-QLT-0013` | [当前输入目录决策](../development/gate-control-plane-design.md) | 核验验收映射、依赖、签发链与不可提升的失败 |
| `LF-TSK-OPS-0001` | [工具链选择与复现](../development/post-approval-toolchain-verification.md) | 比较至少三种技术栈，核对版本、环境差异和批准后计划 |
| `LF-TSK-ARCH-0008` | [G1 退出与用户决定](../reviews/g1-decision-package.md) | 先闭合前置证据，再记录明确用户批准 |

## 唯一执行与证据顺序

1. 引导实现只产生局部实现证据，不满足正式依赖，也不回填 READY。
2. 从依赖根开始，按当前目录的硬性/合同边处理任务；每项由 `TASK_VALIDATION` 执行冻结检查，保存精确任务/变更/运行/执行者身份、源码快照、命令及验收映射。
3. `INDEPENDENT_REVIEW` 消费验证收据与冻结差异，检查真实执行者独立性和零受验对象写入；不重跑交付检查。
4. `CATALOG_DECISION` 核验验证、审阅、依赖、签发授权、当前输入和无环哈希图；不提升 BLOCKED/FAIL、不推断用户批准。
5. 用户明确决定后才发布退出收据，G1 PASS 与 APPROVED 同时满足才进入第二阶段。

详细接口只在[控制面合同](../development/gate-control-plane-design.md)、[规范产物合同](../development/gate-control-plane-design.md#current-execution-evidence)及 Harness 维护；实际命令只在[校验手册](../development/validation/README.md)维护。Java 由 Gradle/Java 唯一执法，Python 不重复扫描 Java 源码。

<!-- 旧章节锚点兼容；精确条目统一到上表和 catalog。 -->
<a id="g1-catalog-task-evidence-map"></a>
<a id="3-catalog-task-到证据的精确映射"></a>
<a id="31-architecture-与-product-主链"></a>
<a id="lf-tsk-arch-0001--freeze-assumptions-invariants-and-non-goals"></a>
<a id="lf-tsk-arch-0002--define-bounded-contexts-and-responsibilities"></a>
<a id="lf-tsk-arch-0003--define-module-dependency-direction-and-ports"></a>
<a id="lf-tsk-arch-0006--freeze-synchronous-and-asynchronous-boundaries"></a>
<a id="lf-tsk-prd-0001--define-english-first-viewing-journey-and-interruption-budget"></a>
<a id="lf-tsk-arch-0007--compare-alternatives-and-publish-p1-adr-package"></a>
<a id="32-phase-1-横切合同"></a>
<a id="lf-tsk-sem-0001--semantic-capability-ports"></a>
<a id="lf-tsk-sem-0002--semantic-result-semantics"></a>
<a id="lf-tsk-prf-0002--cache-contracts"></a>
<a id="lf-tsk-sec-0001--phase-1-threat-model"></a>
<a id="lf-tsk-obs-0001--telemetry-and-redaction-contract"></a>
<a id="lf-tsk-adp-0001--source-adapter-contract"></a>
<a id="33-agentgate-与-dispatch-主链"></a>
<a id="lf-tsk-qlt-0001--task-run-owner-and-evidence-schemas"></a>
<a id="lf-tsk-qlt-0003--g1-plan-run-status-and-receipt-semantics"></a>
<a id="lf-tsk-qlt-0005--file-claims-and-single-owner-dispatch-checks"></a>
<a id="lf-tsk-qlt-0006--callback-first-lifecycle-and-watchdog"></a>
<a id="lf-tsk-qlt-0007--explicit-gate-result-evidence-packets"></a>
<a id="lf-tsk-qlt-0008--pure-gate-planner-and-fixed-command-registry"></a>
<a id="lf-tsk-qlt-0009--frozen-checker-execution-and-typed-outcomes"></a>
<a id="lf-tsk-qlt-0010--atomic-task-validation-receipts-and-read-only-status"></a>
<a id="lf-tsk-qlt-0011--immutable-independent-review-evidence-chains"></a>
<a id="lf-tsk-qlt-0012--acyclic-immutable-evidence-hash-graph"></a>
<a id="lf-tsk-ops-0001--runtime-build-lock-and-repository-layout-decision"></a>
<a id="lf-tsk-arch-0008--run-g1-architecture-review-and-request-user-decision"></a>
<a id="5-建议的-gate-owner-规则"></a>
