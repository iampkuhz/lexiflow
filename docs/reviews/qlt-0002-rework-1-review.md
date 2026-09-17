# LF-TSK-QLT-0002 · Rework 1 Review

> Review result: `FAIL`  
> Catalog status: `PENDING_GATE_RECEIPT`  
> Reviewed run: `37d4a455-e1ca-4b2a-a38f-8a19df1f27f2`  
> Final allowed rework run: `0fd5af62-5b5f-40b0-982b-c639341543af`

This record preserves the main-agent and independent review of the first rework. It is bootstrap implementation evidence, not a Gate receipt. The Qoder process finishing with exit code `0`, its callback being queued, and its 39 local tests passing did not satisfy the task acceptance.

## Identity and lifecycle

The rework reused Qoder session `4ff13cd8-cd8d-4fb2-b665-7a20105c2123` and preserved `LF-TSK-QLT-0002` task version `1`, change version `1.0.0`, client `qoder`, parent client `codex`, and parent session `01a0a5da-3abb-7733-bc2b-c8e379349c04`. The runner generated new agent `agent_248f95f6` and run `37d4a455-e1ca-4b2a-a38f-8a19df1f27f2`.

`task.json` was written at epoch `1789493555.5459952`, `started.json` at `1789493555.5989082`, `completion.json` at `1789494125.4431791`, and `callback.claim` at `1789494125.444251`. Completion therefore preceded callback claim by about 1.072 ms. `callback.json` records `queued` for the exact parent session. Main Agent consumed the result once and acknowledged the terminal run; acknowledgement records handling only and is not acceptance.

The fallback sequence respected the scheduling policy. The first correct terminal status read occurred more than 600 seconds after the earlier successful running-state probe. One intervening command used the wrong option form and failed in local argument parsing before reading run state; it was still conservatively counted as a fallback attempt, and the next attempt was delayed by at least 600 seconds.

## Positive evidence

On the unchanged four validator inputs, both Main Agent and the independent reviewer observed:

```text
python3 -m unittest discover -s tests/gates -p 'test_*.py'
39 tests: PASS

python3 -m scripts.gates.planning --root . --verbose
105 tasks / 11 registered checks: PASS
```

The implementation repaired several first-run defects: soft edges no longer satisfy blocking ancestry; hard and contract edges reject a non-`PASS` required result; contract edges verify producer identity; task-level approval evidence overrides are rejected; and the P2+ fixture covers Gate-exit ancestry. No current task count or OPS version was hard-coded.

## Acceptance-scope false negatives

Independent in-memory mutation probes still produced `PASS` for invalid inputs:

1. All four handoff sources synchronously replaced one canonical caller field or one canonical result field while preserving the 14/6 counts.
2. All four sources synchronously changed or removed runner identity metadata, disabled adapter enforcement, shortened adapter caller fields, or changed declared caller schema values.
3. A dependency retained a required pin key but set its task or change version value to `null`, including a direct previous-Gate hard edge.
4. Workstream, epic, capability, and Gate IDs were duplicated without creating a duplicate task ID; a declared ID policy was also changed to an unsupported value.
5. `path_ownership.resolution` was changed to an unsupported rule, or a scope was removed so a real governed file resolved to no owner.
6. G1 exit ancestry to a P0 G1 entry was removed, or a Gate's `entry_tasks` drifted from the phase-entry registry.
7. Malformed phase-entry containers could escape during `from_data`, while other malformed Gate/phase values produced only a generic exception diagnostic or an incorrect PASS.

The Qoder response also omitted five of the six required result sections. It provided a Status and narrative table but no separately identifiable `changed_files`, `validation`, `acceptance_evidence`, `effect_checks`, or `risks` sections. Free text, exit code, and test counts cannot fill those required fields.

## Current-input variance

The validator inputs remained byte-identical to the first review:

| Input | SHA-256 |
|---|---|
| `planning/workstreams.yaml` | `913b37bc09e85a6423905a10c57dc0fb8a35bff482c0f7afa942eb17d9f392a6` |
| `planning/task-template.yaml` | `7a121dcb0f351aa9e5aa4ece71f8de78775503ae9f78e3844adca07bad651466` |
| `harness/agent-policy.manifest.yaml` | `4c213600229ee41bffac8f78671e6a034ec511270d22e9532ce03555212884ba` |
| `harness/agent-runtime.manifest.yaml` | `0d644f74c87a455d9aa8c1359ff4790bcb392507f4487b55f88a0ddc670bb31d` |

The OpenSpec checklist changed after dispatch to `fab862d3fd22fa5f14ff5a46219244ff1b1bd928b23398dce6df9352a7f53438`, but the validator does not consume that file. The review therefore does not attribute the false negatives to post-dispatch input drift.

## Decision

Rework 1 is `FAIL`. The remaining defects are still within the original stable-ID, owner, typed-DAG, phase-entry, cross-source handoff and structured-result acceptance scope, so task version `1`, change version `1.0.0`, allowed files and Qoder session remain unchanged.

The second and final permitted rework is run `0fd5af62-5b5f-40b0-982b-c639341543af`. It must add exact mutation fixtures for every false-negative class before changing the validator. If any required counterexample remains accepted, the implementer has exhausted its rework allowance and the task must be split, transferred, completed by the Main Agent, or returned to the Gate as non-PASS.
