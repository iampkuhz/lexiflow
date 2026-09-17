# Current execution evidence

Phase 1 的正式 Gate 在 frozen plan 上执行 checks，先保存 START，再发布不可变 receipt。缺少 current packet/issuer 时 fail closed；本页不会把 publisher signal、历史 review、ACK 或 exit zero 称为正式验收 PASS。

Codex package 统一使用 [`agent-policy.manifest.yaml`](../../harness/agent-policy.manifest.yaml) 的 canonical result contract。Caller 只提供稳定 Task/change versions、范围、验收与失败策略；runner 绑定真实 parent/session/client 与稳定的执行 actor/agent，生成唯一 run ID。Caller 不能注入 runner identity，缺 runtime session 不能使用随机 UUID 代替。每个 Task 保留自己的 projection、六字段 outcome、completion 和 signal；最终 package completion 只索引精确 per-Task artifacts 和 hashes。

完成信号只给 locator、run identity、验证摘要与少量 blocker。Main 通过固定 `verify --run-id` 命令检查 schema、current catalog projection 和 artifact hashes；随后按风险选择必要功能验证，不读取完整子任务上下文，不无条件重跑全部测试。正式 TASK_VALIDATION 的 delivery checks 只执行一层，review/catalog 仍为零 delivery checks。

`client` 表示工具类型，不是唯一执行者。两个 Codex 实例可以分别充当 producer 和可信 issuer；执行 actor/agent 与 run/issuer instance 必须不同且经 host verifier 认证；真实 collaboration session 可以共享，不能制造新 session。独立 review 还必须排除 subject producer 与 validation issuer，保持零 subject write set 和 current-input/hash 验证。同 actor/agent/run/instance、伪造 host context、stale/replayed identity 与越权 receipt kind 继续是 FAIL。

Canonical publisher 只发布显式结构化数据，不运行 subprocess、不解析日志推断成功、不签发正式 receipt，也不回退到旧布局。历史产物可以保留，但不能伪装成新 run 或修改后冒充 current inputs。产品 Java 始终使用 exact JDK 25；确定性 Java 规则只由 Gradle/Java 执行。

完成产物检查入口为：

```bash
python3 -m scripts.harness.codex_work_package verify --run-id <run_id> --root .
```

该入口的 PASS 只证明 canonical artifacts 的完整性与一致性；Task acceptance、独立 review 和 G1 用户决定仍由对应不可变 Gate receipts 证明。

Publisher API 接收 runner 提供的 identity；它校验结构与绑定，不负责认证 host 身份。`verify` 复核精确 per-Task key set、固定 locator、完整投影与 current catalog、六字段 outcome、外部证据 hash/bytes 和聚合状态。可信 issuer 的 host verifier 仍是身份认证边界。

历史切片：2026-09-16 的 package `LF-WP-QLT-CANONICAL-EXECUTION-EVIDENCE-019` 已完成实现与产物检查，Main 在补强 verifier 后运行 9 个 publisher 测试和 51 个规划、注册表及分层集成测试，均通过。实际子代理环境的 `session_id` 与 `parent_session_id` 相同；原 outcome 的 `changed_files` 为空，也不足以证明源码交付范围。因此保留原不可变记录，但不把该 package 用作正式独立验收来源。公开增量 Gate 实际返回 `FAIL/missing-evidence-context`，current receipt closure 为 `BLOCKED`。后续须使用真实且可认证的独立执行实例与如实记录的源码证据，不能改写 session、completion 或历史 receipt。

当前变更为 `connect-runtime-bound-gate-evidence`。工作包 `LF-WP-QLT-RUNTIME-GATE-EVIDENCE-020`（预计 265 分钟）已通过回调完成，Main 使用固定命令核对当前 Task pins 与产物 hashes，完整性检查为 PASS，未重跑其 136/46/63 个测试。正式验收为 BLOCKED：未保存改动前源码字节，公开 Gate 缺少当前证据包，新增 Main projection 的角色限制也尚未满足（结构校验仍接受 child agent，不能据此证明 Main-only 权限）。保留原 completion；不改写身份、源码基线或历史 receipt。本次仅一次兜底状态检查，发生在 300 秒之后，之后通过回调接收完成；当前 Codex/Qoder 活跃数均为零。历史 019 和旧收据不满足新 Task pins。审批顺序见 [Phase 1 acceptance order](phase-1-acceptance-order.md)。

后续 Main correction 保存实际 before bytes，补上 canonical `/root` 限制、Main schema 的真实 identity reconciliation，以及 issuer/materializer/planner 对真实 Codex actor path 的兼容；9 项定向回归通过。三个新来源按实际 Git HEAD 中 scoped paths 不存在的事实生成当前导入 diff/snapshot，保留 `/root` 与原实现者的 provenance，不冒充 package020 的物理改动基线。source audit 的 PASS 仅证明候选导入/哈希与定向准备检查，未运行的 declared Task suite 仍由独立 TASK_VALIDATION 执行。

`LF-WP-QLT-CURRENT-VALIDATION-022` 已经使用真实 actor `/root/current_validation_terra` 和真实共享 session 生成可信 issuer。Main 固定 verifier 核对其完成记录后独立确认 planner 的旧简单 ID 检查拒绝该 actor，三次 public Gate 均在 checker selection 前失败，执行检查数为零。历史失败记录保留；修正后通过同一独立执行者的新 run、fresh attestation 和当前输入继续验收，不借用旧 issuer 冒充 Main 的独立验证。
