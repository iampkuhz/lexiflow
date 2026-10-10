"""Serialize test-owned Check facts as self-contained canonical Check declarations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def declaration_document(checks: list[dict[str, Any]]) -> dict[str, Any]:
    result: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        command = check.get("command", ["python3", "-c", "pass"])
        contract = check.get(
            "result_contract",
            {
                "type": "json-stdout",
                "required_fields": [
                    "status",
                    "checks_run",
                    "failures",
                    "errors",
                    "skipped",
                    "reason",
                ],
                "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
                "minimum": {"checks_run": 1},
                "equals": {"failures": 0, "errors": 0, "skipped": 0},
            },
        )
        kind = "json"
        if contract.get("type") == "exit-code" and any(
            "deliveryFull" in str(arg) for arg in command
        ):
            kind = "gradle-exit-code"
        elif contract.get("required_fields") == [
            "status",
            "phase",
            "unit_tests",
            "browser_smoke",
            "reason",
        ]:
            kind = "runtime-phase-json"
        execution = {
            "timeout_seconds": check.get("timeout_seconds", 60),
            "required_environment": check.get("required_environment", []),
            "input_paths": check.get("input_paths", []),
        }
        if "command" in check:
            execution["command"] = command
        if check.get("transaction_reuse", False):
            execution["transaction_reuse"] = True
        if kind != "json":
            execution["result_contract"] = kind
        if "transaction_reuse" in check and not isinstance(
            check["transaction_reuse"], bool
        ):
            execution["transaction_reuse"] = check["transaction_reuse"]
        result.append(
            {
                "check_id": check.get("check_id", f"fixture.check-{index}"),
                "scenarios": check.get("scenarios", [check.get("scope", "repository-baseline")]),
                **execution,
                "module_dependencies": check.get("module_dependencies", []),
            }
        )
        if "change-targeted" in result[-1]["scenarios"] or "triggers" in check:
            result[-1]["triggers"] = check.get("triggers", [{"path": "src/"}])
    return {
        "checks": result,
        "scenario_policy": {
            "ci_quick": {"always_required_check_ids": [], "formal_only_check_ids": []}
        },
    }


def write_declarations(
    path: Path, checks: list[dict[str, Any]], *, ci_quick: dict | None = None
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = declaration_document(checks)
    if ci_quick is not None:
        document["scenario_policy"]["ci_quick"] = ci_quick
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


def input_closure(root: Path, check: dict[str, Any]) -> set[str]:
    """核对真实递归输入，再返回源码成员与声明目录供直接消费者断言。"""
    from scripts.verification.planning.source_file_snapshot import snapshot_inputs

    files, missing = snapshot_inputs(root, check)
    if missing:
        raise AssertionError(f"missing declared input: {missing}")
    return set(check["input_paths"]) | {item["locator"] for item in files}


def trigger_covers(check: dict[str, Any], path: str) -> bool:
    """以真实场景前缀合同核对文件仍被 trigger 覆盖。"""
    return any(
        path == item["path"].rstrip("/")
        or path.startswith(item["path"].rstrip("/") + "/")
        for item in check["triggers"]
    )


def input_member(root: Path, path: str) -> str:
    """目录选择断言使用实际源码成员，不把目录名冒充 Git 变动文件。"""
    from scripts.verification.planning.source_file_snapshot import snapshot_inputs

    files, missing = snapshot_inputs(root, {"input_paths": [path]})
    if missing or not files:
        raise AssertionError(f"unavailable input member: {path}")
    return files[0]["locator"]
