# QLT-0014 Trusted Issuer Packet Implementation Review

> **Implementation acceptance:** `PASS`  
> **Catalog result:** `PENDING_GATE_RECEIPT`  
> **Incremental Gate CLI:** `PENDING` — `scripts/gates/cli.py` 尚不存在，因此 `python3 scripts/gates/cli.py run --mode incremental` 未运行。

本独立复审接受 `LF-TSK-QLT-0014@1/1.0.0` 的当前实现。结论只证明三个 task-owned 文件满足 catalog descriptor、Gate control-plane design 和 `LF-GATE-ISSUER-001`；它没有签发 Gate receipt、没有把 produced contract 标记为 current，也不构成 catalog `PASS`。

## Reviewed snapshot

| 文件 | SHA-256 |
|---|---|
| [`scripts/gates/issuer_packet.py`](../../scripts/gates/issuer_packet.py) | `9107beb347d464d30fcf9cca9cc1cab42f74533aa0603f9d6db50174529082cf` |
| [`tests/gates/test_issuer_packet.py`](../../tests/gates/test_issuer_packet.py) | `11b57a06c63c188efc906b0ca48a1b0a418e4a1fac796f8fca00f67992ab64d0` |
| [`harness/gate-issuer-authorities.yaml`](../../harness/gate-issuer-authorities.yaml) | `caa97993edd0a7ef927ed573f6928a04f0b5e1e2a7478e67e775fbf66eb5bf75` |

这些是最终 PASS 复审使用的当前 bytes。任务的四条 catalog acceptance criteria 位于 [`planning/workstreams.yaml:1069-1100`](../../planning/workstreams.yaml#L1069-L1100)，acceptance case 位于 [`docs/acceptance-cases/phase-1.md:84-91`](../acceptance-cases/phase-1.md#L84-L91)，设计职责位于 [`docs/development/gate-control-plane-design.md:735-744`](../development/gate-control-plane-design.md#L735-L744)。

## Validation evidence

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/gates -p 'test_issuer_packet.py'
Ran 45 tests
OK

Independent stable-forged verifier matrix
84 cases: PASS

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/gates -p 'test_*.py'
Ran 179 tests
OK

PYTHONDONTWRITEBYTECODE=1 python3 -m scripts.gates.planning --root . --verbose
113 tasks / 11 registered checks: PASS
```

84-case matrix covered human and Qoder at initial, pre-link and post-link verification points. It independently varied actor, session, parent, client, run, role, authorization, replay claims, and provenance locator/hash/kind/set; every forged result failed with no final packet, temporary packet, or new replay claim. The checked-in four-actor form of the same invariant is at [`tests/gates/test_issuer_packet.py:1098-1231`](../../tests/gates/test_issuer_packet.py#L1098-L1231).

## Rework history

四次拒收都保留了 `FAIL`，没有把局部修复或测试进程退出零写成验收通过。

| 独立复审轮次 | 拒收原因 |
|---|---|
| Initial review，进入 Rework 1 | attestation ID 与 nonce 被合并为一个 replay identity；失败可留下 partial final；registry/evidence 存在 TOCTOU；CI registry constraints 不完整；`subject_identity` 可缺失。 |
| Rework 1，进入 Rework 2 | 只做 pre-link 复核，真实 link window 仍可漂移；critical path 没有重新执行完整 freshness 语义；CI ref 只做前缀判断，未验证完整 Git ref grammar。 |
| Rework 2，进入 Rework 3 | 固定 verifier 若稳定返回字段形状合法但 identity、authorization 或 provenance 漂移的对象，外层缺少从原始 evidence 独立重推的绑定，伪造输出可建立信任。 |
| Rework 3，进入 Rework 4 | `registry`/`authorities` 公开视图仍可深层修改；磁盘 registry 未变时可在内存给 Qoder 增加 `CATALOG_DECISION`，形成 public-mutation false pass。 |

Rework 4 以递归冻结配置、每次从 current registry bytes 严格重解析、完整 semantic fingerprint 对账及独立 verifier-output derivation 关闭了最后一轮缺陷。公开配置变更、同 bytes/hash 的第 2、3、4 次 parser 语义漂移，以及 Qoder 权限提升均已独立复现为 fail closed；对应 tests 位于 [`tests/gates/test_issuer_packet.py:455-635`](../../tests/gates/test_issuer_packet.py#L455-L635)。

## Accepted security invariants

- 请求面只有 `issuer_instance_id` 和 `receipt_kinds`；actor、authority、verifier、role、session、client 和 authorization 不能由请求选择。四类 verifier ABI 固定在 [`scripts/gates/issuer_packet.py:33-70`](../../scripts/gates/issuer_packet.py#L33-L70) 与 [`harness/gate-issuer-authorities.yaml:7-65`](../../harness/gate-issuer-authorities.yaml#L7-L65)，路由没有 fallback 或搜索路径（[`scripts/gates/issuer_packet.py:426-543`](../../scripts/gates/issuer_packet.py#L426-L543)）。
- 初始配置使用 frozen dataclass、递归 `MappingProxyType` 和 tuple；公开 views 无 setter 或 mutable nested value（[`scripts/gates/issuer_packet.py:139-166`](../../scripts/gates/issuer_packet.py#L139-L166)、[`scripts/gates/issuer_packet.py:300-424`](../../scripts/gates/issuer_packet.py#L300-L424)）。
- initial、pre-link 和 post-link 每次都从 registry 的 exact locator 重读并 strict parse，同时比较 raw SHA-256 与完整 canonical semantic fingerprint（[`scripts/gates/issuer_packet.py:454-464`](../../scripts/gates/issuer_packet.py#L454-L464)、[`scripts/gates/issuer_packet.py:873-887`](../../scripts/gates/issuer_packet.py#L873-L887)、[`scripts/gates/issuer_packet.py:1387-1403`](../../scripts/gates/issuer_packet.py#L1387-L1403)）。
- 外层从原始 evidence 独立重推 expected issuer，并绑定完整 identity、role、authorization、replay claims 和 provenance；每个 provenance locator/hash 都重新安全读取（[`scripts/gates/issuer_packet.py:501-648`](../../scripts/gates/issuer_packet.py#L501-L648)）。
- Qoder 只接受 exact sibling `task.json` 与 terminal `completion.json` 的 runner identity；Codex 必须匹配 trusted current host context；human/CI 必须解析 registry record、验签并检查撤销状态；CI 还绑定合法的 owner/repository、environment 和完整 ref grammar（[`scripts/gates/issuer_packet.py:844-871`](../../scripts/gates/issuer_packet.py#L844-L871)、[`scripts/gates/issuer_packet.py:1074-1250`](../../scripts/gates/issuer_packet.py#L1074-L1250)、[`scripts/gates/issuer_packet.py:1280-1316`](../../scripts/gates/issuer_packet.py#L1280-L1316)）。
- audience、future/stale time、expiry 和 maximum age 在每次 critical-path verification 中重新检查；attestation ID 与 nonce 是两个独立、持久、并发不可重复的 claims（[`scripts/gates/issuer_packet.py:973-989`](../../scripts/gates/issuer_packet.py#L973-L989)、[`scripts/gates/issuer_packet.py:1337-1385`](../../scripts/gates/issuer_packet.py#L1337-L1385)）。
- `subject_identity` 必填且只有六个 runner identity 字段；复制 issuer actor、session、run 或 client 被拒绝（[`scripts/gates/issuer_packet.py:899-929`](../../scripts/gates/issuer_packet.py#L899-L929)、[`scripts/gates/issuer_packet.py:1318-1335`](../../scripts/gates/issuer_packet.py#L1318-L1335)）。
- 所有输入及 provenance 使用 normalized safe locator、无 symlink、regular-file 与 exact hash 检查（[`scripts/gates/issuer_packet.py:228-284`](../../scripts/gates/issuer_packet.py#L228-L284)）。发布采用 fsynced temporary file、exclusive hard-link install、不可覆盖 final，并在 post-link 语义失败时移除 final/temp/claims（[`scripts/gates/issuer_packet.py:1337-1432`](../../scripts/gates/issuer_packet.py#L1337-L1432)）。
- issuer packet 只包含已验证的 issuer identity、authorization、registry/evidence/provenance locators and hashes；不包含 result、status、exit code 或其他 result evidence。stored bytes 是 canonical JSON，返回 SHA-256 与最终文件一致（[`scripts/gates/issuer_packet.py:466-499`](../../scripts/gates/issuer_packet.py#L466-L499)）。

## External trust boundaries

以下能力由本 task 消费，不能由 issuer materializer 自己证明：Codex launcher 注入 current host context 的真实性；human/CI key resolver 与密钥托管；Qoder runner 对 task/completion 目录和 completion mtime 的所有权；本地文件系统之外的跨 host replay 一致性。它们是部署或后续控制面边界，不掩盖本次检查的实现缺陷。

## Catalog boundary

当前只有实现和本地独立复审证据。增量 Gate CLI、current-input validation receipt、independent-review receipt、hash-DAG verification 和 catalog-decision receipt 尚未产生。因此本记录的最终状态保持：

```text
Implementation acceptance: PASS
Catalog result: PENDING_GATE_RECEIPT
Produced contract current: NO
Catalog PASS: NO
```
