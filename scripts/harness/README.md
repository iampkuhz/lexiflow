# Qoder Runner

`qoder_task.py` is the repository entry for one non-blocking Qoder run. It was adapted from the proven runner in `feipi-session-browser-java`; LexiFlow keeps the same completion-first callback and watchdog lifecycle.

```bash
python3 scripts/harness/qoder_task.py start --task <task.json>
python3 scripts/harness/qoder_task.py status <run-id>
python3 scripts/harness/qoder_task.py result <run-id>
python3 scripts/harness/qoder_task.py ack <run-id> --parent-session-id <uuid>
```

`start` and `resume` acquire the same repository task-directory non-blocking file lock. While holding it, the runner rejects every prior run that lacks a self-consistent terminal `completion.json`, performs the cross-checkout process preflight using the full argv field (macOS `comm` can truncate an absolute qodercli path), reserves the new run, and spawns its worker. The run scan reads only completion identity/status fields; it does not read prior task prompts. Concurrent start/resume attempts fail with `BUSY` instead of passing the same preflight window.

The caller supplies the 14 fields listed as `caller_required_input` in `harness/agent-policy.manifest.yaml`. Task and change versions are exact; acceptance criteria and evidence are non-empty string lists; `parent_client` is exactly `codex`. The caller must omit `agent_id`, `run_id`, `session_id`, and `client`; the runner binds them, requires a UUID parent session in the persisted task, and validates that task before launch. The prompt requires `status`, `changed_files`, `validation`, `acceptance_evidence`, `effect_checks`, and `risks` in the agent result.

After that protected window, the command returns a run id immediately. The worker writes `completion.json` before trying `codex queue` against `CODEX_THREAD_ID`. If the worker itself cannot be spawned, the dispatcher records an explicit terminal failure; setup failures before spawn remove their private run and resume reservations so they cannot leave a permanent unknown run.

The non-LLM watchdog first checks after 300 seconds and then every 600 seconds. It only repairs a missing notification or records an unknown worker state; it does not retry, cancel, launch another Qoder, or declare validation PASS.

The allowed/forbidden path text in a task is a collaboration contract, not an operating-system sandbox. The parent must inspect the actual diff and rerun validation.
