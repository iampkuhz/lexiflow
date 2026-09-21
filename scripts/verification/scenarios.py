"""Public repository and change verification scenarios."""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from scripts.environment import check_for, execution_environment
from scripts.verification.declarations import DeclarationError, filter_checks_by_ids, filter_checks_by_scope, load_declarations_snapshot
from scripts.verification.kernel import REPORT_SCHEMA, aggregate_results, build_child_environment, compute_coverage_gap, execute_single_check, fingerprint_json, sha256_bytes, snapshot_check_inputs
from scripts.verification.scope import ScopeError, changed_paths, compute_scope_review, resolve_base, resolve_module_dependencies, select_checks_for_changes, uncovered_changed_paths


def _error_report(result: str, code: str, detail: str, run_id: str, *, scope: str) -> dict[str, Any]:
    return {"schema_version": REPORT_SCHEMA, "result": result, "reason": code, "detail": detail, "scope": scope, "run_id": run_id, "checks": [], "input_fingerprint": "", "configuration_fingerprint": "", "coverage_gaps": [], "scope_review": {"kind": "error", "checks_declared": 0, "checks_executed": 0}}


def _runtime_checks(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for declared in selected:
        check = dict(declared)
        check["input_paths"] = list(dict.fromkeys([*check.get("input_paths", []), "harness/module-checks.yaml"]))
        checks.append(check)
    return checks


def _freeze_selection(repo: Path, checks: list[dict[str, Any]], declaration_snapshot: dict[str, str]) -> tuple[dict[str, dict[str, Any]], bool]:
    """Freeze the entire input closure before any selected check may run."""
    frozen = {check["check_id"]: snapshot_check_inputs(repo, check) for check in checks}
    try:
        current_declaration = sha256_bytes((repo / declaration_snapshot["locator"]).read_bytes())
    except OSError:
        current_declaration = ""
    unchanged = current_declaration == declaration_snapshot["sha256"] and all(
        snapshot["fingerprint"] == snapshot_check_inputs(repo, check)["fingerprint"]
        for check, snapshot in ((check, frozen[check["check_id"]]) for check in checks)
    )
    return frozen, unchanged


def _not_run_input_drift(check: dict[str, Any], frozen: dict[str, Any]) -> dict[str, Any]:
    return {"check_id": check["check_id"], "module": check["module"], "status": "FAIL", "reason": "input-drift", "process": {"executed_argv": [], "exit_code": None, "exit_reason": "not-run", "duration_seconds": 0, "timed_out": False, "started_at": "", "finished_at": "", "output_artifacts": {}}, "input_snapshot": {"pre": frozen, "before_execution": {}, "post": {}}, "result_contract": {}}


def _execute(repo: Path, checks: list[dict[str, Any]], frozen: dict[str, dict[str, Any]], selection_unchanged: bool, run_id: str, runner: Callable[..., dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not selection_unchanged:
        return [_not_run_input_drift(check, frozen[check["check_id"]]) for check in checks]
    results: list[dict[str, Any]] = []
    # A Task may require a differently named declaration which is operationally
    # identical to a baseline declaration.  Execute that fixed command/input
    # closure once and project the result to the alias ID; do not reuse a prior
    # developer report or deduplicate merely by command text.
    equivalent: dict[str, dict[str, Any]] = {}
    for check in checks:
        execution_key = fingerprint_json({key: value for key, value in check.items() if key not in {
            "check_id", "module", "scope", "triggers", "selection_reasons"
        }})
        if execution_key in equivalent:
            original = equivalent[execution_key]
            projected = {
                **original,
                "check_id": check["check_id"],
                "module": check["module"],
                "reason": "equivalent-check-deduplicated" if original.get("status") == "PASS" else original.get("reason", "deduplicated-source-not-pass"),
                "process": {**original.get("process", {}), "executed_argv": [], "exit_reason": "deduplicated", "deduplicated_from": original["check_id"]},
                "input_snapshot": {"pre": frozen[check["check_id"]], "before_execution": original.get("input_snapshot", {}).get("before_execution", {}), "post": original.get("input_snapshot", {}).get("post", {})},
            }
            results.append(projected)
            continue
        environment = check_for(repo, check)
        if environment["status"] != "PASS":
            results.append({"check_id": check["check_id"], "module": check["module"], "status": "BLOCKED", "reason": "missing-environment", "process": {"executed_argv": [], "exit_code": None, "exit_reason": "not-run", "duration_seconds": 0, "timed_out": False, "started_at": "", "finished_at": "", "output_artifacts": {}}, "input_snapshot": {"pre": frozen[check["check_id"]], "before_execution": {}, "post": {}}, "environment": environment})
            equivalent[execution_key] = results[-1]
            continue
        child_environment = build_child_environment(
            runtime_environment=execution_environment(repo, check)
        )
        result = execute_single_check(check, repo, child_environment, runner=runner, run_id=run_id, frozen_pre=frozen[check["check_id"]])
        result["environment"] = environment
        results.append(result)
        equivalent[execution_key] = result
    return results


def _verify_frozen_closure(repo: Path, checks: list[dict[str, Any]], frozen: dict[str, dict[str, Any]], results: list[dict[str, Any]]) -> None:
    by_id = {result["check_id"]: result for result in results}
    for check in checks:
        current = snapshot_check_inputs(repo, check)
        result = by_id[check["check_id"]]
        result.setdefault("input_snapshot", {})["final"] = current
        if current["fingerprint"] != frozen[check["check_id"]]["fingerprint"]:
            result["status"] = "FAIL"
            result["reason"] = "input-drift"


def _report(*, run_id: str, scope: str, declaration_snapshot: dict[str, str], selected: list[dict[str, Any]], results: list[dict[str, Any]], coverage_gaps: list[str], reason_override: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    result, reason = aggregate_results(results)
    if coverage_gaps and (result == "PASS" or reason == "no-checks-executed"):
        result, reason = "BLOCKED", "coverage-gap"
    if reason_override and result != "FAIL":
        result, reason = "BLOCKED", reason_override
    executed = {item["check_id"] for item in results if item.get("process", {}).get("exit_reason") != "not-run"}
    configuration = {"declaration_sha256": declaration_snapshot["sha256"], "selected_checks": selected}
    config_fingerprint = fingerprint_json(configuration)
    snapshots = [{"check_id": item["check_id"], "pre": item.get("input_snapshot", {}).get("pre", {}), "before_execution": item.get("input_snapshot", {}).get("before_execution", {}), "post": item.get("input_snapshot", {}).get("post", {}), "final": item.get("input_snapshot", {}).get("final", {})} for item in results]
    report = {"schema_version": REPORT_SCHEMA, "result": result, "reason": reason, "scope": scope, "run_id": run_id, "checks": results, "configuration_fingerprint": config_fingerprint, "input_fingerprint": fingerprint_json({"configuration_fingerprint": config_fingerprint, "snapshots": snapshots}), "declaration_sha256": declaration_snapshot["sha256"], "coverage_gaps": coverage_gaps, "executed_check_ids": sorted(executed)}
    report.update(extra or {})
    return report


def _select_repository_checks(all_checks: list[dict[str, Any]], check_ids: list[str] | None, required_check_ids: tuple[str, ...]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Select baseline plus fixed extra IDs; no Task semantics enter this API."""
    baseline = filter_checks_by_scope(all_checks, "repository-baseline")
    by_id = {check["check_id"]: check for check in all_checks}
    unknown_required = sorted(set(required_check_ids) - set(by_id))
    if unknown_required: raise ScopeError("unknown-required-check-id", ", ".join(unknown_required))
    unknown_selected = sorted(set(check_ids or []) - {check["check_id"] for check in baseline})
    if unknown_selected: raise ScopeError("unknown-check-id", ", ".join(unknown_selected))
    selected_baseline = filter_checks_by_ids(baseline, check_ids)
    selected = [*selected_baseline]
    seen = {check["check_id"] for check in selected}
    for check_id in required_check_ids:
        if check_id not in seen:
            selected.append(by_id[check_id]); seen.add(check_id)
    if not selected: raise ScopeError("no-declared-checks", "no selected repository checks")
    closed = resolve_module_dependencies(selected, all_checks)
    return closed, baseline, bool(check_ids) and {check["check_id"] for check in selected_baseline} != {check["check_id"] for check in baseline}


def freeze_inputs(root: str | Path = ".", *, required_check_ids: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
    """Return the complete deterministic input closure for baseline plus fixed IDs."""
    repo = Path(root).resolve()
    try:
        declarations, declaration_snapshot = load_declarations_snapshot(repo)
        selected, _baseline, _partial = _select_repository_checks(declarations["checks"], None, tuple(required_check_ids))
    except (DeclarationError, ScopeError) as exc:
        return {"schema_version": "lexiflow.verification-freeze.v1", "result": "FAIL", "reason": exc.code, "detail": str(exc), "checks": [], "input_fingerprint": "", "declaration_sha256": ""}
    runtime_checks = _runtime_checks(selected)
    frozen, unchanged = _freeze_selection(repo, runtime_checks, declaration_snapshot)
    if not unchanged:
        return {"schema_version": "lexiflow.verification-freeze.v1", "result": "FAIL", "reason": "input-drift", "checks": runtime_checks, "input_fingerprint": "", "declaration_sha256": declaration_snapshot["sha256"]}
    return {"schema_version": "lexiflow.verification-freeze.v1", "result": "PASS", "required_check_ids": sorted(set(required_check_ids)), "checks": runtime_checks, "input_snapshots": frozen, "declaration_sha256": declaration_snapshot["sha256"], "input_fingerprint": fingerprint_json({"declaration_sha256": declaration_snapshot["sha256"], "checks": runtime_checks, "input_snapshots": frozen})}


def _validate_frozen_inputs(repo: Path, freeze: dict[str, Any], runtime_checks: list[dict[str, Any]], declaration_snapshot: dict[str, str], required_check_ids: tuple[str, ...]) -> dict[str, dict[str, Any]] | None:
    if not isinstance(freeze, dict): return None
    if freeze.get("schema_version") != "lexiflow.verification-freeze.v1" or freeze.get("result") != "PASS": return None
    expected = {check["check_id"]: check for check in runtime_checks}
    checks = freeze.get("checks")
    if not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks): return None
    supplied = {check.get("check_id"): check for check in checks}
    if None in supplied or len(supplied) != len(checks): return None
    if set(supplied) != set(expected) or any(supplied[key] != value for key,value in expected.items()): return None
    if freeze.get("required_check_ids") != sorted(set(required_check_ids)) or freeze.get("declaration_sha256") != declaration_snapshot["sha256"]: return None
    frozen = freeze.get("input_snapshots")
    if not isinstance(frozen, dict) or set(frozen) != set(expected): return None
    try:
        if freeze.get("input_fingerprint") != fingerprint_json({"declaration_sha256": declaration_snapshot["sha256"], "checks": runtime_checks, "input_snapshots": frozen}): return None
        if any(not isinstance(frozen[key], dict) or snapshot_check_inputs(repo, expected[key])["fingerprint"] != frozen[key].get("fingerprint") for key in expected): return None
    except (KeyError, TypeError, ValueError, OSError): return None
    return frozen

def verify_repository(root: str | Path = ".", *, check_ids: list[str] | None = None, required_check_ids: tuple[str, ...] | list[str] = (), frozen_inputs: dict[str, Any] | None = None, runner: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    repo, run_id = Path(root).resolve(), str(uuid.uuid4()); required = tuple(required_check_ids)
    try:
        declarations, declaration_snapshot = load_declarations_snapshot(repo)
        selected, baseline, partial = _select_repository_checks(declarations["checks"], check_ids, required)
    except (DeclarationError, ScopeError) as exc:
        return _error_report("FAIL", exc.code, str(exc), run_id, scope="repository-baseline")
    runtime_checks = _runtime_checks(selected)
    frozen = _validate_frozen_inputs(repo, frozen_inputs, runtime_checks, declaration_snapshot, required) if frozen_inputs is not None else None
    if frozen is None:
        if frozen_inputs is not None: return _error_report("FAIL", "frozen-input-mismatch", "supplied freeze is not current selected closure", run_id, scope="repository-baseline")
        frozen, unchanged = _freeze_selection(repo, runtime_checks, declaration_snapshot)
    else: unchanged = True
    results = _execute(repo, runtime_checks, frozen, unchanged, run_id, runner)
    _verify_frozen_closure(repo, runtime_checks, frozen, results)
    coverage = compute_coverage_gap(runtime_checks, {item["check_id"] for item in results if item.get("process", {}).get("exit_reason") != "not-run"})
    return _report(run_id=run_id, scope="repository-baseline", declaration_snapshot=declaration_snapshot, selected=runtime_checks, results=results, coverage_gaps=coverage, reason_override="partial-check-selection" if partial else "", extra={"required_check_ids": sorted(set(required)), "frozen_input_fingerprint": fingerprint_json({"declaration_sha256": declaration_snapshot["sha256"], "checks": runtime_checks, "input_snapshots": frozen}), "scope_review": {"kind": "full-repository" if not partial else "partial-repository", "checks_declared": len(baseline), "checks_selected": len(runtime_checks), "checks_executed": len([item for item in results if item.get("process", {}).get("exit_reason") != "not-run"])}})

def verify_changes(root: str | Path = ".", *, base: str | None = None, expected_paths: tuple[str, ...] | list[str] = (), runner: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    repo, run_id = Path(root).resolve(), str(uuid.uuid4())
    try:
        declarations, declaration_snapshot = load_declarations_snapshot(repo)
        resolved_base = resolve_base(repo, base)
        changed = changed_paths(repo, resolved_base)
    except (DeclarationError, ScopeError) as exc:
        return _error_report("FAIL", exc.code, str(exc), run_id, scope="change-targeted")
    all_checks = declarations["checks"]
    if changed:
        selected = select_checks_for_changes(all_checks, changed)
        uncovered = uncovered_changed_paths(all_checks, changed)
        coverage = (["no-check-matched"] if not selected else []) + [f"uncovered-path:{path}" for path in uncovered]
        review_kind = None
    else:
        selected, coverage, review_kind = list(all_checks), [], "no-changes"
    if not selected:
        return _report(run_id=run_id, scope="change-targeted", declaration_snapshot=declaration_snapshot, selected=[], results=[], coverage_gaps=coverage, extra={"base": resolved_base, "scope_review": compute_scope_review(changed, expected_paths, set(), len(all_checks))})
    try:
        selected = resolve_module_dependencies(selected, all_checks)
    except ScopeError as exc:
        return _error_report("FAIL", exc.code, str(exc), run_id, scope="change-targeted")
    runtime_checks = _runtime_checks(selected)
    frozen, unchanged = _freeze_selection(repo, runtime_checks, declaration_snapshot)
    results = _execute(repo, runtime_checks, frozen, unchanged, run_id, runner)
    _verify_frozen_closure(repo, runtime_checks, frozen, results)
    executed = {item["check_id"] for item in results if item.get("process", {}).get("exit_reason") != "not-run"}
    scope_review = compute_scope_review(changed, expected_paths, executed, len(all_checks))
    if review_kind:
        scope_review["kind"] = review_kind
        scope_review["advisories"].append("no changes detected; ran full declared check set")
    return _report(run_id=run_id, scope="change-targeted", declaration_snapshot=declaration_snapshot, selected=runtime_checks, results=results, coverage_gaps=coverage, extra={"base": resolved_base, "scope_review": scope_review})
