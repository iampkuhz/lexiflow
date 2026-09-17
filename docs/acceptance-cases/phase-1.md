# Phase 1 Acceptance Cases

## LF-CAPTION-001 · P0 · English first

- **Source:** Product Architecture / 英文字幕不等待语义服务
- **Given** 客户端收到当前英文 caption
- **When** 后端、Redis 或 Semantic Provider 超时
- **Then** 英文在客户端本地时延预算内显示，且没有整句中文替换
- **Evidence owner:** Chrome Extension integration test（Phase 5）
- **Status:** accepted-design

## LF-ENRICH-001 · P0 · Rule and model separation

- **Source:** Product Architecture / Rules 与 Models 分离
- **Given** 一个用户已高熟悉度的候选 span
- **When** Enrichment 执行 need-hint 判断
- **Then** 候选可在不调用 Semantic Provider 的情况下被抑制
- **Evidence owner:** Enrichment architecture/unit test（Phase 4）
- **Status:** accepted-design

## LF-PROFILE-001 · P0 · Profile source of truth

- **Source:** Product Architecture / 模块化单体
- **Given** 同一用户在两个浏览器实例观看内容
- **When** 一个实例明确标记 term 为 known
- **Then** 服务端 Profile 版本前进，另一实例按同步合同看到新版本
- **Evidence owner:** Backend/Extension contract test（Phase 3/6）
- **Status:** accepted-design

## LF-LEARNING-001 · P0 · Event idempotency

- **Source:** Product Architecture / 学习行为可重放且幂等
- **Given** 多设备重复上传同一个 event identity
- **When** Learning 投影处理这些请求
- **Then** 只形成一份有效学习证据并只更新一次 Profile
- **Evidence owner:** Learning persistence/integration test（Phase 2/6）
- **Status:** accepted-design

## LF-ADAPTER-001 · P1 · Source independence

- **Source:** Product Architecture / YouTube 是 Content Adapter
- **Given** 新的 Content Adapter 能产生规范化片段与上下文
- **When** 它接入组合根
- **Then** Vocabulary、Learning 与 Enrichment 核心不需要依赖来源平台代码
- **Evidence owner:** Module-boundary test（Phase 3）
- **Status:** accepted-design

## LF-AGENT-001 · P0 · Non-busy-wait delegation

- **Source:** Agent Execution / 主 Agent 无 busy wait
- **Given** Qoder run 已启动
- **When** 任务仍在执行且没有 completion
- **Then** 主 LLM 不循环查询；后台兜底首次不早于 300 秒，后续间隔不短于 600 秒
- **Evidence owner:** Harness lifecycle test（Phase 1）
- **Status:** implemented-pending-gate

## LF-ENRICH-002 · P0 · Pending requires durable handoff

- **Source:** Product Architecture / Pending work 有持久证据
- **Given** fast lane 可返回确定性 annotation
- **When** semantic handoff commit 失败
- **Then** 响应不能宣称语义任务 pending，英文和 fast result 仍可使用
- **Evidence owner:** Enrichment/application failure test（Phase 4）
- **Status:** accepted-design

## LF-LEARNING-002 · P0 · Display is an observed fact

- **Source:** Product Architecture / 生成、投递、展示与点击分离
- **Given** slow annotation 已生成但客户端切换 caption，没有实际渲染
- **When** 客户端消费晚到结果
- **Then** 不上传 `HintDisplayed`，Learning 不据此增加展示证据
- **Evidence owner:** Extension/Learning journey test（Phase 5/6）
- **Status:** accepted-design

## LF-GATE-EVIDENCE-001 · P0 · Explicit result evidence

- **Source:** Gate Control Plane / `LF-TSK-QLT-0007`
- **Given** 一个已结束的 Agent run 及 Main Agent 的结构化复核
- **When** materializer 创建 result evidence packet
- **Then** 六个结果字段与 task/completion/stdout/stderr/diff/test hashes 完整绑定，且不从 free text 猜测字段；identity 只接受 qoder/codex，Codex per-Task raw task 另绑定稳定 package、ordered Task、target、caller contract 与 runner identity
- **And** Qoder task/completion 作为 issuer provenance 时接受 runner 的 pretty JSON whitespace、冻结原始 locator/hash，并拒绝 duplicate key、identity drift、非终态或 stale completion
- **Evidence owner:** `tests/gates/test_evidence_packet.py`
- **Status:** implementation-reviewed; catalog receipt pending

## LF-GATE-ISSUER-001 · P0 · Trusted issuer provenance

- **Source:** Gate Control Plane / `LF-TSK-QLT-0014`
- **Given** 一个请求签发 Gate receipt 的 actor
- **When** issuer materializer 验证其权限
- **Then** 只接受 authority registry 中固定 verifier 证明的 provenance，自报 actor、role 或 verifier 不能形成信任
- **Evidence owner:** `tests/gates/test_issuer_packet.py`
- **Status:** implementation-reviewed; catalog receipt pending

## LF-GATE-PLAN-001 · P0 · Frozen deterministic plan

- **Source:** Gate Control Plane / `LF-TSK-QLT-0008`
- **Given** current catalog、显式 evidence/issuer packets 与 versioned registry
- **When** 同一 mode 编译 Gate plan
- **Then** 不产生写入或 run identity，且相同 canonical inputs 得到相同 content fingerprint；planner 按 qoder/codex 使用互斥 raw-task schema，只从 current catalog 投影 owner/discovery/file claims，并让 TASK_VALIDATION 选择 checks、review/catalog 冻结零 checks 的 evidence-consumption layer
- **Evidence owner:** `tests/gates/test_gate_planner.py`
- **Status:** implementation-reviewed; catalog receipt pending

## LF-GATE-DISPATCH-001 · P0 · Fail-closed dispatch preflight

- **Source:** Multi-agent scheduling safety / `LF-TSK-QLT-0005`
- **Given** frozen candidate、catalog/path snapshot 与完整 nonterminal active-instance declaration
- **When** preflight reconciles Task identity、owner、allowed/write claims、case/symlink safety and public-contract writers
- **Then** only disjoint canonical writers PASS; incomplete or unsafe input FAIL, while confirmed owner/claim/write/contract conflicts BLOCKED
- **Evidence owner:** `tests/gates/test_dispatch_preflight.py`
- **Status:** implementation-main-verified; integrated review and catalog receipt pending

## LF-GATE-CHECK-001 · P0 · Typed fail-closed checks

- **Source:** Gate Control Plane / `LF-TSK-QLT-0009`
- **Given** frozen registry 中一组 required checks
- **When** executor 执行并聚合结果
- **Then** 只运行 fixed argv，且 skipped、not-run、empty、malformed 或未证明的 exit zero 均不能得到 PASS
- **Evidence owner:** `tests/gates/test_gate_executor.py`
- **Status:** proposed-implementation

## LF-GATE-VALIDATION-001 · P0 · Observable immutable validation run

- **Source:** Gate Control Plane / `LF-TSK-QLT-0010`
- **Given** 一个合法 TASK_VALIDATION plan
- **When** 唯一 Gate CLI 启动检查
- **Then** checker 前持久化并向调用方 flush START/run_id，最终 receipt 不可覆盖，status 只读固定 run 路径，且只有 TASK_VALIDATION 能调用 delivery executor
- **Evidence owner:** `tests/gates/test_gate_lifecycle.py`
- **Status:** proposed-implementation

## LF-GATE-REVIEW-001 · P0 · Independent review chain

- **Source:** Gate Control Plane / `LF-TSK-QLT-0011`
- **Given** 一份 immutable TASK_VALIDATION receipt
- **When** 独立 reviewer 通过同一 CLI 发布 review
- **Then** producer 自审、identity 漂移或非空 delivery checks 均失败；reviewer write-set 必须与 hash-bound current plan 对账，且 verifier 必须重验 validation changed-file snapshot 对应的 current subject bytes/state；只读 review 的空 write-set 必须来自通过 materializer 与 planner 的 empty snapshot/diff/changed-files packet（包括不同 actor 的 Codex work-package per-Task projection），调用方自报空列表不能掩盖实际 subject mutation；subject receipt 保持不变且 delivery validation 不重跑
- **Evidence owner:** `tests/gates/test_independent_review.py`
- **Status:** proposed-implementation

## LF-GATE-HASH-001 · P0 · Acyclic evidence graph

- **Source:** Gate Control Plane / `LF-TSK-QLT-0012`
- **Given** plan、manifest、leaf 与 prior receipt 引用图
- **When** verifier 只读核对 locator、identity 与 hash
- **Then** self-edge、back-edge、alias、missing node、changed bytes 或 cycle 均失败
- **Evidence owner:** `tests/gates/test_hash_dag.py`
- **Status:** proposed-implementation

## LF-GATE-CATALOG-001 · P0 · Current catalog decision

- **Source:** Gate Control Plane / `LF-TSK-QLT-0013`
- **Given** current acceptance registry、validation/review/hash evidence 与所有 required dependency receipts
- **When** 唯一 CLI 发布 CATALOG_DECISION
- **Then** 只有零-check evidence-consumption plan、无孤儿或重复 case、canonical locator/hash 均 current、前序链 validation subject snapshots 重验 current、review 独立、每份前序/依赖 receipt issuer 均按 current authority registry 验证且依赖为 current-input PASS 时才可 catalog PASS；同字节 alias、自造 issuer receipt、非空 delivery checks 或 review 后 subject mutation必须失败
- **Evidence owner:** `tests/gates/test_catalog_decision.py`
- **Status:** proposed-implementation
