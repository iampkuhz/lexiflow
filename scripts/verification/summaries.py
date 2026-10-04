"""将既有 Verification 证据压缩为定位摘要；不推断缺失的测试或权限事实。"""

from __future__ import annotations

from typing import Any


def selected_result(report: dict[str, Any]) -> str:
    """仅汇总已选诊断闭包；完整覆盖资格保留在原报告，不将未跑或缺项升为 PASS。"""
    from scripts.verification.kernel import aggregate_results

    if report.get("reason") != "partial-check-selection":
        return report.get("result", "FAIL")
    checks = report.get("checks", [])
    if (
        report.get("coverage_gaps")
        or len(checks) != report.get("scope_review", {}).get("checks_selected")
        or any(x.get("process", {}).get("exit_reason") == "not-run" for x in checks)
    ):
        return "BLOCKED"
    return aggregate_results(checks)[0]


def failure_layer(check: dict[str, Any]) -> str:
    """按明确机器原因分类失败；普通断言无法自动区分产品与夹具，保留待定位。"""
    reason = check.get("reason", "")
    if check.get("status") == "PASS":
        return "none"
    if reason in {
        "input-drift",
        "input-missing",
        "verify-context-invalid",
        "frozen-input-mismatch",
        "environment-drift",
        "risk-assessment-drift",
        "result-report-invalid",
        "result-report-missing-fields",
        "result-report-invalid-status",
        "result-report-incomplete",
    } or reason.endswith("-authority-invalid"):
        return "evidence"
    if reason in {"missing-environment", "ruff-unavailable", "pylint-unavailable"}:
        return "environment"
    if reason in {
        "spawn-error",
        "timeout",
        "cancelled",
        "budget-exhausted",
        "ruff-execution-unavailable",
        "pylint-execution-unavailable",
    }:
        return "executor"
    if reason in {"user-approval-missing", "approval-mismatch"}:
        return "authorization"
    if reason in {
        "python-quality-failed",
        "python-docstrings-failed",
        "documentation-governance-failed",
        "policy-projection-drift",
        "planning-catalog-invalid",
    }:
        return "static-check"
    if reason == "python-quality-incomplete":
        detail = check.get("result_contract", {}).get("report", {}).get("detail", {})
        if isinstance(detail, dict):
            layers = {
                failure_layer(item)
                for item in detail.values()
                if isinstance(item, dict) and item.get("status") != "PASS"
            }
            if len(layers) == 1:
                return layers.pop()
        return "check-needs-triage"
    if reason.startswith("prior-check-"):
        return "prerequisite"
    # 错误消息不是权限或业务归因依据；精确原因和日志交给一次根因定位。
    return "check-needs-triage"


def _locations(value: Any) -> list[dict[str, Any]]:
    # 仅提取结构化位置，不从原始 traceback 猜归属，也不复制模型或业务输出。
    found = []
    pending = [value]
    visited = 0
    while pending and len(found) < 20 and visited < 200:
        item = pending.pop()
        visited += 1
        if isinstance(item, dict):
            if isinstance(item.get("path"), str):
                found.append(
                    {
                        key: item[key]
                        for key in ("path", "line", "column", "message-id", "symbol")
                        if key in item
                    }
                )
            pending.extend(reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend(reversed(item[:200]))
    return found


def _next_action(layer: str) -> str:
    return {
        "none": "none",
        "evidence": "refreeze-after-source-or-evidence-repair",
        "environment": "restore-required-environment",
        "executor": "inspect-budget-process-ownership-and-executor",
        "authorization": "request-required-authorization",
        "static-check": "repair-reported-static-findings",
        "prerequisite": "resolve-first-failed-prerequisite",
    }.get(layer, "locate-root-cause-before-redispatch")


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    """汇总执行次数、耗时及失败 locator；别名复用不重复累计时间或测试数。"""
    checks = report.get("checks", [])
    commands = [
        item
        for item in checks
        if item.get("process", {}).get("exit_reason") not in {"not-run", "deduplicated"}
        and item.get("process", {}).get("executed_argv")
    ]
    findings = []
    for item in checks:
        if item.get("status") == "PASS":
            continue
        nested = item.get("result_contract", {}).get("report", {})
        detail = nested.get("detail", {})
        detail = detail if isinstance(detail, dict) else {}
        layer = failure_layer(item)
        findings.append(
            {
                "check_id": item.get("check_id"),
                "status": item.get("status"),
                "reason": item.get("reason", ""),
                "failure_layer": layer,
                "next_action": _next_action(layer),
                "automatic_redispatch": False,
                "artifacts": item.get("process", {}).get("output_artifacts", {}),
                "failed_tests": detail.get("failed_tests", nested.get("failed_tests")),
                "errored_tests": detail.get("error_tests", nested.get("errored_tests")),
                "locations": _locations(nested.get("detail")),
                "reported_counts": {
                    key: nested.get(key)
                    for key in ("checks_run", "failures", "errors", "skipped")
                },
            }
        )
    return {
        "checks_selected": len(checks),
        "commands_executed": len(commands),
        "checks_not_run": sum(
            x.get("process", {}).get("exit_reason") == "not-run" for x in checks
        ),
        "checks_reused": sum(
            x.get("process", {}).get("exit_reason") == "deduplicated" for x in checks
        ),
        "command_seconds": round(
            sum(x.get("process", {}).get("duration_seconds", 0) for x in commands), 6
        ),
        "findings": findings,
        "report_failure": (
            {
                "reason": report.get("reason"),
                "failure_layer": failure_layer(
                    {"status": report.get("result"), "reason": report.get("reason", "")}
                ),
            }
            if report.get("result") in {"FAIL", "BLOCKED"}
            and report.get("reason") != "partial-check-selection"
            else None
        ),
        "qualification": (
            {"result": "BLOCKED", "reason": "partial-check-selection"}
            if report.get("reason") == "partial-check-selection"
            else None
        ),
        "coverage_gaps": report.get("coverage_gaps", []),
        "token_usage": None,
    }
