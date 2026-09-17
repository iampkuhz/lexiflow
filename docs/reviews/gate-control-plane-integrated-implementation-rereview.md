# Gate Control Plane Integrated Implementation Remediation Re-review

## Review identity and decision

- `status`: `FAIL`
- `work_package_id`: `LF-WP-QLT-INTEGRATED-REREVIEW-002`
- `task_ids`: [`LF-TSK-QLT-0005`, `LF-TSK-QLT-0008`, `LF-TSK-QLT-0009`, `LF-TSK-QLT-0010`, `LF-TSK-QLT-0011`, `LF-TSK-QLT-0012`, `LF-TSK-QLT-0013`]
- `run_id`: `8fe48eea-4c53-41e1-9dc4-4d6e57072dd3`
- Reviewer role: read-only Codex Sub-Agent; no delegation and no Qoder run.
- Durable artifact: `docs/reviews/gate-control-plane-integrated-implementation-rereview.md`
- Machine evidence root: `tmp/quality/verification/LF-WP-QLT-INTEGRATED-REREVIEW-002/8fe48eea-4c53-41e1-9dc4-4d6e57072dd3/`

The three defects from the prior integrated review are remediated on the frozen bytes: all 30 formal-closure tasks now compile from the catalog-derived registry, ambient `PATH` cannot substitute the registered Python executable, and both planner and catalog decision reject a same-byte task-source alias. A new independently reproduced compatibility defect is terminal for the current formal receipt chain: the issuer materializer accepts only canonical UUIDv4 identities, while the current Codex host session/thread identifiers and the parent session recorded by a real acknowledged Qoder run are canonical UUIDv7.

This is an implementation `FAIL`, not a bootstrap receipt. No trusted issuer packet or formal receipt was issued or backfilled. Commands 1–11 completed successfully. Once the terminal issuer defect was independently reproduced, the already-running all-Gate command was interrupted and commands 13–14 were not run under the bounded-stop instruction. The missing required results independently prevent review `PASS`.

## Frozen-input binding and write scope

The manifest `tmp/quality/verification/gate-control-plane-integrated-review-input-v3.json` matched the required SHA-256 `57ad0c0824ca29c4109d647cd4226e92f818b3907b105afd5b282c9368c31236`. Its 61 declared locators matched both SHA-256 and byte count at review start and at the mid-review checkpoint. The separately bound Main-Agent remediation probe record `tmp/quality/verification/main-remediation-probes-v3.txt` matched SHA-256 `50183f2fb390e494bc9a53d9165bf98dc763384802e16e46b4eafed670923256`. Final verification after this report was published also found zero manifest, hash, or byte-count mismatches; the records are `input-hashes-start.json`, `input-hashes-mid.json`, and `input-hashes-end.json` below the machine evidence root.

The two historical reports named by the handoff were read and remained byte-identical to the v3 manifest. The pre-existing worktree already contained unrelated modified and untracked paths. Comparing the full pre/post status snapshots shows that this report is the only non-ignored path added by the reviewer; all other reviewer output is in the allowed ignored run directory.

## Blocking finding

### F1 — UUIDv4-only issuer identity validation rejects current canonical UUIDv7 provenance (`FAIL`)

`scripts/gates/issuer_packet.py:210-219` rejects every identity whose parsed UUID version is not 4. Both Qoder and Codex provenance routes feed their session and parent-session identities through this validator (`scripts/gates/issuer_packet.py:1074-1121`, `scripts/gates/issuer_packet.py:1145-1168`). The positive issuer fixtures generate only synthetic UUIDv4 values (`tests/gates/test_issuer_packet.py:44-53`, `tests/gates/test_issuer_packet.py:220-232`), so the 45-test issuer suite does not exercise the current launcher identity version. The accepted QLT-0014 review expressly treats authentic current-host context as an external launcher boundary (`docs/reviews/qlt-0014-implementation-review.md:58,64-66`); the materializer must consume that authentic context rather than reject its RFC UUID version.

The sanitized current-host probe recorded only presence, canonical form, and UUID version: both `CODEX_SESSION_ID` and `CODEX_THREAD_ID` are canonical UUIDv7. Using those exact values internally to construct a fresh current-host attestation, with a distinct valid UUIDv4 subject identity, failed issuer materialization as `FAIL/invalid-identity` on `session_id`. No raw identifier was printed or persisted. A minimality probe changed only `_canonical_uuid` semantics in memory to accept canonical non-nil RFC UUID versions 1–8; the same attestation then materialized a Codex packet authorized for all three receipt kinds.

An independent hermetic Qoder probe copied the task/completion bytes of one real acknowledged run and the current authority registry into an isolated temporary root. Its run and child session identities were canonical UUIDv4, but its parent session was canonical UUIDv7; materialization failed as `FAIL/invalid-identity` on `parent_session_id`. The probe refreshed only the copied completion mtime to satisfy freshness and did not print or persist any raw identity.

Evidence:

- `probes/codex-host-uuid-compat/result.json`
- `probes/codex-host-uuid-compat/minimality.json`
- `probes/qoder-parent-uuid-compat/result.json`

Consequence: the current Desktop Codex route cannot materialize any trusted issuer packet, and the current Qoder runner provenance route cannot materialize one either. Human or CI routes would require distinct authenticated external evidence and keys not supplied to this review; they do not make the declared current Desktop/Qoder chain executable. Therefore current `TASK_VALIDATION`, `INDEPENDENT_REVIEW`, and `CATALOG_DECISION` cannot complete through the actually available provenance paths.

## Prior findings remediation

| Prior finding | Outcome | Independent evidence |
|---|---|---|
| Formal closure root missing from compilable catalog/registry | `PASS` | The current catalog-derived closure contains 30 tasks; the registry contains the same 30 subjects in exact closure order; no closure task lacks `validation_command`, `allowed_files`, `forbidden_files`, `file_claims`, or owner. `TestFormalClosureCompileMatrix.test_all_thirty_g1_closure_tasks_compile_from_current_catalog` iterates the computed hard/contract closure and compiled every current task (`tests/gates/test_gate_planner.py:514-536`). Command 3 ran that fixture and all 51 planner tests. Direct record: `probes/formal-closure-remediation/result.json`. Current hashes: planner `5a710f…b7ccf`, planner tests `50e4c4…1a044`, registry `46e5b8…27ad`, profiles `f55fc8…32b5`, catalog `f49cd4…59c`. |
| Ambient `PATH` could substitute the checker executable | `PASS` | The child environment no longer forwards `PATH`; registered `python3` resolves to the canonical absolute current interpreter, whose content hash, device/inode/size and pre/post verification are recorded and whose absolute locator is used for execution (`scripts/gates/executor.py:44-51,285-341,418-466,975-1025,1121-1178`). The real fake-`PATH` fixture proves the fake executable is not selected (`tests/gates/test_gate_executor.py:1323-1348`). Command 4 ran all 123 executor tests. Direct record: `probes/path-injection-remediation/result.json`. Current hashes: executor `369293…d4c9a`, executor tests `6e36bf…fd12a`. |
| Same-byte task-source alias accepted as current | `PASS` | Planner requires exact `planning/workstreams.yaml` before reading the source (`scripts/gates/planner.py:999-1002`); catalog decision independently repeats that locator check (`scripts/gates/catalog_decision.py:370-384`). The direct planner probe and catalog fixture both reject a same-byte alias as `FAIL/unsafe-locator` (`tests/gates/test_catalog_decision.py:377-387`). Commands 3 and 10 passed. Direct record: `probes/source-alias/result.json`; the separately bound Main-Agent record also contains both negative probes. Current hashes: catalog decision `078d2d…d0fb`, catalog tests `05e488…1bb7`. |

## Required review matrix

| Boundary | Outcome | Current-byte conclusion |
|---|---|---|
| Dispatch | `PASS` | Source and 23 executed negative fixtures still cover finite repository-relative grammar, aliases, ownership/task-source reconciliation, overlap/intersection, exact contract declarations, same-contract writers and typed `BLOCKED`/`FAIL`. Current source/test hashes: `d6370f…1580` / `ebac7e…96b8`. |
| Planner | `PASS` | Pure current-input compilation, exact 30-entry closure, fixed command/cwd, canonical task source, fingerprints, mappings and strict YAML behavior are implemented and exercised by 51 focused fixtures plus the direct closure and alias probes. Current source/test hashes: `5a710f…b7ccf` / `50e4c4…1a044`. This is implementation review evidence only; no formal receipt is implied. |
| Executor | `PASS` | Fixed-plan execution, literal adapter identity, canonical executable binding, bounded capture, timeout/signal facts, pre/post drift rejection and complete three-state aggregation are implemented and exercised by 123 focused fixtures plus the direct fake-`PATH` probe. Current source/test hashes: `369293…d4c9a` / `6e36bf…fd12a`. |
| Lifecycle/store/status | `FAIL` | The lifecycle/store/status implementation and 10 focused fixtures still prove START/flush ordering, no checker after flush failure, safe immutable publication, drift failure and explicit one-read status. The route nevertheless requires a trusted issuer packet; F1 makes the current Codex/Qoder `TASK_VALIDATION` route unexecutable. Current CLI/store/test hashes: `dfdcc5…a45` / `a227fd…8e` / `5d4b61…64c`. |
| Independent review | `FAIL` | Eight focused fixtures cover frozen subject scope/hash, independent issuer, producer/write overlap, empty-report concealment, current snapshot drift, stale inputs and distinct immutable reruns. F1 prevents the current Codex/Qoder reviewer from obtaining the trusted packet required to publish an `INDEPENDENT_REVIEW` receipt. Current source/test hashes: `ef43a6…2d9` / `a2ccce…286c`. |
| Hash DAG | `PASS` | The pure finite verifier and seven fixtures still reject self/back edges, cycles, aliases/`latest`, missing/changed nodes and duplicate identity with different bytes. Current source/test hashes: `34497f…e44` / `98e4f3…e63`. |
| Catalog decision | `FAIL` | Thirteen focused fixtures cover current canonical source/acceptance inputs, subject snapshot revalidation, exact dependencies, recursive issuer authority, unsafe/unverified inputs, non-promotion and forged approval/bootstrap rejection. F1 prevents production of the required current validation/review issuer chains and a current Codex `CATALOG_DECISION` issuer packet. Current catalog/issuer source and test hashes: `078d2d…d0fb` / `05e488…1bb7`; `9107be…82cf` / `11b57a…64d0`. |
| Cross-cutting | `FAIL` | The single CLI and typed results remain in place, missing bootstrap context fails closed, and no callback/ACK/exit zero was treated as catalog `PASS`. The UUID version restriction is a runtime compatibility failure in the formal-chain trust boundary. The incomplete required all-Gate/planning/profile commands also prevent integrated review `PASS`. |

## Per-Task outcomes

| Task | Outcome | Durable rationale |
|---|---|---|
| `LF-TSK-QLT-0005` | `PASS` | Dispatch implementation and its 23 executed adversarial fixtures satisfy the reviewed boundary on current frozen bytes. |
| `LF-TSK-QLT-0008` | `PASS` | The prior formal-closure and canonical-source defects are remediated; all 30 current closure tasks compile and same-byte source aliases fail closed. |
| `LF-TSK-QLT-0009` | `PASS` | The prior ambient-`PATH` defect is remediated with canonical executable binding and real substitution coverage. |
| `LF-TSK-QLT-0010` | `FAIL` | Store/status behavior passes focused review, but the current Desktop/Qoder trust paths cannot provide the trusted issuer packet required for a formal `TASK_VALIDATION` run. |
| `LF-TSK-QLT-0011` | `FAIL` | Review/snapshot behavior passes focused review, but the current reviewer trust paths cannot provide a trusted issuer packet for a formal `INDEPENDENT_REVIEW` receipt. |
| `LF-TSK-QLT-0012` | `PASS` | The read-only finite hash-DAG implementation and all seven focused adversarial fixtures pass on current bytes. |
| `LF-TSK-QLT-0013` | `FAIL` | Current-input and issuer-chain verification pass focused tests, but the required validation/review chain and current Codex catalog issuer packet cannot be materialized through the current runtime identities. |

A per-Task `PASS` here is an implementation-review outcome only. It is not `TASK_VALIDATION`, `INDEPENDENT_REVIEW`, or `CATALOG_DECISION` evidence.

## Serial validation command record

| # | Command | Exit/status | Observed result |
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
| 11 | `python3 -m unittest discover -s tests/gates -p 'test_issuer_packet.py'` | `0` | 45 tests, `OK` |
| 12 | `python3 -m unittest discover -s tests/gates -p 'test_*.py'` | `130` | Interrupted after terminal F1 reproduction; no complete suite result claimed |
| 13 | `python3 -m scripts.gates.planning --root .` | `NOT_RUN` | Stopped under terminal-FAIL instruction |
| 14 | `python3 -m scripts.gates.registry_profiles --root . --check` | `NOT_RUN` | Stopped under terminal-FAIL instruction |

Exact focused logs and exit ledger are under `commands/` and `commands.tsv` in the machine evidence root. Command 12 was interrupted before a complete log could be established; the ledger records its driver exit status. Commands 13–14 are explicitly `NOT_RUN`, not `PASS`.

The mandatory bootstrap CLI invocation was run separately after the bounded stop:

| Command | Exit | Observed result |
|---|---:|---|
| `python3 scripts/gates/cli.py run --mode incremental` | `1` | `FAIL/missing-evidence-context`; no formal receipt claimed or created |

Evidence: `commands/15.log` and `commands/15.exit` in the machine evidence root.

## Reviewer write-scope result

No source, test, planning, harness, OpenSpec, existing review, evidence packet, issuer packet, or formal receipt was modified by this reviewer. The only durable write is this report; every other reviewer-created artifact is below the allowed ignored run directory. No raw host or Qoder identity value appears in this report or the saved probe outputs.
