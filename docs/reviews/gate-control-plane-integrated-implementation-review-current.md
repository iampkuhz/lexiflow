# Gate Control Plane Integrated Implementation Review — Current

## Review identity and decision

- `status`: `FAIL`
- `work_package_id`: `LF-WP-QLT-INTEGRATED-REVIEW-001`
- `task_ids`: [`LF-TSK-QLT-0005`, `LF-TSK-QLT-0008`, `LF-TSK-QLT-0009`, `LF-TSK-QLT-0010`, `LF-TSK-QLT-0011`, `LF-TSK-QLT-0012`, `LF-TSK-QLT-0013`]
- `run_id`: `0dbb6dec-29f9-403a-b5e7-259133005241`
- Reviewer role: read-only Codex Sub-Agent; no delegation and no Qoder run.
- Durable artifact: `docs/reviews/gate-control-plane-integrated-implementation-review-current.md`
- Machine evidence root: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/`

All 11 required commands ran serially and exited `0`, but three independently demonstrated contract violations prevent integrated acceptance. This review does not issue, backfill, or claim any formal Gate receipt. The mandatory bootstrap CLI invocation remained a typed `FAIL/missing-evidence-context`, as expected when explicit evidence and issuer bindings are absent.

## Frozen-input binding and review scope

The manifest `tmp/quality/verification/gate-control-plane-integrated-review-input-v2.json` matched the required SHA-256 `0358953e235418bd32e0636a2f967b67d4302fde5ff98479f5d0f71f17d0de00`. At review start, all 35 declared files matched both recorded SHA-256 and byte count. The separately bound Main Agent probe record `tmp/quality/verification/main-adversarial-probes.txt` matched `eab6857358c547ce80b770f7acb148336472816ba31216eb42d9fac6f742cf6e` and contained four recorded probes. A mid-review revalidation and the final post-publication revalidation both found zero mismatches. The final record is `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/input-hashes-end.json`; it covers the manifest bytes, all 35 entries, and the separately bound probe record.

The addendum-requested closure documents were inspected as current, non-manifest supporting inputs: `docs/reviews/g1-bootstrap-variance.md` SHA-256 `3df75cd3ab5e5a86d43873ac8af8101246e1f3214fe18b25c8bea5f0aaea748a` and `docs/reviews/g1-task-evidence-map.md` SHA-256 `f5558ceb7352d2b90522251dd5be5ff9ab003ecc5eb08f6e9074e2bc217f037c`.

The historical `docs/reviews/gate-control-plane-integrated-implementation-review.md` was read but not modified. Git status before review already contained unrelated modified/untracked paths. Before publishing this artifact, the status delta from that baseline was empty because the run-specific verification directory is ignored.

## Blocking findings

### F1 — Formal current-input closure cannot issue its first root receipt (`FAIL`)

The declared closure order requires formal receipts to start with current-input `LF-TSK-QLT-0001` (`docs/reviews/g1-bootstrap-variance.md:22-29`, `docs/reviews/g1-task-evidence-map.md:437-445`, `docs/development/gate-control-plane-design.md:651-675`). The current catalog entries for `QLT-0001`, `QLT-0003`, `QLT-0004`, and `QLT-0006` do not declare `validation_command`, `allowed_files`, `forbidden_files`, or `file_claims`; none is a subject in the ten-entry registry. `QLT-0002` has only the planner's legacy registry command and still lacks the raw-task scope/claim fields.

The planner derives its registry subject inventory only from explicit validation commands plus the single legacy `QLT-0002` case (`scripts/gates/planner.py:667-680`), while `_raw_task` requires and reconciles a non-empty validation command, catalog allowed/forbidden lists, and non-empty file claims (`scripts/gates/planner.py:713-754`). The independent probe attempted the first required `QLT-0001` raw task both ways: `validation_command=null` reached `FAIL/invalid-entry`; any non-empty string reached `FAIL/task-reconciliation-fail`. The registry inventory was exactly the ten entries for `QLT-0002`, `0007`, `0014`, `0008`–`0013`, and `0005`.

Evidence: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/probes/formal-closure-root/result.json`. Consequence: the documented root-forward formal closure is not executable on current catalog/registry bytes, so `QLT-0013` cannot reach the claimed formal closure without changing the declared inputs.

### F2 — Executor trusts ambient `PATH` and does not bind the executed binary (`FAIL`)

The executor permits inherited `PATH` (`scripts/gates/executor.py:43-50`), copies it into the child environment (`scripts/gates/executor.py:253-265`), and starts registry argv such as `python3 ...` through `subprocess.Popen` without resolving/binding an absolute executable (`scripts/gates/executor.py:346-389`). Recorded process facts contain argv and cwd fingerprints but no resolved executable locator or hash (`scripts/gates/executor.py:836-858`). The existing environment fixture explicitly expects `PATH` to survive (`tests/gates/test_gate_executor.py:433-459`).

The independent probe placed a fake `python3` first in `PATH`. That file only printed a syntactically valid unittest success transcript. `execute_checks` returned `run_status=PASS`, `check_status=PASS`, and one assertion; its process facts had neither a resolved executable nor executable SHA-256. This violates the matrix requirement for no inherited unsafe environment and the requested process/executable binding.

Evidence: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/probes/path-injection/result.json`. Consequence: an untrusted ambient executable can satisfy a frozen command and create typed `PASS` evidence without running the registered checker.

### F3 — A same-byte alias is accepted as the current canonical task source (`FAIL`)

The design requires canonical current locators and says a same-byte alias is not current input (`docs/development/gate-control-plane-design.md:409-415,631`; `docs/acceptance-cases/phase-1.md:147-153`). The planner instead takes `task_source.locator` from the evidence packet and parses it without requiring `planning/workstreams.yaml` (`scripts/gates/planner.py:990-1011`). Catalog verification re-reads whatever task-source descriptor the current plan contains and only compares downstream receipts to that descriptor (`scripts/gates/catalog_decision.py:282-308,359-387`). The fixed canonical-locator check present for the acceptance registry has no equivalent for task source. Existing alias tests cover registry and policy only (`tests/gates/test_catalog_decision.py:360-375`).

The independent probe copied the exact catalog bytes to `tmp/aliases/workstreams-copy.yaml`, rebound the evidence/raw task to that alias, and compiled a full real planner fixture. Compilation returned `PASS`; the plan recorded the alias as its current `task_source` with the same SHA-256.

Evidence: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/probes/source-alias/result.json`. Consequence: a validation/review/catalog chain can consistently bind a noncanonical source alias and pass freshness comparisons, contrary to the current-input contract.

## Required review matrix

| Boundary | Outcome | Implementation and executed fixture evidence |
|---|---|---|
| Dispatch | `PASS` | `dispatch_preflight.py` implements a finite repo-relative POSIX grammar, finite intersection/containment, NFC/case/symlink metadata checks, exact catalog task-source reconciliation, owner resolution, contract declaration, candidate/active overlaps, and typed `BLOCKED` vs malformed `FAIL` (`parse_path_expression`, `_validate_path_metadata`, `_resolve_owners`, `_validate_contract_declaration`, `check_dispatch`). The 23 executed fixtures mutate unsafe paths, active narrowing/completeness, descriptor/task-source fields, case/symlink aliases, owner scopes, allowed/forbidden overlap, registered contract paths and simultaneous contract writers, and assert both the diagnostic code and status. Source/test hashes: `d6370fb9789fd0f0f08b7f9b57ff7fc1fb8d36d1a1bbab7750ef7314f4fb1580` / `ebac7e36edeaf558338581d639edaea2bdda612846caeff460d71c19445996b8`. |
| Planner | `FAIL` | The inspected planner is otherwise pure-read, freezes inputs, validates exact ten-entry order, fixed argv/cwd, entry hashes, mappings and whole-plan fingerprint, and rejects recursive YAML aliases, non-NFC triggers and explicit-null lineage. The 49 executed fixtures monkeypatch process/UUID/clock/scan/write surfaces, mutate every mapping/identity/hash field, exercise recursive aliases and final revalidation. F1 makes required root tasks uncompilable; F3 accepts a same-byte task-source alias. Source/test hashes: `e8ae4dff30665e80a21a0757046baf40191403737875d66606f9c77371603a82` / `06cfbf10d63e8b3a8f34e2f8260257c51518a2433a4af9b4f5225de68939c645`. |
| Executor | `FAIL` | Fixed plan projection, literal adapters, independent bounded stdout/stderr readers, timeout/signal/capture facts, pre/post input verification, not-run failure and the full `FAIL > BLOCKED > PASS` Cartesian aggregation are implemented and exercised by 117 fixtures. F2 proves the actual executable is selected through ambient `PATH` and is absent from process facts. Source/test hashes: `628265e62ead048752af4ac20bade5034e299ab760adf7ca8d782729af0392c7` / `fcbe152923ec5f6c2c2f947eec2f544aa5c2ab03342c35d620973d401080a0a0`. |
| Lifecycle/store/status | `PASS` | The single CLI resolves explicit evidence/issuer context, compiles before allocating the run, durably writes plan and START, flushes the caller-visible run ID before checker execution, and finalizes typed FAIL on flush or post-check drift. The store uses canonical UUIDv4 paths, no-follow reads, exclusive directory/artifact creation, fsync and rollback; status reads one explicit run without scan/wait/retry. Ten fixtures observe `caller-flush` before `checker`, assert no checker on flush failure, force write/link interruption, mutate post-check inputs and persisted plan, and exercise collision/symlink/unsafe status rejection. Source hashes: CLI `dfdcc501c65139634726b7d39a891053be58d5b8605404a7cbd03bc308873a45`, store `a227fd7a6c12f1fb59567095a7f3e88f1aa67b4d8920a7fb372d4323c882f88e`; test `5d4b612df21392dbb53ff4bc4648dec41bce6f2140d0ca329beb8c5433ad564c`. |
| Independent review | `PASS` | The route binds an immutable validation receipt/plan/manifest, re-reads the validation changed-file snapshot against current subject bytes, compares self-reported writes to the current plan write-set, rejects overlap/self-review/stale source/diff/registry/policy, and rereads the subject receipt before publication. Eight fixtures publish a distinct immutable review and execute self-review, write overlap, empty-self-report with plan write, empty-self-report with actual subject mutation, stale hashes and artifact drift. Main Agent probe records independently confirm both empty-write-set mutation paths. Source/test hashes: `ef43a6b016529fe06a0989c4b8f0b48be9b032fa4706d7233c94a45ebab6b2d9` / `a2ccce6780e1b9a1f3a0c3c57c3ff546cc82ae8de26654d2a50eec489f38286c`. |
| Hash DAG | `PASS` | The verifier performs no writes, recursively reads a finite explicit graph, reconciles recorded hashes and schema identities, uses DFS state for cycles, enforces prior-receipt time ordering, and re-reads every node. Seven fixtures mutate a root into self-edge/cycle/forward edge, introduce `latest`, missing/changed leaves and duplicate identity with different bytes, and verify typed FAIL never promotes the graph. Source/test hashes: `34497fc33db933213ac52a6ed28d060a0f065bdf3f3b22d3915cbc0c17070e44` / `98e4f30ed5047da5cc34fd729904243d90b519a29e29162987bc022b3569ae63`. |
| Catalog decision | `FAIL` | Acceptance mapping, exact dependency set/version/result, validation/review/hash reconciliation, current subject-snapshot revalidation, recursive issuer-chain verification against current authority registry, process/CLI binding, forged approval/bootstrap rejection and non-promotion of `BLOCKED`/`FAIL` are implemented. Twelve fixtures execute orphan mapping, missing/stale dependency, manifest tamper/self-reference, forged approval, forged dependency issuer, subject mutation, missing snapshot proof and typed prior results; 45 issuer fixtures exercise provenance, replay and registry/evidence drift. F1 prevents the declared root-forward chain from existing, and F3 permits a noncanonical task source throughout the chain. Source/test hashes: catalog `ac4f8f5052e9db7ccad33403c6cdb59d91264fb3a66eae2a4bd225844553c4d8` / `a94a9bda789966a5eca16e31231888b3e7b4fff521248f44e83d0c6a98439820`; issuer `9107beb347d464d30fcf9cca9cc1cab42f74533aa0603f9d6db50174529082cf` / `11b57a06c63c188efc906b0ca48a1b0a418e4a1fac796f8fca00f67992ab64d0`. |
| Cross-cutting | `FAIL` | One CLI routes all three receipt kinds, public result states are restricted to `PASS/BLOCKED/FAIL`, explicit locators reject `latest`, missing context has no fallback, and no user-learning data or secrets were present in reviewed artifacts. Qoder callback/ACK/exit zero are not catalog PASS, and the 20 runner fixtures enforce identity/callback/lifecycle boundaries. F1–F3 break executable formal closure, executable identity, and canonical current inputs. Registry hash: `0616853cceb07680d7faa4ac7d11a5761a5494cb30081ac003f345ae91afdb87`; design hash: `912f76a4e2abab65cf42d4eec569048f1b681a77b328e70d997feb58322a019b`; acceptance hash: `6cb2df97d93c3c90a4180a1b4c2e157d306d7b6543eef59841e160ab1925d98a`. |

## Per-Task outcomes

| Task | Outcome | Durable rationale |
|---|---|---|
| `LF-TSK-QLT-0005` | `PASS` | Dispatch source and its real negative fixtures cover the required path, owner, overlap, contract and status semantics; its tenth registry entry is current and ordered. |
| `LF-TSK-QLT-0008` | `FAIL` | F1 shows the planner cannot compile the declared formal root; F3 shows it accepts a noncanonical task-source alias. |
| `LF-TSK-QLT-0009` | `FAIL` | F2 lets ambient `PATH` substitute the checker executable while typed aggregation records `PASS`. |
| `LF-TSK-QLT-0010` | `PASS` | START visibility/flush ordering, immutable publication, drift failure and explicit one-read status are implemented and exercised. |
| `LF-TSK-QLT-0011` | `PASS` | Current subject bytes are independently reverified; self-review, hidden writes and stale evidence fail while subject receipt bytes remain unchanged. |
| `LF-TSK-QLT-0012` | `PASS` | Finite read-only graph verification rejects all required edge, alias, node, hash and identity failures. |
| `LF-TSK-QLT-0013` | `FAIL` | F1 makes formal catalog closure unreachable and F3 allows a same-byte source alias to satisfy current-input comparisons. |

A per-Task `PASS` here is an implementation-review conclusion only. It is not a catalog result and does not substitute for `TASK_VALIDATION`, `INDEPENDENT_REVIEW`, or `CATALOG_DECISION` receipts.

## Serial validation command record

| # | Command | Exit | Observed result |
|---:|---|---:|---|
| 1 | `python3 -m unittest discover -s tests/harness -p 'test_qoder_runner.py'` | `0` | 20 tests, `OK` |
| 2 | `python3 -m unittest discover -s tests/gates -p 'test_dispatch_preflight.py'` | `0` | 23 tests, `OK` |
| 3 | `python3 -m unittest discover -s tests/gates -p 'test_gate_planner.py'` | `0` | 49 tests, `OK` |
| 4 | `python3 -m unittest discover -s tests/gates -p 'test_gate_executor.py'` | `0` | 117 tests, `OK` |
| 5 | `python3 -m unittest discover -s tests/gates -p 'test_gate_lifecycle.py'` | `0` | 10 tests, `OK` |
| 6 | `python3 -m unittest discover -s tests/gates -p 'test_independent_review.py'` | `0` | 8 tests, `OK` |
| 7 | `python3 -m unittest discover -s tests/gates -p 'test_hash_dag.py'` | `0` | 7 tests, `OK` |
| 8 | `python3 -m unittest discover -s tests/gates -p 'test_catalog_decision.py'` | `0` | 12 tests, `OK` |
| 9 | `python3 -m unittest discover -s tests/gates -p 'test_issuer_packet.py'` | `0` | 45 tests, `OK` |
| 10 | `python3 -m unittest discover -s tests/gates -p 'test_*.py'` | `0` | 472 tests, `OK` |
| 11 | `python3 -m scripts.gates.planning --root .` | `0` | 113 tasks, 11 checks, `PASS` |

Exact logs and exit ledger: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/commands/` and `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/commands.tsv`.

The required AGENTS.md handoff command was also run after the 11 review commands:

| Command | Exit | Observed result |
|---|---:|---|
| `python3 scripts/gates/cli.py run --mode incremental` | `1` | `FAIL`, reason `missing-evidence-context`; no formal receipt claimed or created |

Evidence: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/commands/12-cli-bootstrap.log` and `12-cli-bootstrap.exit`.

## Independent adversarial probes

| Probe | Expected contract | Observed |
|---|---|---|
| Formal closure root compilability | `QLT-0001` must be compilable as the first formal receipt | Null command: `FAIL/invalid-entry`; non-empty command: `FAIL/task-reconciliation-fail`; root absent from registry |
| Executable substitution | Frozen command must identify the actual executable | Fake `python3` selected through ambient `PATH`; execution and check both `PASS`; no resolved executable/hash recorded |
| Task-source alias | Same-byte alias must not be current | Full real planner fixture compiled `PASS` with `tmp/aliases/workstreams-copy.yaml` as `task_source` |

Probe artifacts reside only under `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/probes/`.

## Frozen manifest entries

| Locator | SHA-256 | Bytes |
|---|---|---:|
| `AGENTS.md` | `867ab1927cde6888ed374c8d8240c27c5bb72be6d7bf6b33fc42a5242b69cce1` | 3516 |
| `scripts/harness/qoder_task.py` | `b05b4fa7441c24e76cc5af503c29cb4b72e6e64919a76e7f3767c8b88e70a916` | 49253 |
| `scripts/harness/qoder_task_lifecycle.py` | `95d585ee3dcad3374ba9380f38cfded645d1a1a630c520534e88cdf60d680be3` | 31201 |
| `tests/harness/test_qoder_runner.py` | `7cf80b88435d26283a6791f1239b5a1e78e40ab4a71b13c2167ba30dd6e02a66` | 23564 |
| `scripts/gates/dispatch_preflight.py` | `d6370fb9789fd0f0f08b7f9b57ff7fc1fb8d36d1a1bbab7750ef7314f4fb1580` | 38954 |
| `tests/gates/test_dispatch_preflight.py` | `ebac7e36edeaf558338581d639edaea2bdda612846caeff460d71c19445996b8` | 20420 |
| `scripts/gates/evidence_packet.py` | `cd7b5ca53c35a4d303b65036a66bd0f1c6ae219d33c6fbe4c1b37905e0462eb3` | 100796 |
| `tests/gates/test_evidence_packet.py` | `bc00bb87b8708a7f7a8794db8cad06adb17cf68199a257120714b9936b901469` | 116738 |
| `scripts/gates/issuer_packet.py` | `9107beb347d464d30fcf9cca9cc1cab42f74533aa0603f9d6db50174529082cf` | 64124 |
| `tests/gates/test_issuer_packet.py` | `11b57a06c63c188efc906b0ca48a1b0a418e4a1fac796f8fca00f67992ab64d0` | 57023 |
| `scripts/gates/planner.py` | `e8ae4dff30665e80a21a0757046baf40191403737875d66606f9c77371603a82` | 70043 |
| `tests/gates/test_gate_planner.py` | `06cfbf10d63e8b3a8f34e2f8260257c51518a2433a4af9b4f5225de68939c645` | 58393 |
| `scripts/gates/executor.py` | `628265e62ead048752af4ac20bade5034e299ab760adf7ca8d782729af0392c7` | 49317 |
| `tests/gates/test_gate_executor.py` | `fcbe152923ec5f6c2c2f947eec2f544aa5c2ab03342c35d620973d401080a0a0` | 62349 |
| `scripts/gates/cli.py` | `dfdcc501c65139634726b7d39a891053be58d5b8605404a7cbd03bc308873a45` | 21175 |
| `scripts/gates/receipt_store.py` | `a227fd7a6c12f1fb59567095a7f3e88f1aa67b4d8920a7fb372d4323c882f88e` | 18307 |
| `tests/gates/test_gate_lifecycle.py` | `5d4b612df21392dbb53ff4bc4648dec41bce6f2140d0ca329beb8c5433ad564c` | 14639 |
| `scripts/gates/independent_review.py` | `ef43a6b016529fe06a0989c4b8f0b48be9b032fa4706d7233c94a45ebab6b2d9` | 22146 |
| `tests/gates/test_independent_review.py` | `a2ccce6780e1b9a1f3a0c3c57c3ff546cc82ae8de26654d2a50eec489f38286c` | 9992 |
| `scripts/gates/hash_dag.py` | `34497fc33db933213ac52a6ed28d060a0f065bdf3f3b22d3915cbc0c17070e44` | 11889 |
| `tests/gates/test_hash_dag.py` | `98e4f30ed5047da5cc34fd729904243d90b519a29e29162987bc022b3569ae63` | 8403 |
| `scripts/gates/catalog_decision.py` | `ac4f8f5052e9db7ccad33403c6cdb59d91264fb3a66eae2a4bd225844553c4d8` | 31826 |
| `tests/gates/test_catalog_decision.py` | `a94a9bda789966a5eca16e31231888b3e7b4fff521248f44e83d0c6a98439820` | 23985 |
| `harness/gate-check-registry.yaml` | `0616853cceb07680d7faa4ac7d11a5761a5494cb30081ac003f345ae91afdb87` | 11360 |
| `harness/gate-issuer-authorities.yaml` | `caa97993edd0a7ef927ed573f6928a04f0b5e1e2a7478e67e775fbf66eb5bf75` | 1786 |
| `harness/agent-policy.manifest.yaml` | `3b81bc38a5996b41415e4974b8735444de22befe277eb5b31ad2202a33984f2d` | 6865 |
| `harness/agent-runtime.manifest.yaml` | `802244d4f690da711ad7b9dda2185ae19cfe7f0422c6a3c5ac2d8c3cd5b55e74` | 4789 |
| `harness/manifest.yaml` | `933f4ab4991ddb2db2956326b10d891e1103316c800df4b72703cf9406d51627` | 2317 |
| `planning/workstreams.yaml` | `41b3448aec16ce5be13e30265664cf4e86a7a1390ca49868612880b9b3f2260f` | 162462 |
| `planning/task-template.yaml` | `49c7057b42a7247dea9a91f5d345c9ae2f49507c8f72333624d15ecd117fd4dc` | 10218 |
| `docs/development/gate-control-plane-design.md` | `912f76a4e2abab65cf42d4eec569048f1b681a77b328e70d997feb58322a019b` | 46311 |
| `docs/reviews/gate-control-plane-integrated-review-plan.md` | `6ac0f87646ae5ffc1241f2ad521a06492b498a198deee1145dbf308e9e7722ce` | 8609 |
| `docs/reviews/gate-control-plane-integrated-implementation-review.md` | `1287947a5f41cb14559477e35185433bb6adeb1d60ea2ca516d683efcdaec296` | 4958 |
| `docs/acceptance-cases/phase-1.md` | `6cb2df97d93c3c90a4180a1b4c2e157d306d7b6543eef59841e160ab1925d98a` | 7982 |
| `openspec/specs/agent-execution/spec.md` | `852598f2e378170bfab59cec4090c39026a68074771a54f4ca238a26f62c36ea` | 7435 |

## Reviewer write-scope result

No source, test, planning, harness, OpenSpec, historical review, bootstrap artifact, evidence packet, issuer packet, or formal receipt was modified by this reviewer. The only durable write is this review artifact; all other reviewer outputs are below the allowed ignored run directory. The final Git-status delta and end hash report are stored under `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REVIEW-001/0dbb6dec-29f9-403a-b5e7-259133005241/`.
