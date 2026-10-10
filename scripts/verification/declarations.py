"""封闭、独立地加载并校验 Module Check 声明。"""

from __future__ import annotations

import copy
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from scripts.verification.yaml_input import parse_yaml_input

_DECLARATION_SCHEMA = "lexiflow.module-checks.v2"
_DEFAULT_RESULT_CONTRACT = {
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
}


class DeclarationError(ValueError):
    """Module Check 声明无效时携带可定位的错误原因。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def _parse_declarations(raw: bytes) -> dict[str, Any]:
    try:
        data = parse_yaml_input(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DeclarationError("declarations-read-error", str(exc)) from None
    except yaml.YAMLError as exc:
        raise DeclarationError("declarations-parse-error", str(exc)) from None
    if not isinstance(data, dict):
        raise DeclarationError("declarations-shape", "root must be a mapping")
    if data.get("schema_version") != _DECLARATION_SCHEMA:
        raise DeclarationError("declarations-schema", f"expected {_DECLARATION_SCHEMA}")
    if set(data) != {"schema_version", "description", "executions", "checks"}:
        raise DeclarationError("declarations-shape", "unknown or missing root fields")
    executions, checks = data["executions"], data["checks"]
    if not isinstance(executions, dict) or not isinstance(checks, list):
        raise DeclarationError(
            "declarations-shape", "executions must be a mapping and checks a list"
        )
    allowed_execution = {
        "command",
        "timeout_seconds",
        "required_environment",
        "input_paths",
        "transaction_reuse",
        "result_contract",
    }
    normalized_executions: dict[str, dict[str, Any]] = {}
    for name, execution in executions.items():
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(execution, dict)
            or set(execution) - allowed_execution
        ):
            raise DeclarationError("declarations-shape", f"invalid execution {name!r}")
        if not {
            "command",
            "timeout_seconds",
            "required_environment",
            "input_paths",
        }.issubset(execution):
            raise DeclarationError(
                "declarations-shape", f"execution {name} missing required fields"
            )
        command = execution["command"]
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(x, str) or not x for x in command)
        ):
            raise DeclarationError(
                "declarations-shape", f"execution {name} command invalid"
            )
        timeout = execution["timeout_seconds"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise DeclarationError(
                "declarations-shape", f"execution {name} timeout invalid"
            )
        for field in ("required_environment", "input_paths"):
            value = execution[field]
            if not isinstance(value, list) or any(
                not isinstance(x, str) or not x for x in value
            ):
                raise DeclarationError(
                    "declarations-shape", f"execution {name} {field} invalid"
                )
        for path in execution["input_paths"]:
            _safe_relative(path, f"executions.{name}.input_paths")
        reuse = execution.get("transaction_reuse", False)
        if not isinstance(reuse, bool):
            raise DeclarationError(
                "declarations-shape", f"execution {name} transaction_reuse invalid"
            )
        kind = execution.get("result_contract", "json")
        if kind == "json":
            contract = _DEFAULT_RESULT_CONTRACT
        elif kind == "gradle-exit-code":
            contract = {
                "type": "exit-code",
                "completeness_guarantee": "后端 Gradle deliveryFull 要求 Java 测试非空且不跳过，并执行 architecture、source、schema、persistence、runtime-smoke 与 boot-jar 交付任务。",
                "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
                "pass_exit_code": 0,
            }
        elif kind == "runtime-phase-json":
            contract = {
                "type": "json-stdout",
                "required_fields": [
                    "status",
                    "phase",
                    "unit_tests",
                    "browser_smoke",
                    "reason",
                ],
                "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
                "minimum": {},
                "equals": {},
            }
        else:
            raise DeclarationError(
                "declarations-shape", f"execution {name} result contract unsupported"
            )
        normalized_executions[name] = {
            **execution,
            "transaction_reuse": reuse,
            "result_contract": contract,
        }
    seen: set[str] = set()
    referenced: set[str] = set()
    normalized_checks = []
    for i, check in enumerate(checks):
        if not isinstance(check, dict) or set(check) != {
            "check_id",
            "module",
            "scope",
            "execution",
            "triggers",
            "module_dependencies",
        }:
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] has unknown or missing fields"
            )
        check_id, execution_id = check["check_id"], check["execution"]
        if not isinstance(check_id, str) or not check_id:
            raise DeclarationError("declarations-shape", f"invalid check_id at {i}")
        if check_id in seen:
            raise DeclarationError("duplicate-check-id", f"invalid check_id at {i}")
        if (
            not isinstance(execution_id, str)
            or execution_id not in normalized_executions
        ):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] unknown execution"
            )
        seen.add(check_id)
        referenced.add(execution_id)
        if (
            not isinstance(check["module"], str)
            or not check["module"]
            or not isinstance(check["scope"], str)
            or check["scope"] not in {"change-targeted", "repository-baseline"}
        ):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] module or scope invalid"
            )
        if not isinstance(check["triggers"], list) or any(
            not isinstance(t, dict)
            or set(t) != {"path"}
            or not isinstance(t["path"], str)
            or not t["path"]
            for t in check["triggers"]
        ):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] triggers invalid"
            )
        if not isinstance(check["module_dependencies"], list) or any(
            not isinstance(x, str) or not x for x in check["module_dependencies"]
        ):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] dependencies invalid"
            )
        definition = normalized_executions[execution_id]
        if definition["transaction_reuse"]:
            from scripts.verification.kernel import is_release_runtime_child_check

            if is_release_runtime_child_check(
                {
                    "check_id": check_id,
                    "command": definition["command"],
                    "executable": definition["command"][0],
                }
            ):
                raise DeclarationError(
                    "declarations-shape",
                    f"checks[{i}] runtime transport cannot enable transaction reuse",
                )
        normalized_checks.append(
            {
                "check_id": check_id,
                "module": check["module"],
                "scope": check["scope"],
                "command": copy.deepcopy(definition["command"]),
                "executable": definition["command"][0],
                "cwd": ".",
                "timeout_seconds": definition["timeout_seconds"],
                "required_environment": copy.deepcopy(
                    definition["required_environment"]
                ),
                "input_paths": copy.deepcopy(definition["input_paths"]),
                "transaction_reuse": definition["transaction_reuse"],
                "result_contract": copy.deepcopy(definition["result_contract"]),
                "triggers": copy.deepcopy(check["triggers"]),
                "module_dependencies": copy.deepcopy(check["module_dependencies"]),
            }
        )
    if referenced != set(normalized_executions):
        raise DeclarationError(
            "declarations-shape", "unreferenced execution definitions"
        )
    return {
        "schema_version": _DECLARATION_SCHEMA,
        "description": data["description"],
        "checks": normalized_checks,
    }


def load_declarations_snapshot(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """仅解析一次声明字节流，并暴露该快照的不可变哈希。"""
    path = root / "harness" / "module-checks.yaml"
    if not path.is_file():
        raise DeclarationError(
            "declarations-missing", f"module declarations not found at {path}"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DeclarationError("declarations-read-error", str(exc)) from None
    data = _parse_declarations(raw)
    import hashlib

    return data, {
        "locator": "harness/module-checks.yaml",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def load_declarations(root: Path) -> dict[str, Any]:
    """为不执行 Verify run 的调用者加载声明。"""
    return load_declarations_snapshot(root)[0]


def _safe_relative(value: Any, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or ".." in PurePosixPath(value).parts
    ):
        raise DeclarationError(
            "declarations-shape", f"{field} must be a safe non-empty relative path"
        )


def filter_checks_by_scope(
    checks: list[dict[str, Any]], scope: str
) -> list[dict[str, Any]]:
    """按完整基线或变更范围筛选已声明 Check。"""
    return [check for check in checks if check["scope"] == scope]


def filter_checks_by_ids(
    checks: list[dict[str, Any]], check_ids: list[str] | None
) -> list[dict[str, Any]]:
    """仅供内部定向诊断选择精确 Check ID，不扩大正式证据。"""
    if not check_ids:
        return list(checks)
    wanted = set(check_ids)
    return [check for check in checks if check["check_id"] in wanted]
