# Qoder Concurrency Incident

> Date: 2026-09-16  
> Result: `PASS` for the runner correction and terminal-state recovery; no catalog Task result is implied

## Observation

Multiple Qoder callbacks and supersession chains were present at the same time. The user removed several blocked callbacks. A bounded process snapshot then showed one remaining `qodercli` process for `LF-TSK-QLT-0009`; its run directory did not yet contain `completion.json`.

This violated the operating expectation that LexiFlow normally has one active Qoder run. No new start or resume is allowed while that process or any unknown run remains.

## Root cause

The cross-checkout preflight in `scripts/harness/qoder_task.py` read `ps ... comm=` and compared its basename with `qodercli`. On macOS, `comm` truncated the absolute executable path to `/Users/zhehan/.l`, so the preflight could miss a live Qoder process.

## Correction

The preflight now reads `ps ... args=` and checks the full executable argv element. A regression test covers an absolute Qoder path whose `comm` value would be truncated. The task-directory lock, missing/unknown completion scan, single-run policy and completion-first callback remain required.

Validation after the correction:

- `python3 -m unittest discover -s tests/harness -p 'test_*.py'`: 20 tests passed, including full-path process detection, prompt-budget enforcement and machine-contract limit alignment.
- `python3 -m py_compile scripts/harness/qoder_task.py scripts/harness/qoder_task_lifecycle.py`: passed.

## Acceptance boundary

The corrected runner prevents future dispatch through this entrypoint while a full-path Qoder process is visible. It cannot retroactively prove that earlier starts respected the single-run rule. Historical run directories are preserved as evidence; they must not be deleted, rewritten or promoted to Task `PASS` merely from callback or exit status.

## Resolution

`LF-TSK-QLT-0009` run `18500d58-c589-4dcc-94c3-9b1db7d9a981` later wrote an identity-matched terminal `completion.json` before its queued callback. Main Agent independently ran 117 executor tests and the 435-test current Gate suite, corrected uncovered executor defects, then ACKed that exact run. No replacement or concurrent Qoder run was started. The incident is closed for current dispatch; its historical overlap remains an audit fact rather than evidence that earlier scheduling was valid.
