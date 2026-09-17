# LF-TSK-QLT-0008 Pure Gate Planner Implementation Review

> **Implementation acceptance:** `PASS`  
> **Catalog result:** `PENDING_GATE_RECEIPT`  
> **Incremental Gate CLI:** `PENDING` — `scripts/gates/cli.py` 尚不存在。  
> **Accepted candidate:** Codex Takeover Final Rework

本独立复审接受 `LF-TSK-QLT-0008@1/1.0.0` 的当前实现。结论只证明三个 task-owned 文件满足 catalog descriptor、Gate control-plane design 和 `LF-GATE-PLAN-001`；它没有执行 checker、签发 Gate receipt、把 `gate-plan-registry@1.0.0` 标为 current，或构成 catalog `PASS`。

Catalog task 的四条 acceptance criteria、文件范围与验证命令位于 [`planning/workstreams.yaml`](../../planning/workstreams.yaml)，acceptance case 位于 [`docs/acceptance-cases/phase-1.md`](../acceptance-cases/phase-1.md)。Planner 消费已物化的 QLT-0007 result packet 与 QLT-0014 issuer packet，冻结 current catalog、task/dependency、scope、registry、checks 与 expectations；它不重新推断 result 字段或 authority。

## Reviewed snapshot

| 文件 | SHA-256 |
|---|---|
| [`scripts/gates/planner.py`](../../scripts/gates/planner.py) | `69a014bde08fc6fc599845deb842fc5336ff19bc18d0d7c538eea2d48cf0f71a` |
| [`harness/gate-check-registry.yaml`](../../harness/gate-check-registry.yaml) | `73edbaf8f3b90fabcbdc9bfd4d6cdd002fe4ad997c7f979f0b0c7d8107b5bfb4` |
| [`tests/gates/test_gate_planner.py`](../../tests/gates/test_gate_planner.py) | `735f764b635418dda1e25b9f1ccbdb78b7784d68a5a4d19db7b351d2d513c5d5` |

这些是最终 `PASS` 复审使用的 exact bytes。后续任何 byte change 都需要重新验收。

## Final validation evidence

```text
Main Agent focused validation
python3 -m unittest discover -s tests/gates -p 'test_gate_planner.py'
49 tests: PASS

Main Agent full Gate validation
python3 -m unittest discover -s tests/gates -p 'test_*.py'
295 tests: PASS

python3 -m scripts.gates.planning --root .
113 tasks / 11 checks: PASS

Main Agent additional adversarial probes
16/16: PASS

Fresh read-only independent reviewer
49/49 focused; 295/295 full; 113 tasks / 11 checks; 30/30 adversarial: PASS
```

两组额外矩阵没有把 checked-in tests 当作充分证据。它们独立覆盖真实 QLT-0007/0014 packets、动态非 QLT subject、checker/subject owner 分离、legacy/current catalog lineage、missing/extra/duplicate/reordered inventory、entry hash 重算、effect/criterion/risk 绑定、RFC 8785 UTF-16 key order、recursive YAML、non-NFC trigger、safe reads、TOCTOU、fingerprint coverage、zero-write、live-full missing input 和 hermetic-full 9/9 selection。九条当前 registry entry hash 也逐条重新计算一致。

## Preserved implementation and rejection history

历史 `FAIL`/`BLOCKED` 没有被最终 `PASS` 覆盖。三次 Qoder process 都以 exit `0` 写出 terminal completion，但 process state、stdout prose 和局部测试不等于实现验收。它们复用 session `e8ad173f-53e4-420c-9be2-a1e259bfe851`，每次使用不同的 agent/run identity。

| 轮次 | Run | 时点结果 | 独立结论 |
|---|---|---|---|
| Qoder initial | `3caa9a88-0f2f-4b9f-987c-168d811e6606` | 自报 21 focused、267 full 为 `PASS` | `FAIL`：fixture 没有证明真实 QLT-0007/0014 packet contract，catalog/registry/selection/safety 语义不完整。 |
| Qoder Rework 1 | `38f70527-5599-4d42-aa42-30b013b56f10` | 明确报告 `BLOCKED`；21 项 fixture 失败 | `FAIL`：真实 materializer、完整 identity/reconciliation 与测试仍未接通。 |
| Qoder Rework 2 | `5a8ce988-e652-413e-beb7-2b180db71dae` | 明确报告 `BLOCKED`；3/21 通过 | `FAIL`：多数 compile path 仍因不合格 packet fixture 失败。 |
| Codex takeover 初版 | — | 39 focused、285 full、113 tasks/11 checks | `FAIL`：catalog null/missing 字段、effect/expectation 绑定、JCS UTF-16、recursive YAML、non-NFC 与动态 registry inventory 尚未全部 fail closed。 |
| Codex Takeover Final Rework | — | 49 focused、295 full、16 Main-Agent probes | `PASS`：再经 fresh reviewer 30/30 独立 probes 接受。 |

Qoder 的 initial 与两次 rework、Codex takeover 初版均保持原结论；不得把它们的 exit `0`、局部测试或 prose 改写为 structured evidence。

## Accepted implementation invariants

- **Pure current-input compiler.** `compile_plan` 不创建 run/plan identity，不读取 clock、环境或目录扫描结果，不执行 process，也不写文件。所有使用的 locator/hash 被纳入 `consumed_inputs`，结束前逐项 revalidate；repo ancestor/root/leaf 替换、symlink、FIFO、device、missing 或 changed bytes fail closed。
- **Real upstream packets.** QLT-0007 packet 通过 `verify_packet_strict` 复核，QLT-0014 packet 通过固定 issuer registry、authority evidence、receipt authorization、subject identity、UTC/UUID/provenance 与 content fingerprint 校验。Planner 冻结 packet，而不重跑 authority verifier 或从 completion/stdout 推断结果。
- **Catalog-derived registry inventory.** 完整 ordered subject set 从 catalog traversal 中的 explicit `validation_command` 派生，另有唯一版本化 legacy 声明 `QLT-0002@1/1.0.0`。Planner 没有硬编码 QLT-0009–0013 或未来 32-entry inventory；missing、extra、duplicate、reorder、stale command/version/change 均失败。
- **Checker owner and subject owner separation.** Registry 与每条 check owner 固定为 `LF-WS-QLT`；`subject_task_id` 的 current task/change version、validation command 与 effective business owner 从 catalog 独立解析并冻结。ARCH 等非 QLT subject 能生成真实 plan；伪造任一 owner 都失败。
- **Strict canonical registry.** Entry exact keys、fixed argv、cwd、timeout、modes、terminal triggers、consumed inputs、outcome contract、criterion/effect mappings 与 hash 全部校验。Registry entry 与 plan fingerprint 使用 RFC 8785 的 UTF-16 key order；unsafe integer、float、surrogate、duplicate JSON/YAML key、recursive YAML alias 和 non-NFC path 都 typed fail closed。
- **Exact selection and expectation binding.** Incremental mode 总是选择 declared command，并加入 changed-file terminal trigger 命中的 entries；full mode按 registry 顺序选择所有 full-enabled entries。每个 selected check 冻结 `subject_task` identity、selection reasons、explicit effect status 与 entry hash；plan expectations 为每个 catalog criterion、selected effect 和 explicit risk 生成稳定 item record，risk 固定要求 `impact`、`mitigation`、`fallback`、`remaining_limitation`。
- **Complete fingerprint.** Plan 除 `content_fingerprint` 本身外的所有字段都进入 canonical hash；task/dependency/subject/owner/expectation/registry projection 的任意变更都会改变 fingerprint。

## Explicit boundaries

1. 当前 registry 已声明 QLT-0009–0013 的 future consumed inputs，但这些实现文件尚不存在。Incremental QLT-0008 plan 只读取 selected input并可 `PASS`；live full mode会如实返回 typed `FAIL/unsafe-locator`。在 hermetic fixture 补齐未来输入后，full mode按顺序选择 9/9 entries。这个结果不冒充 current full Gate `PASS`。
2. QLT-0007/0014 已接受 packet bytes 使用其既有 canonical JSON contract；planner/registry 新产物使用 RFC 8785 UTF-16 key order。Planner 明确区分两种 versioned contract，避免把新排序规则追溯施加到已接受 upstream packet。
3. `scripts/gates/cli.py`、QLT-0009 executor、receipt store、independent-review/hash/catalog-decision routes 尚未实现。Planner 只冻结 fixed argv，不执行 checker；当前没有 current-input validation receipt、current produced contract 或 catalog decision。

## Catalog boundary

OpenSpec 中对应 change-local implementation checklist 已勾选；该勾选只记录本页绑定 hashes 的实现验收，不能升级为 catalog `PASS`。正式 receipt 仍须取得 current `QLT-0002`/`QLT-0003` hard prerequisites，消费 current `QLT-0007`/`QLT-0014` contracts，并经 QLT-0009–0013 的 validation、independent review、hash verification 与 catalog decision 链证明当前 hashes。

```text
Implementation acceptance: PASS
Catalog result: PENDING_GATE_RECEIPT
Incremental Gate CLI: PENDING
Produced contract current: NO
Gate receipt: NONE
Catalog PASS: NO
```
