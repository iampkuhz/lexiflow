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

import yaml

from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.diagnose import diagnose
from scripts.verification.kernel import aggregate_results, snapshot_check_inputs
from scripts.verification.scope import (
    changed_paths,
    resolve_base,
    resolve_module_dependencies,
    select_checks_for_changes,
    uncovered_changed_paths,
)

POLICY = "harness/ci-policy.yaml"
MODULES = "harness/module-checks.yaml"


def _sha(path: Path) -> str:
    """计算文件 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _policy(root: Path) -> tuple[dict[str, Any], str]:
    """读取并严格校验 quick 配置字段。"""
    raw = (root / POLICY).read_bytes()
    data = yaml.safe_load(raw)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != "lexiflow.ci-quick.v1"
    ):
        raise ValueError("missing or invalid CI quick profile")
    allowed = {"schema_version", "always_required_check_ids", "formal_only_check_ids"}
    if set(data) != allowed:
        raise ValueError("unknown CI quick profile fields")
    for key in ("always_required_check_ids", "formal_only_check_ids"):
        value = data.get(key)
        if not isinstance(value, list) or not all(
            isinstance(v, str) and v for v in value
        ):
            raise ValueError(f"invalid {key}")
        if len(value) != len(set(value)):
            raise ValueError(f"duplicate IDs in {key}")
    if set(data["always_required_check_ids"]) & set(data["formal_only_check_ids"]):
        raise ValueError("always-required checks cannot be formal-only")
    return data, hashlib.sha256(raw).hexdigest()


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
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )
    if result.returncode:
        raise ValueError("cannot enumerate Git-visible paths")
    return sorted(set(path for path in result.stdout.split("\0") if path))


def make_plan(
    root: Path, base: str | None = None, *, all_paths: bool = False
) -> dict[str, Any]:
    """生成静态 CI quick 计划，不运行任何检查。"""
    root = root.resolve()
    if all_paths and base is not None:
        raise ValueError("--all and --base are mutually exclusive")
    profile, profile_hash = _policy(root)
    declarations, module_snapshot = load_declarations_snapshot(root)
    checks = declarations["checks"]
    by_id = {check["check_id"]: check for check in checks}
    required = profile["always_required_check_ids"]
    formal = set(profile["formal_only_check_ids"])
    unknown = (set(required) | formal) - set(by_id)
    if unknown:
        raise ValueError(f"unknown policy check IDs: {sorted(unknown)}")
    baseline = [check for check in checks if check["scope"] == "repository-baseline"]
    if any(
        by_id[item]["scope"] != "repository-baseline" for item in set(required) | formal
    ):
        raise ValueError("CI policy IDs must refer to baseline declarations")
    # baseline check 的 trigger 是通用选择入口；只改 scope 供共享选择器接受。
    view = [dict(check, scope="change-targeted") for check in baseline]
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
        "profile_sha256": profile_hash,
        "module_checks_sha256": module_snapshot["sha256"],
        "selected_input_snapshots": {
            check["check_id"]: snapshot_check_inputs(root, by_id[check["check_id"]])
            for check in selected
        },
    }


def run(
    root: Path, base: str | None = None, *, execute: bool, all_paths: bool = False
) -> dict[str, Any]:
    """计划或执行 quick，并保留底层诊断且不提升其身份。"""
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
    if not execute:
        return plan
    root = root.resolve()
    selected_ids = plan["selected_check_ids"]
    try:
        if (
            _sha(root / POLICY) != plan["profile_sha256"]
            or _sha(root / MODULES) != plan["module_checks_sha256"]
        ):
            raise ValueError("CI profile or module declarations drifted after planning")
        declarations, _ = load_declarations_snapshot(root)
        by_id = {item["check_id"]: item for item in declarations["checks"]}
        if any(
            snapshot_check_inputs(root, by_id[item])
            != plan["selected_input_snapshots"][item]
            for item in selected_ids
        ):
            raise ValueError("selected check inputs drifted after planning")
        result = diagnose(root, "repository", check_ids=tuple(selected_ids))
        report = result.get("selected_report", {})
        checks = report.get("checks", [])
        returned = [item.get("check_id") for item in checks]
        if (
            len(returned) != len(set(returned))
            or set(returned) != set(selected_ids)
            or report.get("coverage_gaps")
            or set(report.get("executed_check_ids", [])) != set(selected_ids)
        ):
            status, diagnostic = (
                "FAIL",
                "selected checks missing, duplicate, extra, or incomplete",
            )
        else:
            status, diagnostic = aggregate_results(checks)
            reason = report.get("reason", "")
            selected_result = result.get("selected_result")
            if (
                status == "PASS"
                and selected_result in {"BLOCKED", "FAIL"}
                and reason != "partial-check-selection"
            ):
                status, diagnostic = (
                    selected_result,
                    reason or "underlying diagnostic did not PASS",
                )
            elif status == "PASS" and reason not in {"", "partial-check-selection"}:
                status, diagnostic = "BLOCKED", reason
            elif status == "PASS" and reason == "partial-check-selection":
                diagnostic = "selected checks PASS; underlying repository diagnostic remains BLOCKED (partial-check-selection)"
        if (
            _sha(root / POLICY) != plan["profile_sha256"]
            or _sha(root / MODULES) != plan["module_checks_sha256"]
        ):
            status, diagnostic = (
                "FAIL",
                "CI profile or module declarations drifted during execution",
            )
        else:
            current, _ = load_declarations_snapshot(root)
            current_by_id = {item["check_id"]: item for item in current["checks"]}
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
            if re.fullmatch(r"[a-z][a-z0-9-]{0,100}", reason)
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
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,240}", value)
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
                categories.update(
                    re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:Error|Exception)\b", text)
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
    result = run(
        root or Path(__file__).resolve().parents[2],
        args.base,
        execute=args.command == "quick",
        all_paths=args.all,
    )
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
