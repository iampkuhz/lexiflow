"""仓库基线与变更范围的公开 Verification 场景。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts.environment import resolve_check_environment
from scripts.environment.toolchains.toolchain_identity import (
    ToolchainUnavailable,
    resolve_toolchain,
)
from scripts.verification.execution.checkout_verification_window import (
    VerificationWindow,
    WindowError,
    verification_window,
)
from scripts.verification.planning.check_definitions import (
    DeclarationError,
    REGISTRY_PATH,
    filter_checks_by_scope,
    load_declarations_snapshot,
)
from scripts.verification.execution.check_runner import (
    REPORT_SCHEMA,
    aggregate_results,
    build_child_environment,
    compute_coverage_gap,
    execute_single_check,
    fingerprint_json,
    operation_config,
    is_release_runtime_child_check,
    sha256_bytes,
    snapshot_check_inputs,
)
from scripts.verification.planning.check_scope_selection import (
    ScopeError,
    changed_paths,
    git_run,
    resolve_base,
    resolve_module_dependencies,
    select_checks_for_changes,
    uncovered_changed_paths,
)
from scripts.verification.execution.same_run_check_reuse import (
    VerificationTransaction,
    _begin,
)


def _error_report(
    result: str, code: str, detail: str, run_id: str, *, scope: str
) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "result": result,
        "reason": code,
        "detail": detail,
        "scope": scope,
        "run_id": run_id,
        "checks": [],
        "input_fingerprint": "",
        "configuration_fingerprint": "",
        "coverage_gaps": [],
        "scope_review": {"kind": "error", "checks_declared": 0, "checks_executed": 0},
    }


def _profile_scope(profile: dict[str, Any]) -> str:
    scope = profile.get("verification_scope") if isinstance(profile, dict) else None
    return (
        scope
        if scope
        in {"repository-baseline", "development-baseline", "development-change"}
        else "development-baseline"
    )


def _runtime_checks(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for declared in selected:
        check = dict(declared)
        check["input_paths"] = list(
            dict.fromkeys([*check.get("input_paths", []), REGISTRY_PATH])
        )
        checks.append(check)
    return checks


def _freeze_selection(
    repo: Path, checks: list[dict[str, Any]], declaration_snapshot: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], bool]:
    """在执行任何选中 Check 前冻结完整输入闭包；运行中不得重新选择。"""
    frozen = {check["check_id"]: snapshot_check_inputs(repo, check) for check in checks}
    try:
        current_declaration = sha256_bytes(
            (repo / declaration_snapshot["locator"]).read_bytes()
        )
    except OSError:
        current_declaration = ""
    unchanged = current_declaration == declaration_snapshot["sha256"] and all(
        snapshot["fingerprint"] == snapshot_check_inputs(repo, check)["fingerprint"]
        for check, snapshot in ((check, frozen[check["check_id"]]) for check in checks)
    )
    return frozen, unchanged


def _not_run_input_drift(
    check: dict[str, Any], frozen: dict[str, Any]
) -> dict[str, Any]:
    return {
        "check_id": check["check_id"],
        "status": "FAIL",
        "reason": "input-drift",
        "process": {
            "executed_argv": [],
            "exit_code": None,
            "exit_reason": "not-run",
            "duration_seconds": 0,
            "timed_out": False,
            "started_at": "",
            "finished_at": "",
            "output_artifacts": {},
        },
        "input_snapshot": {"pre": frozen, "before_execution": {}, "post": {}},
        "result_contract": {},
    }


def _not_run_after_stop(
    check: dict[str, Any], frozen: dict[str, Any], reason: str
) -> dict[str, Any]:
    """显式记录因交付 fail-fast 而未执行的选中 Check。"""
    return {
        "check_id": check["check_id"],
        "status": "BLOCKED",
        "reason": reason,
        "process": {
            "executed_argv": [],
            "exit_code": None,
            "exit_reason": "not-run",
            "duration_seconds": 0,
            "timed_out": False,
            "started_at": "",
            "finished_at": "",
            "output_artifacts": {},
        },
        "input_snapshot": {"pre": frozen, "before_execution": {}, "post": {}},
    }


def _execute(
    repo: Path,
    checks: list[dict[str, Any]],
    frozen: dict[str, dict[str, Any]],
    selection_unchanged: bool,
    run_id: str,
    progress_observer: Callable[[dict[str, Any]], None] | None,
    *,
    stop_on_blocked: bool = True,
    transaction: VerificationTransaction | None = None,
    view_scope: str = "",
    view_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按已冻结的 selection 顺序执行声明的 Check，并将输入哈希与执行结果配对。相同闭包仅在本次运行内复用，不能拿旧报告代替执行。"""
    if not selection_unchanged:
        return [
            _not_run_input_drift(check, frozen[check["check_id"]]) for check in checks
        ]
    results: list[dict[str, Any]] = []
    stopped_reason = ""
    for check in checks:
        if stopped_reason:
            results.append(
                _not_run_after_stop(check, frozen[check["check_id"]], stopped_reason)
            )
            continue
        if transaction is not None:
            transaction.check(repo, transaction.window)
        transport_active = is_release_runtime_child_check(check)
        # 每个视图都重新核对环境和输入；缓存仅能证明操作完全等价，不能
        # 跳过目标视图自己的闭包或环境新鲜度检查。
        current_snapshot = snapshot_check_inputs(repo, check)
        if current_snapshot != frozen[check["check_id"]]:
            results.append(_not_run_input_drift(check, frozen[check["check_id"]]))
            stopped_reason = "prior-check-failed"
            continue
        resolved_environment = resolve_check_environment(repo, check)
        environment = resolved_environment.diagnostic
        if environment["status"] != "PASS":
            results.append(
                {
                    "check_id": check["check_id"],
                    "status": "BLOCKED",
                    "reason": "missing-environment",
                    "process": {
                        "executed_argv": [],
                        "exit_code": None,
                        "exit_reason": "not-run",
                        "duration_seconds": 0,
                        "timed_out": False,
                        "started_at": "",
                        "finished_at": "",
                        "output_artifacts": {},
                    },
                    "input_snapshot": {
                        "pre": frozen[check["check_id"]],
                        "before_execution": {},
                        "post": {},
                    },
                    "environment": environment,
                }
            )
            if stop_on_blocked:
                stopped_reason = "prior-check-blocked"
            continue
        child_environment = build_child_environment(
            runtime_environment=resolved_environment.values
        )
        environment_fingerprint = fingerprint_json(
            {key: child_environment[key] for key in sorted(child_environment)}
        )
        transaction_reuse = check.get("transaction_reuse") is True
        toolchain = None
        if transaction_reuse:
            try:
                toolchain = resolve_toolchain(check, child_environment)
            except ToolchainUnavailable as exc:
                blocked = _not_run_after_stop(
                    check, frozen[check["check_id"]], "toolchain-unavailable"
                )
                blocked["detail"] = str(exc)
                results.append(blocked)
                if stop_on_blocked:
                    stopped_reason = "prior-check-blocked"
                continue
        operation = operation_config(check)
        operation_fingerprint = fingerprint_json(operation)
        execution_key = fingerprint_json(
            {
                "operation_fingerprint": operation_fingerprint,
                "input_files": frozen[check["check_id"]]["files"],
                "input_missing": frozen[check["check_id"]]["missing"],
                "environment_fingerprint": environment_fingerprint,
                "toolchain": toolchain,
                "transaction": transaction.nonce if transaction is not None else run_id,
            }
        )
        source = None
        if transaction is not None and transaction_reuse and not transport_active:
            source = transaction.executions.get(execution_key)
            if source is not None and source["run_id"] not in transaction.publications:
                source = None
        if source is not None and not transport_active:
            from scripts.verification.reports.verification_report_store import (
                read_report,
            )

            original_report, descriptor = read_report(repo, source["run_id"])
            if descriptor != transaction.publications[source["run_id"]]:
                raise ValueError("source-report-drift")
            original = next(
                item
                for item in original_report["checks"]
                if item["check_id"] == source["check_id"]
            )
            if original["process"]["exit_reason"] != "exited":
                raise ValueError("source-report-not-executed")
            projected = {
                **original,
                "check_id": check["check_id"],
                "reason": (
                    "equivalent-check-deduplicated"
                    if original.get("status") == "PASS"
                    else original.get("reason", "deduplicated-source-not-pass")
                ),
                "process": {
                    **original.get("process", {}),
                    "executed_argv": [],
                    "exit_reason": "deduplicated",
                    "deduplicated_from": original["check_id"],
                    "source_run_id": source["run_id"],
                },
                "input_snapshot": {
                    "pre": frozen[check["check_id"]],
                    "before_execution": original.get("input_snapshot", {}).get(
                        "before_execution", {}
                    ),
                    "post": original.get("input_snapshot", {}).get("post", {}),
                },
            }
            projected["cross_view_source"] = {
                "source_report": descriptor,
                "check_id": source["check_id"],
                "transaction_nonce": transaction.nonce,
            }
            results.append(projected)
            continue
        result = execute_single_check(
            check,
            repo,
            child_environment,
            progress_observer=progress_observer,
            run_id=run_id,
            frozen_pre=frozen[check["check_id"]],
        )
        result["environment"] = environment
        result["environment_fingerprint"] = environment_fingerprint
        if transaction_reuse:
            result["toolchain"] = toolchain
            try:
                if resolve_toolchain(check, child_environment) != toolchain:
                    result["status"] = "FAIL"
                    result["reason"] = "toolchain-drift"
            except ToolchainUnavailable:
                result["status"] = "FAIL"
                result["reason"] = "toolchain-drift"
        results.append(result)
        if not transport_active:
            if (
                transaction is not None
                and transaction_reuse
                and not transport_active
                and result.get("status") == "PASS"
                and result.get("process", {}).get("exit_reason") == "exited"
            ):
                transaction.check(repo, transaction.window)
                transaction.executions[execution_key] = {
                    "run_id": run_id,
                    "check_id": check["check_id"],
                }
        if stop_on_blocked and result.get("status") in {"FAIL", "BLOCKED"}:
            stopped_reason = (
                "prior-check-failed"
                if result.get("status") == "FAIL"
                else "prior-check-blocked"
            )
    return results


def _verify_frozen_closure(
    repo: Path,
    checks: list[dict[str, Any]],
    frozen: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
) -> None:
    by_id = {result["check_id"]: result for result in results}
    for check in checks:
        current = snapshot_check_inputs(repo, check)
        result = by_id[check["check_id"]]
        result.setdefault("input_snapshot", {})["final"] = current
        if current["fingerprint"] != frozen[check["check_id"]]["fingerprint"]:
            result["status"] = "FAIL"
            result["reason"] = "input-drift"
        if result.get("status") == "PASS":
            try:
                resolved = resolve_check_environment(repo, check)
                if resolved.diagnostic["status"] != "PASS":
                    result["status"] = "FAIL"
                    result["reason"] = "missing-environment"
                    continue
                child_environment = build_child_environment(
                    runtime_environment=resolved.values
                )
                environment_fingerprint = fingerprint_json(
                    {key: child_environment[key] for key in sorted(child_environment)}
                )
                if environment_fingerprint != result.get("environment_fingerprint"):
                    result["status"] = "FAIL"
                    result["reason"] = "environment-drift"
                elif check.get("transaction_reuse") is True and resolve_toolchain(
                    check, child_environment
                ) != result.get("toolchain"):
                    result["status"] = "FAIL"
                    result["reason"] = "toolchain-drift"
            except (ToolchainUnavailable, OSError, ValueError):
                result["status"] = "FAIL"
                result["reason"] = "toolchain-drift"


def _report(
    *,
    run_id: str,
    scope: str,
    declaration_snapshot: dict[str, str],
    selected: list[dict[str, Any]],
    results: list[dict[str, Any]],
    coverage_gaps: list[str],
    reason_override: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """将本次 Check 结果、覆盖缺口和输入指纹组装成 Task 中立报告；缺项不能升格为 PASS。"""
    result, reason = aggregate_results(results)
    if coverage_gaps and (result == "PASS" or reason == "no-checks-executed"):
        result, reason = "BLOCKED", "coverage-gap"
    if reason_override and result == "PASS":
        result, reason = "BLOCKED", reason_override
    executed = {
        item["check_id"]
        for item in results
        if item.get("process", {}).get("exit_reason") != "not-run"
    }
    configuration = {
        "declaration_sha256": declaration_snapshot["sha256"],
        "selected_checks": selected,
    }
    config_fingerprint = fingerprint_json(configuration)
    snapshots = [
        {
            "check_id": item["check_id"],
            "pre": item.get("input_snapshot", {}).get("pre", {}),
            "before_execution": item.get("input_snapshot", {}).get(
                "before_execution", {}
            ),
            "post": item.get("input_snapshot", {}).get("post", {}),
            "final": item.get("input_snapshot", {}).get("final", {}),
        }
        for item in results
    ]
    report = {
        "schema_version": REPORT_SCHEMA,
        "result": result,
        "reason": reason,
        "scope": scope,
        "run_id": run_id,
        "checks": results,
        "configuration_fingerprint": config_fingerprint,
        "input_fingerprint": fingerprint_json(
            {"configuration_fingerprint": config_fingerprint, "snapshots": snapshots}
        ),
        "declaration_sha256": declaration_snapshot["sha256"],
        "coverage_gaps": coverage_gaps,
        "executed_check_ids": sorted(executed),
    }
    report.update(extra or {})
    return report


def _select_repository_checks(
    all_checks: list[dict[str, Any]],
    required_check_ids: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """完整基线加明确依赖；不保留局部 Repository 资格转换能力。"""
    baseline = filter_checks_by_scope(all_checks, "repository-baseline")
    by_id = {check["check_id"]: check for check in all_checks}
    unknown = set(required_check_ids) - set(by_id)
    if unknown:
        raise ScopeError("unknown-required-check-id", ", ".join(sorted(unknown)))
    selected = list(baseline)
    seen = {c["check_id"] for c in selected}
    selected.extend(by_id[c] for c in required_check_ids if c not in seen)
    if not selected:
        raise ScopeError("no-declared-checks", "no repository baseline checks")
    return resolve_module_dependencies(selected, all_checks), baseline


def freeze_inputs(
    root: str | Path = ".",
    *,
    required_check_ids: tuple[str, ...] | list[str] = (),
    verification_scope: str = "repository-baseline",
    base: str | None = None,
    _subject_changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """冻结由公开范围及当前声明确定的输入闭包。"""
    repo = Path(root).resolve()
    scope = verification_scope
    resolved_base, changed = None, []
    try:
        if scope not in {
            "repository-baseline",
            "development-baseline",
            "development-change",
        }:
            raise ScopeError("invalid-verification-scope", scope)
        declarations, declaration_snapshot = load_declarations_snapshot(repo)
        all_checks = declarations["checks"]
        required = tuple(required_check_ids)
        if scope == "repository-baseline":
            selected, _baseline = _select_repository_checks(all_checks, required)
        else:
            formal = set(
                declarations["scenario_policy"]["ci_quick"]["formal_only_check_ids"]
            )
            if set(required) & formal:
                raise ScopeError(
                    "formal-only-required-check",
                    ", ".join(sorted(set(required) & formal)),
                )
            by_id = {x["check_id"]: x for x in all_checks}
            unknown = sorted(set(required) - set(by_id))
            if unknown:
                raise ScopeError("unknown-required-check-id", ", ".join(unknown))
            if scope == "development-baseline":
                selected = filter_checks_by_scope(all_checks, "repository-baseline")
            else:
                resolved_base = git_run(
                    repo,
                    "rev-parse",
                    "--verify",
                    f"{resolve_base(repo, base)}^{{commit}}",
                ).strip()
                changed = (
                    changed_paths(repo, resolved_base)
                    if _subject_changed_files is None
                    else list(_subject_changed_files)
                )
                if any(
                    not isinstance(path, str) or not path for path in changed
                ) or changed != sorted(set(changed)):
                    raise ScopeError(
                        "invalid-changed-subject",
                        "subject paths must be sorted unique strings",
                    )
                selected = (
                    select_checks_for_changes(all_checks, changed) if changed else []
                )
                if changed and (
                    not selected or uncovered_changed_paths(all_checks, changed)
                ):
                    raise ScopeError(
                        "coverage-gap",
                        ", ".join(uncovered_changed_paths(all_checks, changed)),
                    )
            selected = [x for x in selected if x["check_id"] not in formal]
            seen = {x["check_id"] for x in selected}
            selected.extend(by_id[x] for x in required if x not in seen)
            if not selected:
                raise ScopeError("no-declared-checks", "no selected checks")
            selected = resolve_module_dependencies(selected, all_checks)
            forbidden = sorted({x["check_id"] for x in selected} & formal)
            if forbidden:
                raise ScopeError("formal-only-dependency", ", ".join(forbidden))
    except (DeclarationError, ScopeError, ValueError) as exc:
        return {
            "schema_version": "lexiflow.verification-freeze.v2",
            "verification_scope": scope,
            "base": resolved_base,
            "changed_files": changed,
            "result": "FAIL",
            "required_check_ids": sorted(set(required_check_ids)),
            "reason": getattr(exc, "code", "profile-policy-invalid"),
            "detail": str(exc),
            "checks": [],
            "input_fingerprint": "",
            "declaration_sha256": "",
        }
    runtime_checks = _runtime_checks(selected)
    frozen, unchanged = _freeze_selection(repo, runtime_checks, declaration_snapshot)
    if any(snapshot["missing"] for snapshot in frozen.values()):
        return {
            "schema_version": "lexiflow.verification-freeze.v2",
            "verification_scope": scope,
            "base": resolved_base,
            "changed_files": changed,
            "result": "FAIL",
            "required_check_ids": sorted(set(required_check_ids)),
            "reason": "input-missing",
            "checks": runtime_checks,
            "input_snapshots": frozen,
            "input_fingerprint": "",
            "declaration_sha256": declaration_snapshot["sha256"],
        }
    if not unchanged:
        return {
            "schema_version": "lexiflow.verification-freeze.v2",
            "verification_scope": scope,
            "base": resolved_base,
            "changed_files": changed,
            "result": "FAIL",
            "required_check_ids": sorted(set(required_check_ids)),
            "reason": "input-drift",
            "checks": runtime_checks,
            "input_fingerprint": "",
            "declaration_sha256": declaration_snapshot["sha256"],
        }
    return {
        "schema_version": "lexiflow.verification-freeze.v2",
        "verification_scope": scope,
        "base": resolved_base,
        "changed_files": changed,
        "result": "PASS",
        "required_check_ids": sorted(set(required_check_ids)),
        "checks": runtime_checks,
        "input_snapshots": frozen,
        "declaration_sha256": declaration_snapshot["sha256"],
        "input_fingerprint": fingerprint_json(
            {
                "verification_scope": scope,
                "base": resolved_base,
                "changed_files": changed,
                "declaration_sha256": declaration_snapshot["sha256"],
                "checks": runtime_checks,
                "input_snapshots": frozen,
            }
        ),
    }


def _validate_frozen_inputs(
    repo: Path,
    freeze: dict[str, Any],
    runtime_checks: list[dict[str, Any]],
    declaration_snapshot: dict[str, str],
    required_check_ids: tuple[str, ...],
) -> dict[str, dict[str, Any]] | None:
    """在运行前核对调用者提供的冻结闭包与当前声明、输入及选中 Check；漂移时拒绝继续。"""
    if not isinstance(freeze, dict):
        return None
    if (
        freeze.get("schema_version") != "lexiflow.verification-freeze.v2"
        or freeze.get("result") != "PASS"
    ):
        return None
    expected = {check["check_id"]: check for check in runtime_checks}
    checks = freeze.get("checks")
    if not isinstance(checks, list) or any(
        not isinstance(check, dict) for check in checks
    ):
        return None
    supplied = {check.get("check_id"): check for check in checks}
    if None in supplied or len(supplied) != len(checks):
        return None
    if set(supplied) != set(expected) or any(
        supplied[key] != value for key, value in expected.items()
    ):
        return None
    if (
        freeze.get("required_check_ids") != sorted(set(required_check_ids))
        or freeze.get("declaration_sha256") != declaration_snapshot["sha256"]
    ):
        return None
    frozen = freeze.get("input_snapshots")
    if not isinstance(frozen, dict) or set(frozen) != set(expected):
        return None
    try:
        if freeze.get("input_fingerprint") != fingerprint_json(
            {
                "verification_scope": freeze.get("verification_scope"),
                "base": freeze.get("base"),
                "changed_files": freeze.get("changed_files"),
                "declaration_sha256": declaration_snapshot["sha256"],
                "checks": runtime_checks,
                "input_snapshots": frozen,
            }
        ):
            return None
        if any(
            not isinstance(frozen[key], dict)
            or snapshot_check_inputs(repo, expected[key])["fingerprint"]
            != frozen[key].get("fingerprint")
            for key in expected
        ):
            return None
    except (KeyError, TypeError, ValueError, OSError):
        return None
    return frozen


def _verify_repository(
    root: str | Path = ".",
    *,
    required_check_ids: tuple[str, ...] | list[str] = (),
    frozen_inputs: dict[str, Any] | None = None,
    progress_observer: Callable[[dict[str, Any]], None] | None = None,
    _transaction: VerificationTransaction | None = None,
) -> dict[str, Any]:
    """运行完整基线及显式要求的 Check；先冻结输入，再执行并发布本次报告。返回的是 Verification 结果，不是正式 validation receipt。"""
    repo, run_id = Path(root).resolve(), str(uuid.uuid4())
    required = tuple(required_check_ids)
    try:
        declarations, declaration_snapshot = load_declarations_snapshot(repo)
        selected, baseline = _select_repository_checks(declarations["checks"], required)
    except (DeclarationError, ScopeError) as exc:
        return _error_report(
            "FAIL", exc.code, str(exc), run_id, scope="repository-baseline"
        )
    runtime_checks = _runtime_checks(selected)
    frozen = (
        _validate_frozen_inputs(
            repo, frozen_inputs, runtime_checks, declaration_snapshot, required
        )
        if frozen_inputs is not None
        else None
    )
    if frozen is None:
        if frozen_inputs is not None:
            return _error_report(
                "FAIL",
                "frozen-input-mismatch",
                "supplied freeze is not current selected closure",
                run_id,
                scope="repository-baseline",
            )
        frozen, unchanged = _freeze_selection(
            repo, runtime_checks, declaration_snapshot
        )
    else:
        unchanged = True
    results = _execute(
        repo,
        runtime_checks,
        frozen,
        unchanged,
        run_id,
        progress_observer,
        stop_on_blocked=True,
        transaction=_transaction,
        view_scope="repository-baseline",
    )
    _verify_frozen_closure(repo, runtime_checks, frozen, results)
    coverage = compute_coverage_gap(
        runtime_checks,
        {
            item["check_id"]
            for item in results
            if item.get("process", {}).get("exit_reason") != "not-run"
        },
    )
    return _report(
        run_id=run_id,
        scope="repository-baseline",
        declaration_snapshot=declaration_snapshot,
        selected=runtime_checks,
        results=results,
        coverage_gaps=coverage,
        extra={
            "required_check_ids": sorted(set(required)),
            "frozen_input_fingerprint": fingerprint_json(
                {
                    "verification_scope": "repository-baseline",
                    "base": None,
                    "changed_files": [],
                    "declaration_sha256": declaration_snapshot["sha256"],
                    "checks": runtime_checks,
                    "input_snapshots": frozen,
                }
            ),
            "scope_review": {
                "kind": "full-repository",
                "checks_declared": len(baseline),
                "checks_selected": len(runtime_checks),
                "checks_executed": len(
                    [
                        item
                        for item in results
                        if item.get("process", {}).get("exit_reason") != "not-run"
                    ]
                ),
            },
        },
    )


def _within_window(root, scope, execute, window):
    try:
        if window is None:
            with verification_window(Path(root)):
                return execute()
        if type(window) is not VerificationWindow:
            raise WindowError("窗口必须来自当前调用实际取得的 verification_window")
        window.check(Path(root))
        report = execute()
        window.check(Path(root))
        return report
    except WindowError as exc:
        return _error_report(
            exc.status, exc.code, str(exc), str(uuid.uuid4()), scope=scope
        )


def verify_repository(
    root: str | Path = ".",
    *,
    required_check_ids: tuple[str, ...] | list[str] = (),
    frozen_inputs: dict[str, Any] | None = None,
    progress_observer: Callable[[dict[str, Any]], None] | None = None,
    _window: VerificationWindow | None = None,
) -> dict[str, Any]:
    """在同 checkout 串行窗口内运行基线；显式窗口仅供同一调用内的两个 Verify 视图共享。"""
    return _within_window(
        root,
        "repository-baseline",
        lambda: _verify_repository(
            root,
            required_check_ids=required_check_ids,
            frozen_inputs=frozen_inputs,
            progress_observer=progress_observer,
        ),
        _window,
    )


def verify_frozen_inputs(root: str | Path, freeze: dict[str, Any]) -> bool:
    """仅按冻结范围重新推导所选闭包，并核验当前声明与相关输入。"""
    if (
        not isinstance(freeze, dict)
        or freeze.get("schema_version") != "lexiflow.verification-freeze.v2"
        or freeze.get("result") != "PASS"
    ):
        return False
    scope = freeze.get("verification_scope")
    if scope not in {
        "repository-baseline",
        "development-baseline",
        "development-change",
    }:
        return False
    try:
        current = freeze_inputs(
            root,
            required_check_ids=freeze.get("required_check_ids", ()),
            verification_scope=scope,
            base=freeze.get("base"),
            _subject_changed_files=freeze.get("changed_files")
            if scope == "development-change"
            else None,
        )
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return current.get("result") == "PASS" and current == freeze


def verify_profile(
    root: str | Path = ".",
    *,
    frozen_inputs: dict[str, Any],
    progress_observer: Callable[[dict[str, Any]], None] | None = None,
    _window: VerificationWindow | None = None,
    _transaction: VerificationTransaction | None = None,
) -> dict[str, Any]:
    """执行已冻结的 profile；repository-baseline 始终保持完整基线语义。"""
    if _window is None:
        try:
            with verification_window(Path(root)) as window:
                return verify_profile(
                    root,
                    frozen_inputs=frozen_inputs,
                    progress_observer=progress_observer,
                    _window=window,
                    _transaction=_transaction,
                )
        except WindowError as exc:
            return _error_report(
                exc.status,
                exc.code,
                str(exc),
                str(uuid.uuid4()),
                scope="development-baseline",
            )
    try:
        if type(_window) is not VerificationWindow:
            raise WindowError("窗口必须来自当前调用实际取得的 verification_window")
        _window.check(Path(root))
        if _transaction is not None:
            _transaction.check(Path(root), _window)
    except WindowError as exc:
        return _error_report(
            exc.status,
            exc.code,
            str(exc),
            str(uuid.uuid4()),
            scope="development-baseline",
        )
    scope = (
        frozen_inputs.get("verification_scope")
        if isinstance(frozen_inputs, dict)
        else "development-baseline"
    )
    if scope not in {
        "repository-baseline",
        "development-baseline",
        "development-change",
    } or not verify_frozen_inputs(root, frozen_inputs):
        return _error_report(
            "FAIL",
            "frozen-input-mismatch",
            "profile freeze is missing or stale",
            str(uuid.uuid4()),
            scope=scope
            if scope in {"development-baseline", "development-change"}
            else "development-baseline",
        )
    if scope == "repository-baseline":
        report = _verify_repository(
            root,
            required_check_ids=frozen_inputs["required_check_ids"],
            frozen_inputs=frozen_inputs,
            progress_observer=progress_observer,
            _transaction=_transaction,
        )
        _window.check(Path(root))
        return report
    repo, run_id = Path(root).resolve(), str(uuid.uuid4())
    try:
        _declarations, declaration = load_declarations_snapshot(repo)
        selected = frozen_inputs["checks"]
        checks = _runtime_checks(selected)
        snapshots = frozen_inputs["input_snapshots"]
        unchanged = all(
            snapshot_check_inputs(repo, check)["fingerprint"]
            == snapshots[check["check_id"]]["fingerprint"]
            for check in checks
        )
        results = _execute(
            repo,
            checks,
            snapshots,
            unchanged,
            run_id,
            progress_observer,
            stop_on_blocked=True,
            transaction=_transaction,
            view_scope=scope,
            view_context={
                key: frozen_inputs[key]
                for key in (
                    "verification_scope",
                    "base",
                    "changed_files",
                    "required_check_ids",
                    "input_fingerprint",
                )
            },
        )
        _verify_frozen_closure(repo, checks, snapshots, results)
        coverage = compute_coverage_gap(
            checks,
            {
                x["check_id"]
                for x in results
                if x.get("process", {}).get("exit_reason") != "not-run"
            },
        )
        report = _report(
            run_id=run_id,
            scope=scope,
            declaration_snapshot=declaration,
            selected=checks,
            results=results,
            coverage_gaps=coverage,
            extra={
                "required_check_ids": frozen_inputs["required_check_ids"],
                "frozen_input_fingerprint": frozen_inputs["input_fingerprint"],
                "base": frozen_inputs["base"],
                "scope_review": {
                    "kind": scope,
                    "changed_files": frozen_inputs["changed_files"],
                    "checks_declared": len(checks),
                    "checks_selected": len(checks),
                    "checks_executed": sum(
                        x.get("process", {}).get("exit_reason") != "not-run"
                        for x in results
                    ),
                },
            },
        )
        _window.check(repo)
        return report
    except (DeclarationError, ScopeError, KeyError, TypeError, ValueError) as exc:
        return _error_report(
            "FAIL",
            getattr(exc, "code", "profile-invalid"),
            str(exc),
            run_id,
            scope=scope,
        )


def verify_profiles(
    root: str | Path = ".",
    *,
    frozen_profiles: list[dict[str, Any]],
    _window: VerificationWindow | None = None,
    before_profile: Callable[[int, dict[str, Any]], bool] | None = None,
    after_profile: Callable[[int, dict[str, Any], dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """在同一真实窗口与一次性内存台账内验证一到十六个冻结 profile。"""
    repo = Path(root).resolve()
    if not isinstance(frozen_profiles, list) or not 1 <= len(frozen_profiles) <= 16:
        raise ValueError("frozen_profiles must contain one to sixteen freeze inputs")
    scopes = [
        profile.get("verification_scope") if isinstance(profile, dict) else None
        for profile in frozen_profiles
    ]
    valid_single = len(scopes) == 1 and scopes[0] in {
        "repository-baseline",
        "development-baseline",
        "development-change",
    }
    valid_many = len(scopes) >= 2 and all(
        scope in {"development-baseline", "development-change", "repository-baseline"}
        for scope in scopes
    )
    if not (valid_single or valid_many):
        raise ValueError("profiles must contain one to sixteen valid scopes")
    if _window is None:
        try:
            with verification_window(repo) as window:
                return verify_profiles(
                    repo,
                    frozen_profiles=frozen_profiles,
                    _window=window,
                    before_profile=before_profile,
                    after_profile=after_profile,
                )
        except WindowError as exc:
            return [
                _error_report(
                    exc.status,
                    exc.code,
                    str(exc),
                    str(uuid.uuid4()),
                    scope=_profile_scope(profile),
                )
                for profile in frozen_profiles
            ]
    if type(_window) is not VerificationWindow:
        return [
            _error_report(
                "BLOCKED",
                "verification-window-unsafe",
                "窗口必须来自当前调用实际取得的 verification_window",
                str(uuid.uuid4()),
                scope=_profile_scope(profile),
            )
            for profile in frozen_profiles
        ]
    try:
        transaction = _begin(repo, _window)
    except WindowError as exc:
        return [
            _error_report(
                exc.status,
                exc.code,
                str(exc),
                str(uuid.uuid4()),
                scope=_profile_scope(profile),
            )
            for profile in frozen_profiles
        ]
    try:
        # 在任何昂贵 Check 启动前重验整个请求。执行后仍由每份 verify_profile
        # 再验证；这可避免明显陈旧的后续 view 发生无谓首轮副作用。
        stale = [not verify_frozen_inputs(repo, profile) for profile in frozen_profiles]
        if any(stale):
            return [
                _error_report(
                    "FAIL" if is_stale else "BLOCKED",
                    "frozen-input-mismatch"
                    if is_stale
                    else "transaction-preflight-blocked",
                    "profile freeze is stale"
                    if is_stale
                    else "another profile freeze is stale",
                    str(uuid.uuid4()),
                    scope=_profile_scope(profile),
                )
                for profile, is_stale in zip(frozen_profiles, stale, strict=True)
            ]
        reports = []
        for index, profile in enumerate(frozen_profiles):
            if before_profile is not None and not before_profile(index, profile):
                reports.append(
                    _error_report(
                        "FAIL",
                        "profile-preflight-changed",
                        "profile changed immediately before execution",
                        str(uuid.uuid4()),
                        scope=_profile_scope(profile),
                    )
                )
                break
            reports.append(
                verify_profile(
                    repo,
                    frozen_inputs=profile,
                    _window=_window,
                    _transaction=transaction,
                )
            )
            reports[-1]["verification_transaction"] = {
                "nonce": transaction.nonce,
                "profile_index": index,
            }
            if reports[-1].get("result") == "PASS":
                from scripts.verification.reports.verification_report_store import (
                    persist_report,
                )

                descriptor = persist_report(repo, reports[-1])
                transaction.publications[reports[-1]["run_id"]] = descriptor
                reports[-1]["publication"] = descriptor
            if after_profile is not None and not after_profile(
                index, profile, reports[-1]
            ):
                reports[-1] = {
                    **reports[-1],
                    "result": "FAIL",
                    "reason": "profile-postflight-changed",
                }
            if reports[-1].get("result") in {
                "FAIL",
                "BLOCKED",
            }:
                break
        if (before_profile is not None or after_profile is not None) and len(
            reports
        ) < len(frozen_profiles):
            for profile in frozen_profiles[len(reports) :]:
                reports.append(
                    _error_report(
                        "BLOCKED",
                        "prior-profile-not-run",
                        "earlier profile failed or was blocked",
                        str(uuid.uuid4()),
                        scope=_profile_scope(profile),
                    )
                )
        _window.check(repo)
        return reports
    except WindowError as exc:
        return [
            _error_report(
                exc.status,
                exc.code,
                str(exc),
                str(uuid.uuid4()),
                scope=_profile_scope(profile),
            )
            for profile in frozen_profiles
        ]
    finally:
        transaction.close()
