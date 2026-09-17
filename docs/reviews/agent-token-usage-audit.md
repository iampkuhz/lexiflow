# Agent Token Usage Audit

> Window: 2026-09-15 17:02 UTC to 2026-09-16 01:02 UTC  
> Trigger: user reported excessive token consumption and suspected busy wait

## Evidence

The local Qoder run records contain ten completed implementation runs in this eight-hour window; no Qoder run remains active:

| Task | Completed runs | Qoder internal turns | Prompt characters | Process time |
|---|---:|---:|---:|---:|
| `LF-TSK-QLT-0002` | 3 | 95 | 11,576 | 34.7 min |
| `LF-TSK-QLT-0007` | 3 | 66 | 19,169 | 32.3 min |
| `LF-TSK-QLT-0008` | 3 | 92 | 28,090 | 30.9 min |
| `LF-TSK-QLT-0009` | 1 | 80 | 9,972 | 30.8 min |

The ten completed runs total 333 Qoder internal turns, 68,807 prompt characters and about 128.7 minutes of Qoder process time. Every completed run produced a callback record. All watchdog logs contain zero lines, and successive Qoder starts were separated by more than five minutes.

The current Codex account usage snapshot reports 37% of the seven-day window consumed. The available usage API does not provide a reliable per-run token breakdown, so this audit does not invent one.

## Finding

There is no local evidence of a tight polling loop or watchdog busy wait. The high consumption came primarily from execution strategy:

1. `max_rework_rounds=2` allowed three Qoder implementation runs for each of three tasks.
2. Later handoffs repeated large design contracts; individual prompts reached roughly 9,000–10,000 characters.
3. Codex Sub-Agents were repeatedly created with full conversation history, multiplying a large parent context.
4. Gate control-plane work expanded into repeated implementation, independent probes and takeover fixes before the final Phase 1 user decision.

The absence of a busy loop does not make the consumption acceptable. Repeated full-context delegation and three-run rework chains were avoidable.

## Controls applied

- Qoder remains limited to one active run.
- Qoder prompt size is capped at 8,000 characters; detailed design must be referenced by bounded file and section locators.
- Each Task may use one initial Qoder run and at most one correction run. A third Qoder implementation run is forbidden.
- Codex Sub-Agent concurrency is capped at one and defaults to `fork_turns=none`.
- Catalog Tasks remain atomic planning units, while Codex work is grouped under a stable `work_package_id` and exact `task_ids[]` into coherent packages containing at least two compatible Task outcomes and at least 120 estimated minutes; single-file fixes, single-command checks and isolated read-only reviews remain in the main session. Every Task keeps separate outcome evidence even though one Agent session covers the package.
- The Harness contract test now requires `agent-policy`, `workstreams`, `task-template` and runtime entry metadata to agree on the 120–360 minute package envelope, minimum Task count and compact callback behavior. Turn count and token count are explicitly excluded from acceptance evidence.
- Sub-Agent callbacks carry a compact signal, artifact locators and validation commands rather than full logs or context.
- New Agent dispatch remains blocked while a Qoder run is active or unknown.
- The macOS process preflight now inspects full argv rather than truncated `comm`.

The historical records remain unchanged. These controls govern future dispatch and do not retroactively promote prior runs to `PASS`.
