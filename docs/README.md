# LexiFlow Documentation

按下面顺序阅读：

1. [Product Brief](product/product-brief.md)：问题、目标、MVP 与非目标。
2. [Phase 1 Architecture](architecture/phase-1.md)：Domain、依赖方向与核心数据流。
3. [Phase 1 Cross-cutting Contracts](architecture/phase-1-cross-cutting-contracts.md)：Semantic port/result、缓存、威胁、可观测性与来源适配合同。
4. [Decisions](architecture/decisions.md)：推荐方案、替代方案与 trade-off。
   [Post-approval Toolchain Verification](development/post-approval-toolchain-verification.md) 记录 ADR-010 获批后的版本锁定、干净重建、负例与证据计划；当前未执行。
5. [Phase 1 Review Checklist](reviews/phase-1-checklist.md)：进入下一阶段前需要确认的边界和初始目标。
6. [G1 Decision Package](reviews/g1-decision-package.md)：供用户评审的架构、初始 SLO、技术栈与 Gate 边界。
   [G1 Catalog Task Evidence Map](reviews/g1-task-evidence-map.md) 展开当前 30 个 blocking-closure task、证据缺口与 receipt 签发顺序。
   [G1 Bootstrap Variance](reviews/g1-bootstrap-variance.md) 记录 Gate 控制面建立前的证据限制和禁止回填规则。
   [QLT-0002 Run 1 Review](reviews/qlt-0002-run-1-review.md) 记录首轮 validator 被拒绝的反例与同 session 返工链。
   [QLT-0002 Rework 1 Review](reviews/qlt-0002-rework-1-review.md) 记录 39 个测试通过后仍被 mutation probes 拒绝的同步漂移、ID、owner、Gate 与结构化结果缺口，以及最后一次同 session 返工。
   [QLT-0002 Final Implementation Review](reviews/qlt-0002-final-review.md) 保留最后一次 Qoder 失败，并记录 Codex takeover、86 个测试、42 条独立 probe 与 implementation PASS/catalog pending 边界。
   [Gate Control Plane Design](development/gate-control-plane-design.md) 定义唯一 CLI、显式 evidence/issuer context、三态、不可覆盖 receipt 和 current-input chain；[独立设计审阅](reviews/gate-control-plane-design-review.md) 记录可重算 fingerprint、issuer hash 与实现边界。
   [Gate JIT Task Draft Review](reviews/gate-jit-task-draft-review.md) 记录八个原子实现任务、可信 issuer authority、串行 CLI route 与 `ARCH-0008@2` 迁移核算。
   [Gate JIT Catalog Activation Review](reviews/gate-jit-catalog-activation-review.md) 记录 113-task 目录、全部版本 pin、30-task blocking closure 与 activation 非 READY/PASS 的独立复核。
   [QLT-0005 Dispatch Preflight Design Review](reviews/dispatch-preflight-design-review.md) 记录纯输入、canonical claim、owner、write overlap 与 contract-writer 设计的独立 PASS；实现和 catalog receipt 仍待完成。
7. [Master Plan](roadmap/master-plan.md)：Phase 1–7、WBS/DAG 与滚动拆分规则。
   [Phase 1 Status](roadmap/phase-1-status.md) 记录当前任务与证据。
8. [Acceptance Cases](acceptance-cases/README.md)：稳定验收 ID 与未来测试证据。
9. [Agent Subtasks](development/agent-subtasks.md)：Codex/Qoder handoff、回调和真实 PASS 语义。
10. [Reference Audit](references/feipi-session-browser-java.md)：从参考项目复用与未复用的约束。

长期产品行为以 `openspec/specs/` 为准；当前工作只在 `openspec/changes/` 跟踪；机器契约在 `harness/`。
