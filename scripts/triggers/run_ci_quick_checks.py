"""规划或执行不具备正式验收资格的有界 CI quick 检查。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


from scripts.verification.planning.check_definitions import (
    REGISTRY_PATH,
    load_declarations_snapshot,
    filter_checks_by_scope,
)
from scripts.verification.execution.check_runner import (
    aggregate_results,
    snapshot_check_inputs,
)
from scripts.verification.planning.check_scope_selection import (
    changed_paths,
    resolve_base,
    resolve_module_dependencies,
    select_checks_for_changes,
    uncovered_changed_paths,
)

MODULES = REGISTRY_PATH


def _sha(path: Path) -> str:
    """计算文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _base(root: Path, requested: str | None) -> str:
    """核对显式基点为完整且可解析的 commit SHA。"""
    if requested is not None and not re.fullmatch(
        r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", requested
    ):
        raise ValueError("base must be a full 40- or 64-character commit SHA")
    base = requested or resolve_base(root)
    check = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base}^{{commit}}"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if check.returncode or check.stdout.strip().lower() != base.lower():
        raise ValueError("base does not resolve to the specified commit")
    return base


def _all_paths(root: Path) -> list[str]:
    """返回 Git 跟踪及未忽略未跟踪文件路径。"""
    from scripts.checks.repository.catalog.task_catalog_rules import (
        repository_source_inventory,
    )

    return repository_source_inventory(root)["candidate_paths"]


def make_plan(
    root: Path, base: str | None = None, *, all_paths: bool = False
) -> dict[str, Any]:
    """生成静态 CI quick 计划，不运行任何检查。"""
    root = root.resolve()
    if all_paths and base is not None:
        raise ValueError("--all and --base are mutually exclusive")
    declarations, module_snapshot = load_declarations_snapshot(root)
    profile = declarations["scenario_policy"]["ci_quick"]
    checks = declarations["checks"]
    by_id = {check["check_id"]: check for check in checks}
    required = profile["always_required_check_ids"]
    formal = set(profile["formal_only_check_ids"])
    unknown = (set(required) | formal) - set(by_id)
    if unknown:
        raise ValueError(f"unknown policy check IDs: {sorted(unknown)}")
    baseline = filter_checks_by_scope(checks, "repository-baseline")
    by_id = {check["check_id"]: check for check in baseline}
    if any(item not in by_id for item in set(required) | formal):
        raise ValueError("CI policy IDs must refer to baseline declarations")
    # CI 只接受 baseline 资格，并读取每项 Check 的唯一触发源；完整
    # baseline 执行仍只按资格，不把它的空触发视图改装成路径选择输入。
    view = [
        dict(check, scope="change-targeted", triggers=check["triggers"])
        for check in checks
        if "repository-baseline" in check["scenarios"]
    ]
    base_sha = "all" if all_paths else _base(root, base)
    changed = _all_paths(root) if all_paths else changed_paths(root, base_sha)
    chosen = select_checks_for_changes(view, changed)
    uncovered = uncovered_changed_paths(view, changed)
    if uncovered:
        raise ValueError(f"uncovered changed paths: {uncovered}")
    deferred = sorted(formal.intersection(check["check_id"] for check in chosen))
    direct = [check for check in chosen if check["check_id"] not in formal]
    selected = resolve_module_dependencies(
        [*direct, *(by_id[item] for item in required)], baseline
    )
    selected_ids = [check["check_id"] for check in selected]
    if formal.intersection(selected_ids):
        raise ValueError("dependency closure pulls formal-only checks into quick")
    if not selected_ids:
        raise ValueError("empty CI quick selection")
    from scripts.verification.execution.check_plan_runner import _runtime_checks

    selected = _runtime_checks(selected)
    return {
        "kind": "ci-quick-plan",
        "status": "PASS",
        "checks_executed": 0,
        "full_repository_executed": False,
        "formal_eligible": False,
        "base": base_sha,
        "changed_paths": changed,
        "selected_check_ids": selected_ids,
        "deferred_for_formal": deferred,
        "diagnostic": "plan only; no checks executed",
        "module_checks_sha256": module_snapshot["sha256"],
        "selected_checks": selected,
        "selected_input_snapshots": {
            check["check_id"]: snapshot_check_inputs(root, check) for check in selected
        },
    }


def run(
    root: Path, base: str | None = None, *, all_paths: bool = False
) -> dict[str, Any]:
    """执行自身冻结的 quick 计划，不提供可切换的运行模式。"""
    try:
        plan = make_plan(root, base, all_paths=all_paths)
    except Exception as exc:
        return {
            "kind": "ci-quick",
            "status": "FAIL",
            "checks_executed": 0,
            "full_repository_executed": False,
            "formal_eligible": False,
            "selected_check_ids": [],
            "deferred_for_formal": [],
            "changed_paths": [],
            "diagnostic": str(exc),
        }
    root = root.resolve()
    selected_ids = plan["selected_check_ids"]
    try:
        if _sha(root / MODULES) != plan["module_checks_sha256"]:
            raise ValueError("CI module declarations drifted after planning")
        import uuid
        from scripts.verification.execution.checkout_verification_window import (
            verification_window,
        )
        from scripts.verification.execution.check_plan_runner import (
            _execute,
            _verify_frozen_closure,
        )

        checks = plan["selected_checks"]
        frozen = plan["selected_input_snapshots"]
        if any(snapshot_check_inputs(root, c) != frozen[c["check_id"]] for c in checks):
            raise ValueError("selected check inputs drifted after planning")
        with verification_window(root) as window:
            results = _execute(
                root,
                checks,
                frozen,
                True,
                str(uuid.uuid4()),
                None,
                stop_on_blocked=False,
            )
            _verify_frozen_closure(root, checks, frozen, results)
            window.check(root)
        returned = [r.get("check_id") for r in results]
        if len(returned) != len(set(returned)) or set(returned) != set(selected_ids):
            raise ValueError("CI selected result coverage invalid")
        status, diagnostic = aggregate_results(results)
        result = {"kind": "ci-quick-execution", "selected_report": {"checks": results}}
        checks = results
        if _sha(root / MODULES) != plan["module_checks_sha256"]:
            status, diagnostic = (
                "FAIL",
                "CI module declarations drifted during execution",
            )
        else:
            current_by_id = {item["check_id"]: item for item in plan["selected_checks"]}
            if any(
                snapshot_check_inputs(root, current_by_id[item])
                != plan["selected_input_snapshots"][item]
                for item in selected_ids
            ):
                status, diagnostic = (
                    "FAIL",
                    "selected check inputs drifted during execution",
                )
        return {
            "kind": "ci-quick",
            "status": status if status in {"PASS", "BLOCKED", "FAIL"} else "FAIL",
            "checks_executed": len(checks),
            "full_repository_executed": False,
            "formal_eligible": False,
            "selected_check_ids": selected_ids,
            "deferred_for_formal": plan["deferred_for_formal"],
            "changed_paths": plan["changed_paths"],
            "diagnostic": diagnostic,
            "selected_diagnostic": result,
        }
    except Exception as exc:
        return {
            "kind": "ci-quick",
            "status": "FAIL",
            "checks_executed": 0,
            "full_repository_executed": False,
            "formal_eligible": False,
            "selected_check_ids": selected_ids,
            "deferred_for_formal": plan["deferred_for_formal"],
            "changed_paths": plan["changed_paths"],
            "diagnostic": str(exc),
        }


def public_summary(result: dict[str, Any], root: Path) -> dict[str, Any]:
    """只公开状态、计数与代码位置，禁止透传原始日志和任意报告正文。"""
    summary = {
        "kind": result["kind"],
        "status": result["status"],
        "formal_eligible": False,
        "full_repository_executed": False,
        "checks_executed": result.get("checks_executed", 0),
        "checks": [],
    }
    report = result.get("selected_diagnostic", {}).get("selected_report", {})
    for check in report.get("checks", []):
        process = check.get("process", {})
        item = {
            "check_id": check["check_id"],
            "status": check["status"],
            "exit_code": process.get("exit_code"),
            "timed_out": process.get("timed_out", False),
        }
        # reason 只消费机械类别，不输出动态错误正文。
        reason = check.get("reason", "")
        item["reason"] = (
            reason
            if reason
            in {
                "",
                "non-zero-exit",
                "input-drift",
                "result-report-invalid",
                "result-report-missing-fields",
                "result-report-invalid-status",
                "result-status-unexpected",
                "verification-module-tests-incomplete",
                "repository-module-tests-incomplete",
                "documentation-governance-failed",
                "python-quality-incomplete",
                "browser-api-smoke-assertion-failed",
                "docker-contract-tests-incomplete",
                "timeout",
                "environment-unavailable",
            }
            else "see-local-evidence"
        )
        contract = check.get("result_contract", {}).get("report", {})
        item["counts"] = {
            key: contract[key]
            for key in (
                "checks_run",
                "failures",
                "errors",
                "skipped",
                "unit_tests",
                "browser_smoke",
            )
            if isinstance(contract.get(key), int)
        }
        detail = contract.get("detail", {})
        item["test_ids"] = sorted(
            {
                value
                for key in ("failed_tests", "error_tests", "skipped_tests")
                for value in (detail.get(key, []) if isinstance(detail, dict) else [])
                if isinstance(value, str)
                and re.fullmatch(r"tests\.[A-Za-z_][A-Za-z0-9_.]{0,230}", value)
            }
        )
        locations, categories, tasks = set(), set(), set()
        if check["status"] != "PASS":
            for descriptor in process.get("output_artifacts", {}).values():
                locator = descriptor.get("locator", "")
                target = (root / locator).resolve()
                if not target.is_relative_to((root / "tmp/quality").resolve()):
                    continue
                try:
                    raw = target.read_bytes()
                except OSError:
                    continue
                if len(raw) > 4_000_000 or hashlib.sha256(
                    raw
                ).hexdigest() != descriptor.get("sha256"):
                    continue
                text = raw.decode("utf-8", errors="replace")
                locations.update(
                    re.findall(
                        r"(?:tests|scripts|backend|extension|ops)/[A-Za-z0-9_./-]+\.(?:py|mjs|java|kts):[0-9]+",
                        text,
                    )
                )
                # Python traceback 的行号与路径分离，只公开仓库代码定位。
                locations.update(
                    f"{filename}:{line}"
                    for filename, line in re.findall(
                        r'File "[^"\r\n]*?((?:tests|scripts|backend|extension|ops)/'
                        r'[A-Za-z0-9_./-]+\.py)", line ([0-9]+)',
                        text,
                    )
                    if ".." not in Path(filename).parts
                    and (root / filename).resolve().is_relative_to(root.resolve())
                    and (root / filename).is_file()
                )
                categories.update(
                    name
                    for name in (
                        "AssertionError",
                        "FileNotFoundError",
                        "PermissionError",
                        "TimeoutError",
                        "TypeError",
                        "ValueError",
                        "RuntimeError",
                        "CalledProcessError",
                        "SourceBridgeError",
                        "ModuleNotFoundError",
                    )
                    if re.search(r"\b" + name + r"\b", text)
                )
                tasks.update(
                    re.findall(
                        r"(?:Execution failed for task '|> Task )(:[A-Za-z0-9:_-]+)",
                        text,
                    )
                )
                for label, pattern in {
                    "missing-display": r"Missing X server|\$DISPLAY|without having a XServer",
                    "dependency-resolution": r"Could not resolve|Could not download",
                    "tls-failure": r"PKIX|SSLHandshake|certificate verify failed",
                    "java-compilation": r"Compilation failed|error: cannot find symbol",
                    "gradle-test-failure": r"There were failing tests",
                    "process-timeout": r"timed out|TimeoutExpired",
                }.items():
                    if re.search(pattern, text):
                        categories.add(label)
        item["source_locations"] = sorted(locations)[:80]
        item["error_categories"] = sorted(categories)[:40]
        item["gradle_tasks"] = sorted(tasks)[:40]
        summary["checks"].append(item)
    return summary


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    """解析 plan/quick 命令行并输出状态结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("plan", "quick"):
        item = sub.add_parser(command)
        group = item.add_mutually_exclusive_group()
        group.add_argument("--base")
        group.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    try:
        operation = make_plan if args.command == "plan" else run
        result = operation(
            root or Path(__file__).resolve().parents[2], args.base, all_paths=args.all
        )
    except (ValueError, OSError) as exc:
        result = {
            "kind": "ci-quick-plan",
            "status": "FAIL",
            "checks_executed": 0,
            "full_repository_executed": False,
            "formal_eligible": False,
            "selected_check_ids": [],
            "deferred_for_formal": [],
            "changed_paths": [],
            "diagnostic": str(exc),
        }
    # GitHub 日志仅输出白名单摘要；完整私有证据仍留本次 runner。
    output = (
        public_summary(result, root or Path(__file__).resolve().parents[2])
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else result
    )
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return {"PASS": 0, "BLOCKED": 2, "FAIL": 1}.get(result["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
