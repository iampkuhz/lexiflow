# LF-TSK-QLT-0007 Explicit Evidence Packet Implementation Review

> **Implementation acceptance:** `PASS`  
> **Catalog result:** `PENDING_GATE_RECEIPT`  
> **Incremental Gate CLI:** `PENDING` — `scripts/gates/cli.py` 尚不存在，因此 `python3 scripts/gates/cli.py run --mode incremental` 未运行。  
> **Accepted candidate:** Codex Takeover Rework 1

本独立复审接受 `LF-TSK-QLT-0007@1/1.0.0` 的当前实现。结论只证明两个 task-owned 文件满足 catalog descriptor、Gate control-plane design 和 `LF-GATE-EVIDENCE-001`；它没有签发 Gate receipt、没有把 produced contract 标记为 current，也不构成 catalog `PASS`。

Catalog task 的四条 acceptance criteria、文件范围与验证命令位于 [`planning/workstreams.yaml:1010-1068`](../../planning/workstreams.yaml#L1010-L1068)，acceptance case 位于 [`docs/acceptance-cases/phase-1.md:75-82`](../acceptance-cases/phase-1.md#L75-L82)。设计要求 Main Agent 显式提供六字段、绑定 raw artifacts 且不得从 free text 推断；issuer trust 由 `QLT-0014` 独立承担（[`docs/development/gate-control-plane-design.md:97-120`](../development/gate-control-plane-design.md#L97-L120)）。

## Reviewed snapshot

| 文件 | SHA-256 |
|---|---|
| [`scripts/gates/evidence_packet.py`](../../scripts/gates/evidence_packet.py) | `cd7b5ca53c35a4d303b65036a66bd0f1c6ae219d33c6fbe4c1b37905e0462eb3` |
| [`tests/gates/test_evidence_packet.py`](../../tests/gates/test_evidence_packet.py) | `bc00bb87b8708a7f7a8794db8cad06adb17cf68199a257120714b9936b901469` |

这些是最终 `PASS` 复审使用的 exact bytes。后续任何 byte change 都需要重新验收，不能沿用本记录的结论。

## Final validation evidence

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/gates -p 'test_evidence_packet.py' -q
115 tests: PASS

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/gates -p 'test_*.py' -q
246 tests: PASS

python3 -m scripts.gates.planning --root .
113 tasks / 11 registered checks: PASS

Independent one-off probes
190 probes: PASS for the reviewed contract
  188 contract-conformance probes: PASS
  2 boundary-characterization probes: expected boundary observed
```

独立矩阵没有把 115 个 checked-in tests 当作充分证据。它另外覆盖 exact nested schema/type、九字段 identity、terminal completion、真实 UTC、artifact 与 current-file drift、strict diff、dispatch-path-v1 truth table、scope/claims/reconciliation、initial/pre-link/post-link semantics、publication fault injection、8-way duplicate、foreign replacement、repo inode replacement，以及 verifier 的 nested deletion/extra/type/drift。对应 checked-in 回归入口位于 [`tests/gates/test_evidence_packet.py:1065-1754`](../../tests/gates/test_evidence_packet.py#L1065-L1754)、[`tests/gates/test_evidence_packet.py:1756-2497`](../../tests/gates/test_evidence_packet.py#L1756-L2497) 和 [`tests/gates/test_evidence_packet.py:2499-2667`](../../tests/gates/test_evidence_packet.py#L2499-L2667)。

## Preserved implementation and rejection history

历史结果没有被最终 `PASS` 覆盖。三次 Qoder process 都写出 terminal `finished`/exit `0` completion，但 process state、stdout prose 和局部测试数都不等于验收通过。三个 run 复用同一 session，并由 runner 分配不同 `agent_id` 与 `run_id`；identity 可分别在 initial [`task.json:23-29`](../../tmp/qoder-tasks/ed79b008-2202-4b03-ad8b-c8136173c675/task.json#L23-L29)、Rework 1 [`task.json:23-30`](../../tmp/qoder-tasks/1a12f3e6-acd6-4b18-ad21-774de3474233/task.json#L23-L30) 和 Rework 2 [`task.json:23-30`](../../tmp/qoder-tasks/68a21dfb-a627-41f7-8bff-f4a346c2783a/task.json#L23-L30) 核对；其 completion 分别位于 [`ed79…/completion.json:2-15`](../../tmp/qoder-tasks/ed79b008-2202-4b03-ad8b-c8136173c675/completion.json#L2-L15)、[`1a12…/completion.json:2-15`](../../tmp/qoder-tasks/1a12f3e6-acd6-4b18-ad21-774de3474233/completion.json#L2-L15) 和 [`68a2…/completion.json:2-15`](../../tmp/qoder-tasks/68a21dfb-a627-41f7-8bff-f4a346c2783a/completion.json#L2-L15)。

| 轮次 | 时点验证 | 独立结果 | 主要拒收原因 |
|---|---:|---|---|
| Qoder initial，run `ed79b008-2202-4b03-ad8b-c8136173c675` | 85 focused；run stdout 自报当时 190 full | `FAIL` | 六字段的类型、空值与 exact schema 不严；task/completion identity 与 terminal 状态没有双向对账；缺少 snapshot/diff/scope 完整绑定；dispatch 使用宽松匹配；source bindings 自由；publication 可覆盖或受 temp symlink 影响；pre-publication TOCTOU；verifier fail-open；determinism probe 无效；stored hash 不等于 final bytes。Initial handoff 的原始 contract 位于 [`task.json:2-22`](../../tmp/qoder-tasks/ed79b008-2202-4b03-ad8b-c8136173c675/task.json#L2-L22)，首轮拒收后的十类修复要求保存在 [`qlt-0007-rework-1.json:2-16`](../../tmp/qoder-tasks/qlt-0007-rework-1.json#L2-L16)。 |
| Qoder Rework 1，run `1a12f3e6-acd6-4b18-ad21-774de3474233` | 48 focused；run stdout 自报的 full count 不是接受证据 | `FAIL` | 仍接受 raw task 六个 runner identity 漂移；terminal set 错误；snapshot、raw/normalized scope、claims 与 reconciliation 不严；path grammar 和 binding-to-evidence 不完整；symlink ancestor 可穿越；short write、rollback、post-link publication 失败路径不安全；verifier 没有复用 exact schema/semantics；canonical identity 有缺口。逐项观察保存在 [`qlt-0007-rework-2.json:2-16`](../../tmp/qoder-tasks/qlt-0007-rework-2.json#L2-L16)。 |
| Qoder Rework 2，run `68a21dfb-a627-41f7-8bff-f4a346c2783a` | 30 focused；161 full | `FAIL` | raw task null/type/drift 仍可通过；dispatch parser 未被 scope/claim reconciliation 全程调用，claim/diff 语义仍有缺口；完整 semantics 没有在 pre-link/post-link 重跑；publication ownership 与 cleanup 不完整；strict verifier 没有 exact 复用 materializer semantics。该轮任务明确要求这些不变量（[`task.json:2-22`](../../tmp/qoder-tasks/68a21dfb-a627-41f7-8bff-f4a346c2783a/task.json#L2-L22)），局部 tests 退出零仍不足以接受。 |
| Codex takeover 初版 | 99 focused；230 full；183 independent probes | `FAIL` | 183 个 probes 中 168 个 contract cases 通过，13 个 probes 暴露 7 类缺陷，另有 2 个边界观察：runner identity 未兼容合法 canonical UUIDv7；strict diff 仍有组合/metadata/新增删除语义缺口；final FIFO 可阻塞；nested ancestor `ENOENT` 的 delete/rename absent semantics 错误；temp 第一次 `fstat` 故障残留；fresh publication 目录没有逐级 fsync parent/newdir；被语义解析的 JSON duplicate keys 未在任意层 typed reject。 |
| Codex Takeover Rework 1 | 115 focused；246 full；113 tasks/11 checks；190 independent probes | `PASS` | 7 类缺陷全部关闭；188 个 contract probes 全通过，2 个既定边界被准确刻画。最终结论只适用于上方两个 reviewed hashes。 |

Qoder 的 initial、Rework 1、Rework 2 均保持 `FAIL`；Codex takeover 初版也保持 `FAIL`。它们的局部通过、terminal completion 或 prose `PASS` 没有被回填为 structured evidence。

## Accepted implementation invariants

- **Exact v1 schema and issuer separation。** Packet、snapshot、task、subject、identity、attestation、六个 result fields、source bindings、scope、raw artifact descriptors 与 reconciliation 都使用 exact keys；任意层 issuer/authority/verifier/role 类字段被拒绝。入口定义位于 [`scripts/gates/evidence_packet.py:20-91`](../../scripts/gates/evidence_packet.py#L20-L91)，结构校验位于 [`scripts/gates/evidence_packet.py:1272-1440`](../../scripts/gates/evidence_packet.py#L1272-L1440)。
- **Identity and explicit results。** Supplied task、raw task 与 raw completion 的 task/version 及六个 runner identity 字段必须 exact、type-valid、非 null 且一致；raw task 的 `task_source`、`allowed_files`、`forbidden_files` 另须与 supplied task 完全一致。`finished`、`failed`、`completed` 是仅有 terminal statuses。Runner identity 接受 canonical RFC UUID version 1–8，包括 UUIDv7；publication identity 单独限制为 canonical lowercase UUIDv4。六个结果字段只能来自 explicit Main-Agent review，不读取 stdout、stderr、callback、exit code 或 prose（[`scripts/gates/evidence_packet.py:535-642`](../../scripts/gates/evidence_packet.py#L535-L642)、[`scripts/gates/evidence_packet.py:1279-1335`](../../scripts/gates/evidence_packet.py#L1279-L1335)、[`scripts/gates/evidence_packet.py:1525-1567`](../../scripts/gates/evidence_packet.py#L1525-L1567)）。
- **Strict JSON and exact bytes。** 被语义消费的 raw task、raw completion、snapshot、CLI input 与 stored packet 使用 duplicate-aware JSON parser；任意嵌套 duplicate key typed reject。所有 artifacts 先绑定 supplied lowercase SHA-256，再从安全 FD 读取 exact regular-file bytes；symlink ancestor/final symlink、FIFO、directory、device、path swap 和 changed bytes fail closed（[`scripts/gates/evidence_packet.py:123-403`](../../scripts/gates/evidence_packet.py#L123-L403)、[`scripts/gates/evidence_packet.py:1441-1523`](../../scripts/gates/evidence_packet.py#L1441-L1523)）。
- **Snapshot, diff, dispatch and reconciliation。** Snapshot 使用 exact `lexiflow.changed-file-snapshot.v1`，并核对当前文件或明确 absent path。Strict diff 支持合法 text、binary、rename 和 delete，拒绝 combined diff、未知 metadata、矛盾 section、额外 file section，以及缺失 `/dev/null` 的 new/delete 语义。`dispatch-path-v1` 只允许 literal、terminal `/*`、terminal `/**`，并在 containment、intersection、specificity、allowed/forbidden scope 与 claims 中统一使用；attested、snapshot、diff、raw scope、normalized scope、claims 与六键 reconciliation 必须完全一致（[`scripts/gates/evidence_packet.py:412-525`](../../scripts/gates/evidence_packet.py#L412-L525)、[`scripts/gates/evidence_packet.py:650-1205`](../../scripts/gates/evidence_packet.py#L650-L1205)、[`scripts/gates/evidence_packet.py:1211-1270`](../../scripts/gates/evidence_packet.py#L1211-L1270)、[`scripts/gates/evidence_packet.py:1572-1649`](../../scripts/gates/evidence_packet.py#L1572-L1649)）。
- **One complete semantics at every materialization stage。** `validate_semantics` 是唯一完整语义入口；materializer 在初始构建、pre-link 和 post-link 都复用它，包含 current task source、raw artifacts、snapshot members、diff、identity、bindings、scope、claims、reconciliation 与 fingerprint，不依赖推断或阶段性弱化（[`scripts/gates/evidence_packet.py:1712-1750`](../../scripts/gates/evidence_packet.py#L1712-L1750)、[`scripts/gates/evidence_packet.py:2098-2153`](../../scripts/gates/evidence_packet.py#L2098-L2153)、[`scripts/gates/evidence_packet.py:2189-2218`](../../scripts/gates/evidence_packet.py#L2189-L2218)）。
- **Immutable, durable and ownership-safe publication。** Output namespace 与 basename 由 explicit UUIDv4 唯一确定。目录创建逐级 fsync parent 与 new directory；private temp 使用 random name、`O_EXCL`/`O_NOFOLLOW`、完整 write loop、file fsync、inode binding 与 exclusive hard-link install。短写、partial write、首次 `fstat`、file/dir fsync、link before/after side effect、temp unlink、post-link drift、directory rename 和 repo inode replacement 都 typed fail closed；8-way 相同 ID 只有一个 winner。Rollback 只删除 owned final/temp，post-link foreign replacement 保留 foreign 且不残留 private name（[`scripts/gates/evidence_packet.py:223-274`](../../scripts/gates/evidence_packet.py#L223-L274)、[`scripts/gates/evidence_packet.py:1753-1974`](../../scripts/gates/evidence_packet.py#L1753-L1974)、[`scripts/gates/evidence_packet.py:1987-2186`](../../scripts/gates/evidence_packet.py#L1987-L2186)）。
- **Strict verifier reuses the same contract。** Verifier 绑定 exact namespace、UUIDv4 basename、external lowercase hash、canonical bytes、publication ID 与 content fingerprint，然后复用完整 `validate_semantics`；任意 nested deletion/extra/type/drift、task source 或 snapshot/current artifact drift、issuer field、locator/hash/fingerprint mismatch 均返回 typed `EvidencePacketError`（[`scripts/gates/evidence_packet.py:2221-2291`](../../scripts/gates/evidence_packet.py#L2221-L2291)）。

## Explicit boundaries

1. `tests` validation artifacts 是由 locator/hash 绑定的 opaque bytes。若某个 test artifact 恰好包含 JSON，materializer 不解释其对象语义，因此其中 duplicate key 不走 JSON parser；这与设计规定的“Raw artifacts 只被 hash 绑定”一致（[`docs/development/gate-control-plane-design.md:108-120`](../development/gate-control-plane-design.md#L108-L120)）。Raw task、completion、snapshot、CLI input 与 packet 等实际被解析的 JSON 仍在任意深度拒绝 duplicate key（[`tests/gates/test_evidence_packet.py:1065-1148`](../../tests/gates/test_evidence_packet.py#L1065-L1148)）。
2. Publication 的本地安全模型假定同 UID 非协作 writer 无法猜中并抢占随机 private temp name；如果外部 writer 能在 `O_EXCL` 创建后精确抢占该不可预测私有名，cleanup 可能把该 replacement 当作私有条目处理。共享 final name 不依赖这一假定：独立 post-link foreign replacement probe 已证明 foreign 被保留、owned final/temp 被清理。跨主机无协作写入和任意同进程 private monkeypatch 也不由本 task 消除；相关 ownership/rollback tests 位于 [`tests/gates/test_evidence_packet.py:1924-2113`](../../tests/gates/test_evidence_packet.py#L1924-L2113) 和 [`tests/gates/test_evidence_packet.py:2307-2472`](../../tests/gates/test_evidence_packet.py#L2307-L2472)。

这两个边界没有绕过 task 的 catalog acceptance criteria，也没有留下当前实现 defect。

## Catalog boundary

OpenSpec change 中 `LF-GATE-EVIDENCE-P1-001` 仍为未勾选状态（[`openspec/changes/establish-lexiflow-foundation/tasks.md:50-52`](../../openspec/changes/establish-lexiflow-foundation/tasks.md#L50-L52)）。统一 Gate CLI 的声明命令仍明确处于“实现文件创建前不得描述为已执行”的状态（[`README.md:23-27`](../../README.md#L23-L27)）。当前没有 current-input validation receipt、independent-review receipt、hash-DAG verification 或 catalog-decision receipt；因此本地 implementation review 不能升级为 catalog `PASS`。

```text
Implementation acceptance: PASS
Catalog result: PENDING_GATE_RECEIPT
Incremental Gate CLI: PENDING
Produced contract current: NO
Gate receipt: NONE
Catalog PASS: NO
```
