# Phase 1 Gate JIT Task Draft Review

> Historical draft review. It records the original eight-task decomposition and its then-current version arithmetic. The layered Gate migration supersedes those version pins and receipt contracts; current execution must use [`planning/workstreams.yaml`](../../planning/workstreams.yaml) and registry v3.

> Review result: `PASS`  
> Draft status: `DRAFT_NON_READY`  
> Reviewed artifact: `tmp/quality/gate-jit-task-draft.yaml`

The independent review accepted the eight-task decomposition and its catalog migration arithmetic. The draft is planning evidence, not a READY grant, implementation result, or Gate receipt.

## Accepted decomposition

| Task | Outcome |
|---|---|
| `LF-TSK-QLT-0007` | Explicit six-field result evidence packet |
| `LF-TSK-QLT-0014` | Trusted issuer packet from registered authority evidence |
| `LF-TSK-QLT-0008` | Pure plan compiler and fixed-command registry |
| `LF-TSK-QLT-0009` | Fixed-argv execution and typed three-state aggregation |
| `LF-TSK-QLT-0010` | Common receipt handler/store, TASK_VALIDATION lifecycle, and read-only status |
| `LF-TSK-QLT-0011` | Independent-review route and immutable review receipt |
| `LF-TSK-QLT-0012` | Pure-read acyclic evidence hash verifier |
| `LF-TSK-QLT-0013` | Acceptance/dependency reconciliation and catalog decision receipt |

Each task has one QLT owner, explicit task source, narrow allowed and forbidden files, direct tests, a deterministic validation command, four acceptance assertions, and a 60–90 minute estimate. The only repeated write claim is `scripts/gates/cli.py`; QLT-0010, QLT-0011, and QLT-0013 are transitively ordered, and dispatch preflight must reject overlapping active claims.

QLT-0014 does not accept actor or role strings as authority. It uses a QLT-owned verifier registry and fixed Qoder, current Codex session, authenticated operator, or CI workload evidence. QLT-0013 directly consumes QLT-0004 traceability and verifies the acceptance registry hash, orphan and duplicate cases, and current mappings. Raw QLT-0009 output is consumed through the QLT-0010 validation receipt.

## Reconstructed migration

Applying the draft and its explicit `ARCH-0008` migration to the current catalog yields:

- 113 tasks: 32 in P1, with 13 P1 capacity-envelope tasks remaining;
- 256 edges: 231 hard, 24 contract, and 1 soft;
- 13 produced contracts;
- `ARCH-0008` task version 2/change 1.1.0 with five retained hard dependencies plus the QLT-0013 catalog-decision contract;
- a 30-task/60-edge `ARCH-0008` blocking closure;
- nine direct consumer pins, G2 `entry_requires`, and four P2 task-level prerequisite pins updated to the new exit version.

The draft remains immutable `DRAFT_NON_READY` provenance. Its reviewed task descriptors and migration have since been copied into the canonical catalog, where they remain planned work pending implementation and current-input receipts. Activation does not grant READY, PASS, or permission to backfill a bootstrap run.
