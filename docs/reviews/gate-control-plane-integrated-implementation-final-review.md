# Gate Control Plane Integrated Implementation Final Review

## Review identity and decision

- `status`: `FAIL`
- `work_package_id`: `LF-WP-QLT-INTEGRATED-REREVIEW-003`
- `task_ids`: [`LF-TSK-QLT-0005`, `LF-TSK-QLT-0014`, `LF-TSK-QLT-0008`, `LF-TSK-QLT-0009`, `LF-TSK-QLT-0010`, `LF-TSK-QLT-0011`, `LF-TSK-QLT-0012`, `LF-TSK-QLT-0013`]
- `run_id`: `98dd5166-1789-4579-8aa6-8ccd8ebfa0e0`
- Reviewer role: read-only Codex Sub-Agent; no delegation and no Qoder run.
- Durable artifact: `docs/reviews/gate-control-plane-integrated-implementation-final-review.md`
- Machine evidence root: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REREVIEW-003/98dd5166-1789-4579-8aa6-8ccd8ebfa0e0/`

All 14 required commands ran serially and exited `0`. The full Gate suite ran 492 tests. The three findings from the first integrated review and the UUIDv7 finding from the remediation re-review are fixed on the frozen bytes. One independently reproduced formal-chain defect still prevents acceptance: the current evidence packet, catalog task-contract scope, planner and independent-review contracts make a disjoint review plan impossible for all 19 `task-contract` profile Tasks.

This report is implementation-review evidence only. It does not issue, backfill, or claim a `TASK_VALIDATION`, `INDEPENDENT_REVIEW`, or `CATALOG_DECISION` receipt. The bootstrap CLI invocation correctly remained `FAIL/missing-evidence-context` because no formal evidence/issuer bindings were supplied.

## Frozen-input binding and write scope

The sole frozen manifest `tmp/quality/verification/gate-control-plane-integrated-review-input-v4.json` matched the required SHA-256 `6710068da169601f6119391e11918bee53fd6308760ff3e365d3de8035de9c13`. All 68 declared files matched both SHA-256 and byte count at review start and at the mid-review checkpoint. The separately bound Main-Agent records also matched their manifest hashes:

- `tmp/quality/verification/main-remediation-probes-v3.txt`: `50183f2fb390e494bc9a53d9165bf98dc763384802e16e46b4eafed670923256`
- `tmp/quality/verification/main-uuid-remediation-probes-v4.json`: `9d6c0ccca1916a3fa02ca0a00915449a466dc8c22a96a814565999b1f61dbf3f`

Final verification after this report was published also found zero manifest, file-hash, byte-count, or bound-probe mismatches. The complete exact records are `input-hashes-start.json`, `input-hashes-mid.json`, and `input-hashes-end.json` below the machine evidence root. Every source/test conclusion below binds to the exact descriptor in that manifest; displayed hashes are full SHA-256 values where the finding or remediation changed.

The existing review artifacts remained byte-identical. Before this report was written, the full Git-status snapshot matched the review-start snapshot. The final status delta contains only this report outside the ignored run directory.

## Blocking finding

### F1 — All 19 task-contract profile Tasks have no reachable independent-review PASS path (`FAIL`)

The current profile projection deliberately gives every profile Task one exact write target. For each of the 19 profiles whose runner is `task-contract`, catalog `allowed_files` is exactly its `tmp/quality/task-evidence/<DOMAIN>/<task-id>/result.json`, and the only `file_claims` entry names the same exact path. The checked-in projection test enforces this geometry for every profile (`tests/gates/test_registry_profiles.py:29-46`). Frozen hashes: profile `f55fc85d557c69709af8a4afada6464c3fd1b9312a34adcbf90d9dcb711232b5`, catalog `f49cd4fa233362774e504691b88448974140b8e0094570748143fe05629fd59c`, registry `46e5b8cf43190f884d1dbafc15e00dd718654b5307f6b40e4b919ed7313527ad`.

The explicit evidence materializer rejects an empty `changed_files` list (`scripts/gates/evidence_packet.py:543-557`) and requires attested, scope, snapshot and diff file sets to be identical, allowed and claimed (`scripts/gates/evidence_packet.py:1225-1269,1400-1438,1624-1649`). The planner then requires packet allowed scope and claims to equal the current catalog and freezes packet `changed_files` into the plan (`scripts/gates/planner.py:713-754,990-1030`). Therefore a current evidence packet and plan for any of those 19 Tasks must contain that same sole evidence path.

For independent review, validation `changed_files` are the subject files, while the current review plan's `scope.changed_files` is the trusted reviewer write set. The implementation requires the self-report to equal that plan set and rejects any intersection with the subject (`scripts/gates/independent_review.py:215-229`). Thus the only compilable non-empty review plan overlaps the only valid subject path and deterministically returns `FAIL/reviewer-not-independent`. The positive focused fixture does not prove formal reachability because it manually replaces the review plan write set with `[]` (`tests/gates/test_independent_review.py:35-48`), a state that the real evidence materializer rejects as `RESULT_FIELD_EMPTY`.

Using a different catalog Task for the review cannot close the chain: catalog decision requires both validation and review receipts to have the exact current Task identity (`scripts/gates/catalog_decision.py:290-308,393-425`).

The independent probe established all four links without modifying frozen inputs:

1. exactly 19 `task-contract` profiles exist;
2. all 19 have the single exact evidence-file scope and claim;
3. a real planner fixture for a current profile Task freezes that sole file as `scope.changed_files` while empty scope is `FAIL/RESULT_FIELD_EMPTY`;
4. the same subject/plan path is rejected as `FAIL/reviewer-not-independent`.

Evidence: `probes/formal-review-overlap/result.json`. Consequence: none of those 19 Tasks can produce the current-input `INDEPENDENT_REVIEW PASS` required for its catalog decision. This includes the formal root `LF-TSK-QLT-0001` and exit task `LF-TSK-ARCH-0008`, so the declared root-forward catalog closure is not executable.

## Prior remediation results

| Remediation | Outcome | Independent current-byte evidence |
|---|---|---|
| Formal 30-Task closure compile/catalog projection | `PASS` | Planner command ran 51 tests including the computed 30-Task compile matrix; task-contract and registry suites ran 6 and 4 tests; registry check reported 30 entries and 21 profiles. The prior missing catalog fields/subjects are fixed. Planner/test hashes: `5a710f6746d602702a648f90bffd87db26968181efaddc4baa18d064b8eb7ccf` / `50e4c450947566cb642a61c915872bbb8c3f26543f56494c289b429ef401a044`. This proves compilation, not the independent-review reachability rejected by F1. |
| Canonical executable binding | `PASS` | Executor strips ambient `PATH`, resolves registered `python3` to a canonical absolute executable, records content/inode binding and rechecks it after execution. The real fake-`PATH` negative fixture ran within all 123 executor tests. Source/test hashes: `369293c64f2f69d1d3211d949614b1cb88a266083a6e48a74213dc40b0ed4c9a` / `6e36bf0bf962de725307f455daac51f490029ec0147bcebf00f6c6b0e97fd12a`. |
| Canonical task-source locator | `PASS` | Planner and catalog decision both require exact `planning/workstreams.yaml`; their same-byte alias negative fixtures ran in the 51- and 13-test suites. Source hashes: planner above; catalog `078d2da479e1b3f699e9e2366e2897b176ce9a101008ed13a427196eba35d0fb`. |
| Current UUIDv7 authority provenance | `PASS` | `_canonical_rfc_uuid` accepts canonical, non-nil RFC UUID versions 1–8 for external authority/subject identities, while `_canonical_uuid4` still governs packet namespace and attestation replay values (`scripts/gates/issuer_packet.py:210-233,603-622,913-935,1088-1172`). Current Codex host session/parent identities were both canonical UUIDv7 and materialized a packet authorized for all three receipt kinds. The frozen real ACKed Qoder task/completion had a canonical UUIDv7 parent and materialized the two Qoder-authorized kinds. Direct UUIDv7 negative probes for `issuer_instance_id`, `attestation_id`, and `nonce` all returned `FAIL/invalid-identity`. No raw host or Qoder identity was persisted. Source/test hashes: `f8f9e3555aff216e8894aa9c9980c51700173cfd2b01ff25130e1571573ccc16` / `0429588e249b42172c0e9514306b073dae374f48cf66dee2418d51a3a2dc2c4d`; issuer suite 46 tests `OK`. Evidence: `probes/uuid-runtime-compat/result.json`. |

## Required review matrix

| Boundary | Outcome | Current-byte conclusion |
|---|---|---|
| Dispatch | `PASS` | The implementation and 23 executed fixtures cover finite repository-relative grammar, symlink/case aliases, owner/task-source reconciliation, active/candidate overlaps, allowed/forbidden intersection, exact contract declarations, same-contract writers, and typed `BLOCKED` versus malformed `FAIL`. Source/test hashes: `d6370fb9789fd0f0f08b7f9b57ff7fc1fb8d36d1a1bbab7750ef7314f4fb1580` / `ebac7e36edeaf558338581d639edaea2bdda612846caeff460d71c19445996b8`. |
| Issuer | `PASS` | External UUID v1–8 and local UUIDv4 boundaries were inspected and independently executed on current Codex and real Qoder provenance. The 46-test suite also exercises forged authority, stale/replayed evidence, authorization mismatch, parser drift and immutable publication. |
| Planner | `PASS` | The pure compiler, exact current Task/change/contract, 30-entry projection, canonical source, fixed argv/cwd, hashes/fingerprint, mappings and strict YAML cases pass 51 fixtures. F1 is an incompatibility across evidence scope and independent-review semantics; the planner faithfully exposes the non-empty catalog scope. |
| Executor | `PASS` | Frozen commands, literal adapters, canonical executable binding, independent capture bounds, timeout/signal facts, non-promotion and complete `FAIL > BLOCKED > PASS` aggregation pass 123 fixtures and the full Gate suite. |
| Lifecycle/store/status | `PASS` | Ten fixtures cover explicit packet resolution, caller-visible flushed START before checker, no checker after flush failure, safe immutable publication, collision/interruption/drift failure, and explicit one-read status. CLI/store/test hashes: `dfdcc501c65139634726b7d39a891053be58d5b8605404a7cbd03bc308873a45` / `a227fd7a6c12f1fb59567095a7f3e88f1aa67b4d8920a7fb372d4323c882f88e` / `5d4b612df21392dbb53ff4bc4648dec41bce6f2140d0ca329beb8c5433ad564c`. |
| Independent review | `FAIL` | Subject/current-snapshot, issuer, self-review, overlap, concealed writes, stale inputs and immutable rerun checks exist and their eight focused fixtures pass. F1 shows that the only positive fixture's empty plan write set cannot be produced through the real evidence/planner path for 19 current Tasks. Source/test hashes: `ef43a6b016529fe06a0989c4b8f0b48be9b032fa4706d7233c94a45ebab6b2d9` / `a2ccce6780e1b9a1f3a0c3c57c3ff546cc82ae8de26654d2a50eec489f38286c`. |
| Hash DAG | `PASS` | The pure finite verifier and seven fixtures reject self/back edges, cycles, aliases/`latest`, missing or changed nodes, and duplicate identity with different bytes. Source/test hashes: `34497fc33db933213ac52a6ed28d060a0f065bdf3f3b22d3915cbc0c17070e44` / `98e4f30ed5047da5cc34fd729904243d90b519a29e29162987bc022b3569ae63`. |
| Catalog decision | `FAIL` | Thirteen focused fixtures prove current acceptance mappings, subject snapshot revalidation, exact dependencies, recursive issuer authority, fail-closed inputs and non-promotion. F1 prevents the required review receipt from existing for the formal root-forward closure. Catalog source/test hashes: `078d2da479e1b3f699e9e2366e2897b176ce9a101008ed13a427196eba35d0fb` / `05e488d3611281f3e57903fcdbaebedb146d517fcfa6d2dddef822ae17511bb7`. |
| Cross-cutting | `FAIL` | The single CLI, three typed results, explicit locators and bootstrap refusal remain correct; no callback, ACK or exit zero was treated as catalog `PASS`. F1 makes formal closure unreachable despite every required command exiting zero. |

## Per-Task outcomes

| Task | Outcome | Durable rationale |
|---|---|---|
| `LF-TSK-QLT-0005` | `PASS` | Dispatch implementation and its executed adversarial matrix satisfy the reviewed boundary. |
| `LF-TSK-QLT-0014` | `PASS` | Current Codex and real ACKed Qoder UUIDv7 provenance materialize, while local packet/attestation/nonce UUIDv4 boundaries and issuer trust negatives remain enforced. |
| `LF-TSK-QLT-0008` | `PASS` | The planner compiles all 30 current closure Tasks, binds canonical sources and exact catalog scope, and rejects the prior alias defect. |
| `LF-TSK-QLT-0009` | `PASS` | Canonical executable binding closes ambient-`PATH` substitution; focused and full tests pass. |
| `LF-TSK-QLT-0010` | `PASS` | Validation lifecycle, immutable store and explicit status behavior satisfy their focused matrix. |
| `LF-TSK-QLT-0011` | `FAIL` | F1 proves that 19 current catalog Tasks cannot obtain a real disjoint review plan and therefore cannot publish `INDEPENDENT_REVIEW PASS`. |
| `LF-TSK-QLT-0012` | `PASS` | Read-only finite hash-DAG verification satisfies all focused negative cases. |
| `LF-TSK-QLT-0013` | `FAIL` | Exact current-input catalog validation is implemented, but the required review receipts for the formal root-forward closure are unreachable under F1. |

A per-Task `PASS` is an implementation-review conclusion only. It is not a formal receipt or catalog `PASS`.

## Serial validation command record

| # | Command | Exit | Observed result |
|---:|---|---:|---|
| 1 | `python3 -m unittest discover -s tests/harness -p 'test_qoder_runner.py'` | `0` | 20 tests, `OK` |
| 2 | `python3 -m unittest discover -s tests/gates -p 'test_dispatch_preflight.py'` | `0` | 23 tests, `OK` |
| 3 | `python3 -m unittest discover -s tests/gates -p 'test_gate_planner.py'` | `0` | 51 tests, `OK` |
| 4 | `python3 -m unittest discover -s tests/gates -p 'test_gate_executor.py'` | `0` | 123 tests, `OK` |
| 5 | `python3 -m unittest discover -s tests/gates -p 'test_task_contracts.py'` | `0` | 6 tests, `OK` |
| 6 | `python3 -m unittest discover -s tests/gates -p 'test_registry_profiles.py'` | `0` | 4 tests, `OK` |
| 7 | `python3 -m unittest discover -s tests/gates -p 'test_gate_lifecycle.py'` | `0` | 10 tests, `OK` |
| 8 | `python3 -m unittest discover -s tests/gates -p 'test_independent_review.py'` | `0` | 8 tests, `OK` |
| 9 | `python3 -m unittest discover -s tests/gates -p 'test_hash_dag.py'` | `0` | 7 tests, `OK` |
| 10 | `python3 -m unittest discover -s tests/gates -p 'test_catalog_decision.py'` | `0` | 13 tests, `OK` |
| 11 | `python3 -m unittest discover -s tests/gates -p 'test_issuer_packet.py'` | `0` | 46 tests, `OK` |
| 12 | `python3 -m unittest discover -s tests/gates -p 'test_*.py'` | `0` | 492 tests, `OK` |
| 13 | `python3 -m scripts.gates.planning --root .` | `0` | 113 tasks, 11 checks, `PASS` |
| 14 | `python3 -m scripts.gates.registry_profiles --root . --check` | `0` | 30 entries, 21 profiles, `PASS` |

Exact logs and exit ledger are in `commands/` and `commands.tsv` below the machine evidence root.

The required bootstrap invocation was run separately:

| Command | Exit | Observed result |
|---|---:|---|
| `python3 scripts/gates/cli.py run --mode incremental` | `1` | `FAIL/missing-evidence-context`; no formal receipt claimed or created |

Evidence: `commands/15-bootstrap.log` and `commands/15-bootstrap.exit`.

## Reviewer write-scope result

No source, test, planning, harness, OpenSpec, existing review, evidence packet, issuer packet, task evidence, or formal receipt was modified. The only durable write is this report; all other reviewer-created output is below the allowed ignored run directory. Saved runtime probes contain only canonicality flags, UUID versions, result codes and hash comparisons; no raw host or Qoder identity is persisted.
