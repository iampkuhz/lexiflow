# G1 Bootstrap Dependency Variance

> Recorded: 2026-09-16  
> Scope: initial `LF-TSK-QLT-0001` → `LF-TSK-QLT-0002` and `LF-TSK-OPS-0001` evidence, plus the one-time non-READY bootstrap needed to create `LF-TSK-QLT-0007`–`0014`

## Observed sequence

`LF-TSK-QLT-0002` run `1ea2bbdb-ec89-4dee-83c1-18c760f98528` was dispatched after the task-template, planning catalog and two harness manifests had passed main-agent and independent semantic validation. At dispatch time, however, `LF-TSK-QLT-0001` did not yet have a catalog-level immutable Gate receipt. `LF-TSK-QLT-0003` later froze the control-plane design but intentionally did not create its executable routes; the activated `LF-TSK-QLT-0007`–`0014` chain now owns that implementation.

The DAG requires `LF-TSK-QLT-0001@1` / change `1.0.0` to be `PASS` before `LF-TSK-QLT-0002@1` / change `1.0.0` becomes formally `READY`. The dispatch therefore produced **bootstrap implementation evidence**, not proof that the dependency or `LF-TSK-QLT-0002` achieved catalog `PASS`. The original dispatch time and run identity must remain unchanged.

`LF-TSK-OPS-0001` v2 / change `1.1.0` was also designed and independently reviewed before the control plane existed. Its ADR, official-source audit, local inventory and post-approval reproducibility plan can satisfy a change-local documentation slice, but the local bootstrap validation cannot issue or replace its required catalog receipt.

## Available bootstrap evidence

- The architecture and planning reviews independently found the schema and DAG semantically valid before dispatch.
- The runner conformance suite passed 17 tests before dispatch.
- A local-only bootstrap receipt records the post-dispatch schema validation at `tmp/quality/bootstrap/9a59133f-b4ea-4a01-a8f1-2c294e23091a/receipt.json`. Its own `catalog_task_status` is `PENDING_GATE_RECEIPT`; it is not a G1 receipt.
- A second local-only bootstrap receipt records the OPS proposal/inventory validation at `tmp/quality/bootstrap/8ea89f88-8528-496c-9f23-f1569ce859f7/receipt.json`. Its `validation_status` is `PASS` while its `catalog_task_status` remains `PENDING_GATE_RECEIPT`; later independent document review found and repaired additional reproducibility-plan gaps. Neither result may be promoted retroactively.
- The Qoder completion, exit code or callback for `LF-TSK-QLT-0002` can only prove that one implementation run ended. All three Qoder implementations failed Main Agent acceptance. The bounded Codex takeover and [final implementation review](qlt-0002-final-review.md) establish change-local implementation PASS; catalog validation remains separate.

## Required closure order

1. Preserve every `LF-TSK-QLT-0002` run identity and failure. Run `1ea2bbdb-ec89-4dee-83c1-18c760f98528`, rework 1 `37d4a455-e1ca-4b2a-a38f-8a19df1f27f2`, and final Qoder rework `0fd5af62-5b5f-40b0-982b-c639341543af` remain `FAIL`; [the initial review](qlt-0002-run-1-review.md), [rework 1 review](qlt-0002-rework-1-review.md), and [final implementation review](qlt-0002-final-review.md) distinguish those runs from the accepted Codex takeover.
2. Treat the reviewed `LF-TSK-QLT-0003` design as bootstrap design evidence only. It is not the planner, runner or receipt implementation and cannot issue a receipt.
3. Perform the minimum one-time non-READY implementation bootstrap in the activated DAG order: materialize result evidence and trusted issuer packets (`QLT-0007`, `QLT-0014`); implement planner and executor (`QLT-0008`, `QLT-0009`); create validation lifecycle/status (`QLT-0010`); serially add independent review (`QLT-0011`); add the read-only hash verifier (`QLT-0012`); then add catalog decision (`QLT-0013`). Each implementation needs its own run evidence and independent review. None becomes READY or PASS retroactively.
4. Once the CLI exists, issue current-input receipts from dependency roots forward: validate `QLT-0001`, `QLT-0006`, accepted `QLT-0002`, reviewed `QLT-0003`, and then `QLT-0004`/`0005`; issue `QLT-0007` and `0014`, then `0008`, `0009`, `0010`, `0011`, `0012`, and `0013` only after each current pinned dependency is proven.
5. Run the independently reviewed ADR/inventory/reproducibility checks through the control plane and issue the first catalog receipt for `LF-TSK-OPS-0001` v2 / change `1.1.0`; planned post-approval build commands remain explicitly unexecuted. Apply the same current-input chain to every other `ARCH-0008@2/1.1.0` blocking dependency.
6. Record this variance in affected receipts or their review evidence. Do not backdate a receipt, rewrite a dependency, use `latest`, or label any earlier implementation/review activity `READY` retroactively.

If the accepted `LF-TSK-QLT-0002` implementation cannot validate the changed planning/OpenSpec context, it must be revised through a new run under the same Task id and an incremented version when scope or acceptance changes.
