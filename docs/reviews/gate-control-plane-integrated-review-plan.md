# Gate Control Plane Integrated Review Plan

> Change: `establish-lexiflow-foundation`  
> Catalog owner: `LF-WS-QLT`  
> Review owner/task: `LF-TSK-ARCH-0008@2` / change `1.1.0`  
> Execution state: three frozen reviews have closed the formal-root, executable-binding, canonical-source and UUIDv7 defects. The third review then found that read-only review/catalog plans could not express an empty reviewer write set. The evidence packet and planner now support a strict zero-write packet for `INDEPENDENT_REVIEW` and `CATALOG_DECISION`, while `TASK_VALIDATION` still requires at least one changed file. One closure review of the new bytes is pending. This file is a review packet, not review evidence or a Gate receipt.

## Review outcome and package boundary

Run one read-only Codex Sub-Agent work package after the `QLT-0010–0013` implementation package has reached a terminal callback and the Main Agent has rerun its declared validation commands. The review covers the current bytes of the complete executable chain, rather than creating one short review per atomic Task:

- `LF-TSK-QLT-0005`: dispatch preflight and its current fixed-command registry entry;
- `LF-TSK-QLT-0007`: trusted evidence materialization, including typed zero-write read-only packets;
- `LF-TSK-QLT-0014`: trusted issuer materialization, including external UUIDv7 provenance and UUIDv4 packet/nonce boundaries;
- `LF-TSK-QLT-0008`: pure planner against the current 30-entry catalog-derived registry;
- `LF-TSK-QLT-0009`: fixed-argv executor and typed aggregation;
- `LF-TSK-QLT-0010`: validation lifecycle, immutable store and one-read status;
- `LF-TSK-QLT-0011`: independent-review route and receipt;
- `LF-TSK-QLT-0012`: read-only evidence hash-DAG verification;
- `LF-TSK-QLT-0013`: current-input catalog decision.

The package is expected to require 2–6 hours. It has one review outcome, one reviewer identity and compatible read-only scope. The reviewer must not delegate, start Qoder, edit implementation/tests/contracts, or create formal receipts. Any required source correction returns `FAIL` with a bounded finding; the Main Agent fixes it and later starts at most one fresh integrated re-review package.

## Dispatch preconditions

The Main Agent must establish all of the following before dispatch:

1. No implementation writer remains active for the reviewed files.
2. The implementation package callback contains only its compact signal and durable artifact locators.
3. The Main Agent has run every declared focused command and recorded its own results.
4. `python3 -m scripts.gates.planning --root .` and the Harness contract suite pass on current bytes.
5. The review handoff freezes SHA-256 for every reviewed source, test, `harness/gate-check-registry.yaml`, `harness/agent-policy.manifest.yaml`, `harness/agent-runtime.manifest.yaml`, `planning/workstreams.yaml`, `planning/task-template.yaml`, the Gate design and this packet.
6. The reviewer receives `fork_turns=none`, generates its own unique run identity, and has not authored any reviewed source or test file.

If any precondition is missing, do not dispatch. A callback, exit code, prior nine-entry review or passing focused suite is not a substitute.

## Read and write scope

The handoff permits read access only to the frozen files needed by the nine reviewed Tasks, their acceptance cases, current catalog, OpenSpec mappings and prior review history. The only durable write target for the closure review is:

- `docs/reviews/gate-control-plane-integrated-implementation-closure-review.md`

The historical `docs/reviews/gate-control-plane-integrated-implementation-review.md`, first complete `FAIL` report `docs/reviews/gate-control-plane-integrated-implementation-review-current.md`, UUID finding report `docs/reviews/gate-control-plane-integrated-implementation-rereview.md`, and read-only reachability report `docs/reviews/gate-control-plane-integrated-implementation-final-review.md` remain unchanged. The closure reviewer writes only `docs/reviews/gate-control-plane-integrated-implementation-closure-review.md`. Temporary test output may be written below an ignored run-specific directory under `tmp/quality/verification/`. All implementation, test, planning, harness, OpenSpec and existing review files are forbidden write targets. The reviewer must report unexpected worktree mutation as `FAIL/reviewer-write-scope-violation`.

## Required review matrix

The report must bind each conclusion to current file hashes and cover all rows below. A test name alone is not evidence that the relevant negative path executed; the reviewer must inspect the implementation and the fixture that proves it.

| Boundary | Required proof and adversarial cases |
|---|---|
| Dispatch | finite repository-relative path grammar; symlink/case aliases; owner and exact task-source reconciliation; candidate/active write overlap; allowed/forbidden intersection; exact contract-path declaration; same-contract writer conflict; `BLOCKED` versus malformed-input `FAIL` |
| Issuer | external Codex/Qoder/human/CI session and run identities accept only canonical non-nil RFC UUID v1–v8; current Codex and a real ACKed Qoder UUIDv7-parent path materialize; `issuer_instance_id`, attestation, nonce and replay identities remain canonical UUIDv4; forged authority, stale/replayed evidence and authorization mismatch fail closed |
| Evidence/planner | generic materializer accepts a cryptographically bound zero-write packet only when scope, claims, snapshot and diff are all empty; `TASK_VALIDATION` rejects that packet; real current `INDEPENDENT_REVIEW` and `CATALOG_DECISION` planner routes compile it; non-empty overlap still fails; caller-reported empty scope cannot conceal subject mutation; zero writes/processes/random scan; exact current Task/change/contract and 30-entry catalog-derived registry; canonical `planning/workstreams.yaml` locator; all 30 G1 closure Tasks compile from current packets; fixed argv/cwd; entry hash and full plan fingerprint; acceptance/effect/risk mappings; recursive YAML alias, non-NFC and explicit-null rejection |
| Executor | only frozen plan commands; literal adapter/command identity; registered `python3` resolves to a canonical absolute interpreter with pre/post content hash and inode binding; inherited `PATH` cannot substitute it; independent stdout/stderr limits; timeout, signal and capture facts; skipped/not-run/unavailable/empty/malformed never `PASS`; complete `FAIL > BLOCKED > PASS` aggregation |
| Lifecycle/store/status | explicit packet resolution; caller-visible START/run ID and disk flush before checker; no checker on flush failure; safe run paths; exclusive immutable publication; collision/interruption/input drift cannot publish `PASS`; explicit-run one-read status only |
| Independent review | frozen subject receipt/hash and scope; trusted reviewer packet; producer self-review, write-set overlap, self-reported/plan-empty concealment, current subject snapshot drift and stale inputs rejected; subject remains byte-identical; every re-review receives a new immutable run |
| Hash DAG | pure read; finite receipt/manifest/leaf/prior graph; recorded bytes and identities reconcile; self/back edges, cycles, aliases/latest, missing nodes and duplicate identity with different bytes rejected |
| Catalog decision | current acceptance registry hash and mappings; no orphan/duplicate/stale cases; validation subject snapshots reverified current; validation, review, hash and exact dependency receipts reconciled; prior receipt issuer authority reverified; missing/unsafe/unverified inputs fail closed; prior `BLOCKED`/`FAIL` cannot be promoted; no raw executor bypass, bootstrap backfill or inferred user approval |
| Cross-cutting | single CLI route; only `PASS/BLOCKED/FAIL`; no secrets or user learning data; no `latest` evidence lookup; no silent fallback; callback/ACK/exit zero never treated as Task or catalog `PASS` |

## Required commands

Run these commands serially from the repository root and preserve exact exit status in the review artifact. A missing or skipped command prevents review `PASS`.

```bash
python3 -m unittest discover -s tests/harness -p 'test_qoder_runner.py'
python3 -m unittest discover -s tests/gates -p 'test_dispatch_preflight.py'
python3 -m unittest discover -s tests/gates -p 'test_evidence_packet.py'
python3 -m unittest discover -s tests/gates -p 'test_gate_planner.py'
python3 -m unittest discover -s tests/gates -p 'test_gate_executor.py'
python3 -m unittest discover -s tests/gates -p 'test_task_contracts.py'
python3 -m unittest discover -s tests/gates -p 'test_registry_profiles.py'
python3 -m unittest discover -s tests/gates -p 'test_gate_lifecycle.py'
python3 -m unittest discover -s tests/gates -p 'test_independent_review.py'
python3 -m unittest discover -s tests/gates -p 'test_hash_dag.py'
python3 -m unittest discover -s tests/gates -p 'test_catalog_decision.py'
python3 -m unittest discover -s tests/gates -p 'test_issuer_packet.py'
python3 -m unittest discover -s tests/gates -p 'test_*.py'
python3 -m scripts.gates.planning --root .
python3 -m scripts.gates.registry_profiles --root . --check
```

The integrated implementation review must not claim that `python3 scripts/gates/cli.py run --mode incremental` produced a formal catalog receipt unless the separate current-input dependency chain exists and the Main Agent explicitly dispatches that Gate run. During bootstrap review, a typed refusal caused by absent formal prerequisites may be correct behavior; replacing it with synthetic `PASS` evidence is a review failure.

## Decision rules and compact completion signal

- `PASS`: all required commands ran and passed, all matrix rows are proven on frozen current bytes, the reviewer made no forbidden writes, and no actionable finding remains.
- `BLOCKED`: the reviewer cannot execute a required check because a declared external environment or tool is unavailable, while inspected implementation evidence does not establish a defect. Missing repository inputs or unimplemented required behavior are `FAIL`, not `BLOCKED`.
- `FAIL`: any behavior, identity, immutability, freshness, privacy, scope or result-semantics requirement is violated or unverified.

The callback contains only `status`, stable `work_package_id=LF-WP-QLT-INTEGRATED-REREVIEW-004`, exact ordered `task_ids=[LF-TSK-QLT-0005, LF-TSK-QLT-0007, LF-TSK-QLT-0014, LF-TSK-QLT-0008, LF-TSK-QLT-0009, LF-TSK-QLT-0010, LF-TSK-QLT-0011, LF-TSK-QLT-0012, LF-TSK-QLT-0013]`, reviewer-generated `run_id`, the review artifact locator, validation command list and at most three blocking findings. The durable artifact records a separate outcome for every reviewed Task. The callback must not contain full source, full logs or a replay of this packet. The Main Agent independently reruns the declared commands, verifies current hashes and inspects every finding before accepting the review artifact.
