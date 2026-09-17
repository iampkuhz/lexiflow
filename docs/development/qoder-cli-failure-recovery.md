# Qoder CLI failure diagnosis and explicit recovery

## This incident

Run `972690fa-8c7d-45d1-8baf-7f995519f94f` failed before the first model response. Its unchanged Result already included `error_code: 118`; earlier triage read only `errors` and missed it. [Qoder's official Result error reference](https://docs.qoder.com/cli/sdk/errors) defines 118 as **personal Credits exhausted**. This identifies the incident; it is not a live account-balance query.

The separate, PID/session/cwd/start-time-matched CLI run log contains raw service code 112 and HTTP 403. Raw service codes, normalized Result codes and process exit codes are distinct signals. Do not replace Result 118 with raw 112, or infer an authentication/quota result from process exit 1.

## Runner behavior

The executable entrypoint is `scripts/harness/qoder_task.py`; shared constraints remain in `AGENTS.md` and `harness/agent-policy.manifest.yaml`.

- Completion `failure` records fixed category/action, `result_error_code`, raw `service_code`, `http_status`, retryability and diagnostic source. The callback contains only safe enums/numbers, not errors, pricing URLs, prompts, credentials or logs.
- Prefer the structured Result. Missing transport details may be recovered once from a bounded matching CLI **run** log. Ambiguous runs, identity/time/cwd mismatches and symlinked logs are ignored; private session/conversation files are never read.
- Static preflight reports `model_access_checked: false`. Tool/version/context PASS is not proof of account/model availability, product validation or delivery acceptance.
- Internal model-request retries are disabled. Active/unknown guards and the dispatch lock remain in effect. One initial and one correction are counted by stable Task id, including historical runs; changing anchor/package/version does not reset the budget. Legacy records without package Task ids count their known anchor only.
- The latest known account/access denial blocks further dispatch, including after ACK. ACK only acknowledges consumption; it cannot repair account capacity.

## Recovery

First check the CLI account's Plan/Credits using the [official usage panel](https://docs.qoder.com/cli/usage) and resolve the account/model-access issue. Do not silently change model/provider/account or repeatedly submit the same task.

After the operator has resolved the external blocker, an **explicit** parent repair may use the existing `resume` entrypoint with `--followup` and the new `--runtime-recovery-confirmed` flag. No example here authorizes recovery of this incident's package: current Task contracts, scope rules and hash-bound harness must still pass preflight. A failed terminal run can now be repaired explicitly; nothing restarts automatically.

The flag is an operator assertion, not a balance/access probe. It cannot bypass active/unknown or stable-Task attempt limits. Its provenance is recorded in runner-owned `runtime-recovery.json`, with `account_access_verified: false`, without adding fields to frozen caller handoffs. For a different, not-yet-attempted package, `start` accepts the same assertion after external recovery.

## Verification limits

Only offline fixture tests and read-only incident diagnosis were performed. The side session's default Python 3.14 lacks PyYAML; tests reused the reference repository's existing Python 3.12/PyYAML environment without installing dependencies or changing PATH globally. Future callers must use a dependency-ready runtime. Formal incremental Gate acceptance still requires current evidence and an independent issuer; offline tests are not that receipt.
