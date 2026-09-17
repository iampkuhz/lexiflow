# LF-TSK-QLT-0005 Dispatch Preflight Design Review

> Review result: `PASS`  
> Catalog status: `PENDING_IMPLEMENTATION`  
> Reviewed artifact: `docs/development/dispatch-preflight-design.md`  
> Task: `LF-TSK-QLT-0005@1` / change `1.0.0`

The independent review accepts the design contract for implementation. This is a document-scope result; no dispatch preflight code, fixtures, Gate receipt, READY decision or permission to dispatch was produced.

## Accepted boundary

The preflight is a pure decision over three explicit inputs: one candidate view, a caller-frozen catalog/path snapshot, and caller-provided normalized active instances. It does not inspect processes, Qoder state, task directories, completion files, Gate status, leases, queues or timers. A PASS is relative only to the explicit active snapshot and cannot prove that the caller supplied every real writer.

The catalog snapshot is the canonical source for each candidate and active Task/version's descriptor hash, allowed/write claims and produced contracts. Active instances cannot narrow those sets: their descriptor hash and actual handoff allowed value must reconcile to the canonical snapshot before overlap checks run. This closes the false-PASS path where an adapter omits one active claim or contract.

## Accepted rules

- `dispatch-path-v1` has exact, one-child and descendant languages; descendant requires at least one segment. Eight fixed truth-table cases remove ambiguity around `dir/*`, `dir/x` and `dir/x/**`.
- Most-specific owner selection, unowned/ambiguous paths, owner crossing, allowed/write-claim equality and forbidden overlap have typed outcomes.
- Candidate-active write intersection and same-name public contract writers produce `BLOCKED`; malformed or unprovable input produces `FAIL`.
- CLI result mapping is `PASS=0`, `BLOCKED=1`, `FAIL=2`; no exit code is acceptance evidence by itself.
- The design keeps exactly five implementation acceptance items and one primary production module plus direct tests, within the 20–90 minute atomic-task policy.

## Review corrections incorporated

The first review rejected a required QLT-0002 receipt input, ambiguous active identity, and underspecified glob semantics. The second review rejected caller-supplied active claims/contracts because omission could create a false PASS. The final artifact removes the receipt dependency, defines a normalized active-instance schema, freezes canonical claims/contracts in the catalog snapshot, fixes the exit mapping, and defines the path languages and truth table.

## Remaining work

`LF-TSK-QLT-0005` remains pending until its implementation and direct tests prove the declared owner, claim, write-overlap and contract-writer behavior. Main Agent must then review the actual diff, rerun the required fixtures and obtain a current-input catalog receipt. This review cannot be promoted to task PASS or used as the active-set truth source.
