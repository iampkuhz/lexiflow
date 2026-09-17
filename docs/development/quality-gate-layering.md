# Quality Gate layering

Java product quality has one execution owner: Gradle/Java. `backend` uses Spotless for formatting, Checkstyle and PMD for deterministic static analysis, `javaSourceGates` for the LexiFlow-specific comment/Javadoc/PMD-suppression rules, ArchUnit plus the Gradle dependency guard for architecture, and JUnit/Spring suites for behavior. Python does not scan Java source or repeat those assertions.

The pinned-JDK launcher is control-plane plumbing. It selects Temurin 25 and calls the repository Gradle Wrapper; the tool that decides the Java rule result is still Gradle/Java. The executable selections are declared in [java-product.manifest.yaml](../../harness/java-product.manifest.yaml): incremental delivery uses `check`; full delivery uses `deliveryFull`, which contains `qualityFull`, `check`, and both boot jars. Direct `architectureTest` and `verifyProductLanguage` commands are diagnostics, not additional Gate selections.

The public Gate has three layers. TASK_VALIDATION executes the frozen selected checks and creates the only delivery-validation receipt. INDEPENDENT_REVIEW reads that immutable receipt and independent review evidence. CATALOG_DECISION reads validation/review/dependency receipts and their hash DAG. The latter two routes publish a zero-check, evidence-consumption record and reject a supplied delivery execution payload.

Python continues to own catalog/planning validation, dispatch preflight, evidence packet materialization, issuer authorization, immutable receipt lifecycle and cross-artifact hash DAG checks. Those are repository governance rules, not Java product source rules.

## 执行与验收规则

| 责任 | 唯一执行位置 | 后续如何验收 |
|---|---|---|
| 格式、imports | Spotless | 读取交付收据 |
| Java 风格 | Checkstyle | 读取交付收据 |
| 静态分析 | PMD | 读取交付收据 |
| 业务注释、record Javadoc、PMD suppression | Java `javaSourceGates` | 读取交付收据 |
| 类依赖、模块依赖 | ArchUnit、Gradle dependency guard | 读取交付收据 |
| 产品行为 | JUnit/Spring tests | 读取测试报告与交付收据 |
| 调度、版本、证据完整性 | Python control plane | 核对合同、身份和 hashes |

开发修复可以按失败范围运行必要测试。源码和输入冻结后，正式交付只执行一次选中的增量或完整聚合入口；同一计划不能同时选择 `check`、`deliveryFull` 及其诊断子任务。独立审阅核对必要 diff 和上一层证据；阶段验收核对收据依赖，二者执行的交付检查数均为零。源码、命令、配置或输入改变时，旧证据不能继续充当当前结果。

Qoder preflight 将工作包中每个 Task 的当前 catalog 命令转换为 argv，再绑定到冻结 manifest。遗漏非主 Task、额外命令、重复命令或工作目录漂移均在工具 probe 前拒绝。相同 argv 可在同一冻结工作包中共享一次执行的 hash-bound evidence，每个 Task 保留独立 outcome。主会话通过固定完成记录校验命令接收少量完成信号，不无条件重跑子任务测试。

2026-09-16 本次补强验证：Qoder runner 30 个测试、分层与 registry 7 个测试、planning 113 个 Task 的 11 项合同检查通过。本次未改 Java 源码，未重跑 Java 完整构建。公开增量 Gate 实际为 `FAIL/missing-evidence-context`；缺少当前证据包时，正式验收保持 `BLOCKED`。

后续实际执行纠正：独立 Terra 验收曾通过 planner、evidence、issuer 三项公开增量 Task 验证。修复 `v3` 命令注册与执行器衔接，以及 JSON Schema 源文件被误认成证据路径的问题后，只运行此前未执行的 evidence/issuer 检查；executor/hash-DAG 回归合并运行一次，planner 不重复。主会话只校验完成记录、收据与哈希链，未重跑测试。这三项历史 Task 验证结果为 `PASS`；之后 policy/catalog 输入变化，原绑定不能用于当前验收。阶段独立审阅和目录决策尚未签发，历史失败同样保留。

## Codex issuer 调用边界

`IssuerPacketMaterializer(..., trusted_codex_context=...)` 要求 runner 提供精确的 `actor_id`、`session_id`、`parent_session_id`、`client` 四字段 mapping。从真实 runtime binding 派生时，`actor_id` 对应 `identity.agent_id`，其他三字段取实际值；不能直接传入含 `run_id`、`agent_id`、`parent_client` 的完整 binding。`_derive_codex_expected` 比较完整 mapping，额外字段会触发 `identity-drift`。attestation 也必须使用同一实际 host 身份，不能用随机 session 代替。

这项接口约束来自当前代码检查，尚不构成 package029 失败原因的完整运行复证。源作者不能签发自己的 Task 验证或独立审阅；源完整性 `PASS` 不能替代正式 Gate 收据。
