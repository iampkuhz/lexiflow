"""Shared local verification plan and producer-submission implementation.

Public command entrypoints are change_verify.py, repository_verify.py, and
certify_submit.py.  This module deliberately owns no lock or commit policy.
"""
from __future__ import annotations

import json
import subprocess
import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gates.planner import PlannerError, _catalog, _registry, _yaml, _dispatch, _match, canonical_json_bytes, sha256_bytes
from scripts.gates.acceptance_cases import case_ids


class LocalVerifyError(ValueError):
    def __init__(self, result: str, code: str, detail: str):
        self.result, self.code, self.detail = result, code, detail
        super().__init__(detail)


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise LocalVerifyError("BLOCKED", "git-unavailable", result.stderr.decode("utf-8", "replace").strip() or "git command failed")
    return result.stdout


def _descriptor(root: Path, locator: str) -> dict[str, str]:
    path = root / locator
    if not path.is_file():
        raise LocalVerifyError("FAIL", "consumed-input-missing", locator)
    return {"locator": locator, "state": "present", "sha256": sha256_bytes(path.read_bytes())}


def compile_local_plan(root: Path, changed: list[str], mode: str, *, verification_scope: str = "change-targeted") -> dict[str, Any]:
    if mode not in ("incremental", "full"):
        raise LocalVerifyError("FAIL", "invalid-mode", mode)
    try:
        catalog = _yaml((root / "planning/workstreams.yaml").read_bytes(), "planning/workstreams.yaml")
        tasks, owners, subjects = _catalog(catalog)
        cases = set(case_ids((root / "docs/product/product-brief.md").read_text(encoding="utf-8")))
        registry_value = _yaml((root / "harness/gate-check-registry.yaml").read_bytes(), "gate-check-registry.yaml")
        entries = _registry(registry_value, tasks, owners, subjects, cases)
    except (OSError, UnicodeError, PlannerError) as exc:
        raise LocalVerifyError("FAIL", getattr(exc, "code", "registry-contract-invalid"), str(exc)) from None
    checks = []
    for entry in entries:
        if verification_scope not in entry.get("verification_scopes", []):
            continue
        reasons = []
        if mode == "full":
            if "full" in entry["modes"]:
                reasons.append({"kind": "full-mode"})
        elif "incremental" in entry["modes"]:
            for path in changed:
                if any(_match(_dispatch(trigger["path"]), path) for trigger in entry["triggers"]):
                    reasons.append({"kind": "changed-file", "changed_file": path})
        if not reasons:
            continue
        checks.append({
            "check_id": entry["check_id"], "check_version": entry["check_version"], "owner": entry["owner"],
            "subject_task": {"task_id": entry["subject_task_id"], "task_version": entry["subject_task_version"],
                "change_version": entry["subject_change_version"], "owner": owners[entry["subject_task_id"]]},
            "command_id": entry["command_id"], "fixed_argv": entry["fixed_argv"], "cwd": entry["cwd"],
            "timeout_seconds": entry["timeout_seconds"], "consumed_inputs": [_descriptor(root, p) for p in entry["consumed_inputs"]],
            "outcome_contract": entry["outcome_contract"], "required": entry["required"], "selection_reasons": reasons,
            "effect_check_ids": entry["effect_check_ids"],
        })
    payload = {"schema_version": "lexiflow.gate-plan.v1", "mode": mode, "checks": checks, "local_only": True,
               "verification_scope": verification_scope, "changed_files": changed}
    payload["content_fingerprint"] = sha256_bytes(canonical_json_bytes(payload))
    return payload


def certify_submit(root: str | Path, *, task_id: str, run_id: str,
                   confirm_scope_review: str) -> dict[str, Any]:
    """Materialize subject evidence from an explicitly acknowledged Change Verify.

    This is producer submission only.  It never creates an issuer or a formal
    receipt, and the acknowledgement binds the author's scope self-review
    rather than turning change context into an edit/commit permission.
    """
    from scripts.gates.evidence_packet import (
        canonical_json, materialize_codex_main_task_evidence, reconcile_snapshot_scope,
        _normalize_scope_string,
    )
    from scripts.harness.local_codex_runtime import bind_main_task
    repo = Path(root).resolve()
    if confirm_scope_review != run_id:
        raise LocalVerifyError("BLOCKED", "scope-review-confirmation-required", "pass --confirm-scope-review <change-verify-run-id>")
    base = repo / "tmp/quality/change-verification" / run_id
    if not base.is_dir():
        raise LocalVerifyError("BLOCKED", "change-verification-missing", run_id)
    summary = json.loads((base / "summary.json").read_text()); plan = json.loads((base / "plan.json").read_text())
    if summary.get("execution_result") != "PASS":
        raise LocalVerifyError("BLOCKED", "current-change-verification-required", "the acknowledged Change Verify must have execution_result PASS")
    changed = summary["scope_review"]["changed_files"]
    diff = _git(repo, "diff", "--binary", summary["base"], "--")
    from scripts.gates.evidence_packet import extract_diff_file_set
    if extract_diff_file_set(diff) != set(changed):
        raise LocalVerifyError("BLOCKED", "subject-diff-incomplete", "formal submission requires tracked current diff without untracked-file drift")
    bound = bind_main_task(repo, task_id)
    projection_path = bound["projection"]; projection = json.loads((repo / projection_path).read_text())
    catalog = _yaml((repo / "planning/workstreams.yaml").read_bytes(), "planning/workstreams.yaml")
    tasks, _, _ = _catalog(catalog); task = tasks[task_id]
    allowed = ", ".join(task["allowed_files"]); forbidden = ", ".join(task["forbidden_files"])
    normalized_allowed = _normalize_scope_string(allowed); normalized_forbidden = _normalize_scope_string(forbidden)
    claims = sorted(item["path"] for item in task.get("file_claims", []) if isinstance(item, dict) and isinstance(item.get("path"), str))
    if not claims:
        raise LocalVerifyError("BLOCKED", "task-file-claims-required", "formal subject task has no explicit file claims")
    snapshots = {}
    for item in changed:
        path = repo / item
        snapshots[item] = {"state": "present", "sha256": sha256_bytes(path.read_bytes())} if path.is_file() else {"state": "absent"}
    artifacts = base / "subject"; artifacts.mkdir(exist_ok=False)
    completion = {**projection, "status": "completed", "exit_code": 0}
    for name, content in {
        "completion.json": canonical_json(completion), "stdout.json": (base / "execution.json").read_bytes(),
        "stderr.json": b"{}", "snapshot.json": canonical_json({"schema_version": "lexiflow.changed-file-snapshot.v1", "files": snapshots}),
        "diff.patch": diff,
    }.items(): (artifacts / name).write_bytes(content)
    def desc(path: Path) -> dict[str, str]:
        return {"locator": str(path.relative_to(repo)), "sha256": sha256_bytes(path.read_bytes())}
    test = desc(base / "execution.json")
    effects = {effect: "PASS" for check in plan["checks"] for effect in check.get("effect_check_ids", [])}
    if not effects: effects = {"local-verification": "PASS"}
    raw_strings = {"allowed_files": allowed, "forbidden_files": forbidden}
    normalized = {"allowed_files": normalized_allowed, "forbidden_files": normalized_forbidden, "canonical_file_claims": claims}
    reconciliation = reconcile_snapshot_scope(changed, changed, list(snapshots), set(changed), normalized, raw_strings)
    attestation = {"actor_id": projection["agent_id"], "reviewed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "result_fields": {"status": "PASS", "changed_files": changed, "validation": {"status": "PASS", "evidence_locator": test["locator"]},
            "acceptance_evidence": [test["locator"]], "effect_checks": effects,
            "risks": ["Independent validation remains required."]},
        "field_source_bindings": {"status": "explicit-main-agent-review", "changed_files": "reviewed-snapshot-diff",
            "validation": "test-evidence", "acceptance_evidence": "test-evidence", "effect_checks": "explicit-main-agent-review", "risks": "explicit-main-agent-review"}}
    raw = {"task": desc(repo / projection_path), "completion": desc(artifacts / "completion.json"), "stdout": desc(artifacts / "stdout.json"),
        "stderr": desc(artifacts / "stderr.json"), "changed_file_snapshot": desc(artifacts / "snapshot.json"), "diff": desc(artifacts / "diff.patch"), "tests": [test]}
    packet = materialize_codex_main_task_evidence(repo, projection, raw_artifacts=raw, main_agent_attestation=attestation,
        scope={"changed_files": changed, "raw_caller_strings": raw_strings, "normalized": normalized, "three_way_reconciliation": reconciliation}, publication_id=str(uuid.uuid4()))
    return {"result": "PASS", "scope": "producer-subject-only-not-task-validation", "scope_review_confirmed": run_id,
            "evidence_packet": packet["publication"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts/gates/local_verify.py")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("command", choices=("certify-submit",))
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--confirm-scope-review", required=True)
    args = parser.parse_args(argv)
    try:
        result = certify_submit(args.repo_root, task_id=args.task_id, run_id=args.run_id, confirm_scope_review=args.confirm_scope_review)
    except LocalVerifyError as exc:
        result = {"result": exc.result, "reasons": [exc.code], "detail": exc.detail}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}[result["result"]]


if __name__ == "__main__":
    raise SystemExit(main())
