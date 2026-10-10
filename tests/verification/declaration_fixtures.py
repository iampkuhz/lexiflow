"""Serialize test-owned Check facts using the current flat declaration format."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def declaration_document(checks: list[dict[str, Any]]) -> dict[str, Any]:
    executions: dict[str, dict[str, Any]] = {}
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
        name = f"fixture-{index}"
        executions[name] = execution
        result.append(
            {
                "check_id": check.get("check_id", f"fixture.check-{index}"),
                "module": check.get("module", "fixture"),
                "scope": check.get("scope", "repository-baseline"),
                "execution": name,
                "triggers": check.get("triggers", [{"path": "src/"}]),
                "module_dependencies": check.get("module_dependencies", []),
            }
        )
    return {
        "schema_version": "lexiflow.module-checks.v2",
        "description": "test fixture",
        "executions": executions,
        "checks": result,
    }


def write_declarations(path: Path, checks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(declaration_document(checks), sort_keys=False), encoding="utf-8"
    )
