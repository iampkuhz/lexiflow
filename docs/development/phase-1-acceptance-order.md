# Phase 1 acceptance order

G1 的退出任务是 `LF-TSK-ARCH-0008@5/2.2.0`。当前 catalog 的 blocking closure 有 30 个 Task、60 条依赖边；其中 29 个是请求用户决定前必须完成的前置任务，退出任务负责最终评审和记录用户决定。不能要求退出任务先 PASS 才请求该任务需要的用户决定。

| 顺序 | 所需证据 | 结果边界 |
|---|---|---|
| 实现与交付 | current canonical source/outcome、真实 runtime actor、按范围选择并只执行一次的 TASK_VALIDATION checks | 小 signal、ACK、测试摘要不能替代正式 validation receipt。 |
| 独立审阅 | current validation receipt、冻结 diff/source/snapshot、与 producer/validation issuer 不同的受信 actor、零 subject write-set | 只消费证据，不重跑交付命令；缺身份或证据即 BLOCKED/FAIL。 |
| 前置准入 | 29 个 prerequisite Task 的 current validation/review/catalog receipts 及精确依赖闭包 | 退出任务的用户决定仍未发生，不把它提前标为 PASS。 |
| 用户决定 | 已冻结且可评审的完整架构、ADR alternatives/trade-offs、初始 SLO 假设、前置证据索引和公开 Gate 的实际结果 | Main 提请明确接受、修改或拒绝；不得自行添加批准标记。 |
| G1 退出与 Phase 2 入口 | 明确用户批准后，记录可追溯批准来源，签发退出任务的 current receipt；G1 PASS 与 APPROVED 同时满足 | Phase 2 才能进入 Data Model；批准造成的决定记录变化需更新相关输入绑定。 |

批准前对退出任务的检查可以得到 `BLOCKED`，理由必须限定为待用户决定，并且其余技术前置已证明完成。这个结果不能作为 Phase 2 前置 PASS。不能重复运行它等待批准，也不能把用户批准变成重复全量交付测试的理由。

## Toolchain boundary

`OPS-0001` 的 Phase 1 deliverable 是技术选择 ADR、当前环境差异与批准后的精确复现计划。已有 Java 25/Gradle/Spring Boot 工程底座来自用户后续明确要求的 Java harness 实施；存在的文件不能再描述为未创建。该底座不证明产品业务或双环境复现完成。

批准后的 clean external environment reproduction、Extension runtime/package-manager pin、部署编排与产品实现由后续阶段的 OPS/Data/API Task 承担。不能把这些批准后才执行的动作反向作为请求 G1 批准前的条件。已声明的 Phase 1 架构、工具链选择、harness 与正式 current-input 验收仍保留完整要求。

## Current status

当前完整前置目录收据数为零，尚未到请求用户决定的时点。QLT-0007、QLT-0014、QLT-0008 的 Task 验证 PASS 收据保留历史证明；当前 policy/catalog 输入已经变化，这些收据不能用于当前验收。ARCH-0001 与 QLT-0001 的 Root 源材料及独立审阅 carrier 同样保留原始记录，不能刷新 hashes 后沿用旧验收。

Qoder 工作包 `LF-WP-ARCH-P1-COMPLETE-031`（run `972690fa-8c7d-45d1-8baf-7f995519f94f`）已失败并 ACK：约 6 秒退出、无工作包结果、七个交付路径均无变更。回调身份和当前版本核对通过，七条当前源契约命令各运行一次均 PASS；这些命令不能证明此次 Qoder 交付，交付验收为 FAIL。CLI 错误仅提供计费入口，具体拒绝原因尚未确认。

Terra 工作包 `LF-WP-ARCH-P1-COMPLETE-032` 已结束：canonical 完整性校验通过，七项 outcome 仍为 BLOCKED，ARCH1 汇总和推演精度等设计缺项尚待补齐。父会话真实留存的 before bytes 已与七项 before SHA/absence 完全匹配，无历史重建。

当前由一个 Luna 子代理 `/root/architecture_completion_luna` 承接完整七项架构补齐包 `LF-WP-ARCH-P1-COMPLETE-036`，共 315 catalog 分钟。实际派发使用 Luna、medium reasoning、fork none；新派发默认 Luna，Terra 只凭父决策和具体证据升级。Qoder 当前无活动分配，恢复前不补开重试。设计推演不能冒充产品测试，启动成功不能替代交付和分层验收。工作仍停留在 Phase 1 架构层；进入后续产品实施仍需用户决定。
