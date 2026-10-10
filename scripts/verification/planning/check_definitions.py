"""封闭、独立地加载并校验 Module Check 声明。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from scripts.verification.planning.bounded_yaml_parser import parse_yaml_input

REGISTRY_PATH = "config/engineering/module-checks.yaml"
_DEFAULT_TIMEOUT_SECONDS = 180
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


def _validate_triggers(value: Any, context: str) -> None:
    if not isinstance(value, list):
        raise DeclarationError("declarations-shape", f"{context} triggers invalid")
    for trigger in value:
        if not isinstance(trigger, dict) or set(trigger) != {"path"}:
            raise DeclarationError("declarations-shape", f"{context} triggers invalid")
        _safe_relative(trigger["path"], f"{context}.triggers")


def _result_contract(kind: Any, index: int) -> dict[str, Any]:
    if kind == "json":
        return _DEFAULT_RESULT_CONTRACT
    if kind == "gradle-exit-code":
        return {
            "type": "exit-code",
            "completeness_guarantee": "后端 Gradle deliveryFull 要求 Java 测试非空且不跳过，并执行 architecture、source、schema、persistence、runtime-smoke 与 boot-jar 交付任务。",
            "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
            "pass_exit_code": 0,
        }
    if kind == "runtime-phase-json":
        return {
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
    raise DeclarationError(
        "declarations-shape", f"checks[{index}] result contract unsupported"
    )


def _parse_declarations(raw: bytes) -> dict[str, Any]:
    try:
        data = parse_yaml_input(raw)
    except UnicodeDecodeError as exc:
        raise DeclarationError("declarations-read-error", str(exc)) from None
    except yaml.YAMLError as exc:
        raise DeclarationError("declarations-parse-error", str(exc)) from None
    if not isinstance(data, dict):
        raise DeclarationError("declarations-shape", "root must be a mapping")
    if set(data) != {"checks", "scenario_policy"}:
        raise DeclarationError("declarations-shape", "unknown or missing root fields")
    checks = data["checks"]
    if not isinstance(checks, list):
        raise DeclarationError("declarations-shape", "checks must be a list")
    required_fields = {
        "check_id",
        "command",
        "input_paths",
        "scenarios",
    }
    optional_fields = {
        "timeout_seconds",
        "required_environment",
        "triggers",
        "transaction_reuse",
        "result_contract",
        "module_dependencies",
    }
    seen: set[str] = set()
    normalized_checks = []
    for i, check in enumerate(checks):
        if (
            not isinstance(check, dict)
            or not required_fields.issubset(check)
            or set(check) - required_fields - optional_fields
        ):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] has unknown or missing fields"
            )
        check_id = check["check_id"]
        if not isinstance(check_id, str) or not check_id:
            raise DeclarationError("declarations-shape", f"invalid check_id at {i}")
        if check_id in seen:
            raise DeclarationError("duplicate-check-id", f"invalid check_id at {i}")
        seen.add(check_id)
        command = check["command"]
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or not arg for arg in command)
        ):
            raise DeclarationError("declarations-shape", f"checks[{i}] command invalid")
        timeout = check.get("timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise DeclarationError("declarations-shape", f"checks[{i}] timeout invalid")
        for field in ("required_environment", "input_paths"):
            value = check.get(field, [])
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item for item in value
            ):
                raise DeclarationError(
                    "declarations-shape", f"checks[{i}] {field} invalid"
                )
        for path in check["input_paths"]:
            _safe_relative(path, f"checks[{i}].input_paths", directory_trigger=False)
        reuse = check.get("transaction_reuse", False)
        if not isinstance(reuse, bool):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] transaction_reuse invalid"
            )
        contract = _result_contract(check.get("result_contract", "json"), i)
        scenarios = check["scenarios"]
        if (
            not isinstance(scenarios, list)
            or not scenarios
            or any(not isinstance(scope, str) for scope in scenarios)
            or len(scenarios) != len(set(scenarios))
            or set(scenarios) - {"change-targeted", "repository-baseline"}
        ):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] scenarios invalid"
            )
        triggers = check.get(
            "triggers", [{"path": path} for path in check["input_paths"]]
        )
        _validate_triggers(triggers, f"checks[{i}]")
        # Registry 内容改变所有操作定义；这个固定消费者关系不逐项配置。
        if not any(trigger["path"] == REGISTRY_PATH for trigger in triggers):
            triggers = [*triggers, {"path": REGISTRY_PATH}]
        environment = check.get("required_environment", [])
        # 首个 argv 的已知 capability 由真实命令推导；子进程资源仍显式声明。
        if command[0] in {"python3", "node"}:
            environment = list(dict.fromkeys([command[0], *environment]))
        dependencies = check.get("module_dependencies", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(x, str) or not x for x in dependencies
        ):
            raise DeclarationError(
                "declarations-shape", f"checks[{i}] dependencies invalid"
            )
        if reuse:
            from scripts.verification.execution.check_runner import (
                is_release_runtime_child_check,
            )

            if is_release_runtime_child_check(
                {"check_id": check_id, "command": command, "executable": command[0]}
            ):
                raise DeclarationError(
                    "declarations-shape",
                    f"checks[{i}] runtime transport cannot enable transaction reuse",
                )
        normalized_checks.append(
            {
                "check_id": check_id,
                "scenarios": list(scenarios),
                "triggers": copy.deepcopy(triggers),
                "command": copy.deepcopy(check["command"]),
                "executable": check["command"][0],
                "cwd": ".",
                "timeout_seconds": timeout,
                "required_environment": copy.deepcopy(environment),
                "input_paths": copy.deepcopy(check["input_paths"]),
                "transaction_reuse": reuse,
                "result_contract": copy.deepcopy(contract),
                "module_dependencies": copy.deepcopy(dependencies),
            }
        )
    for check in normalized_checks:
        dependencies = check["module_dependencies"]
        if len(dependencies) != len(set(dependencies)) or set(dependencies) - seen:
            raise DeclarationError(
                "declarations-shape",
                f"invalid canonical dependencies for {check['check_id']}",
            )
    from scripts.verification.planning.check_scope_selection import (
        ScopeError,
        resolve_module_dependencies,
    )

    try:
        resolve_module_dependencies(normalized_checks, normalized_checks)
    except ScopeError as exc:
        raise DeclarationError(exc.code, str(exc)) from None
    policy = data["scenario_policy"]
    if not isinstance(policy, dict) or set(policy) != {"ci_quick"}:
        raise DeclarationError("declarations-shape", "invalid scenario_policy")
    quick = policy["ci_quick"]
    if not isinstance(quick, dict) or set(quick) != {
        "always_required_check_ids",
        "formal_only_check_ids",
    }:
        raise DeclarationError("declarations-shape", "invalid ci_quick policy")
    baseline = {
        c["check_id"]
        for c in normalized_checks
        if "repository-baseline" in c["scenarios"]
    }
    for field, identifiers in quick.items():
        if (
            not isinstance(identifiers, list)
            or any(not isinstance(v, str) for v in identifiers)
            or len(identifiers) != len(set(identifiers))
            or set(identifiers) - baseline
        ):
            raise DeclarationError("declarations-shape", f"invalid ci_quick.{field}")
    if set(quick["always_required_check_ids"]) & set(quick["formal_only_check_ids"]):
        raise DeclarationError("declarations-shape", "ci_quick required/formal overlap")
    return {
        "checks": normalized_checks,
        "scenario_policy": copy.deepcopy(policy),
    }


def load_declarations_snapshot(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """仅解析一次声明字节流，并暴露该快照的不可变哈希。"""
    path = root / REGISTRY_PATH
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
        "locator": REGISTRY_PATH,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def load_declarations(root: Path) -> dict[str, Any]:
    """为不执行 Verify run 的调用者加载声明。"""
    return load_declarations_snapshot(root)[0]


def _safe_relative(value: Any, field: str, *, directory_trigger: bool = True) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or any(
            part in {"", ".", ".."}
            for part in (value.rstrip("/") if directory_trigger else value).split("/")
        )
        or "\\" in value
        or any(token in value for token in ("*", "?", "[", "]"))
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise DeclarationError(
            "declarations-shape", f"{field} must be a safe non-empty relative path"
        )


def filter_checks_by_scope(
    checks: list[dict[str, Any]], scope: str
) -> list[dict[str, Any]]:
    """按完整基线或变更范围筛选已声明 Check。"""
    if scope not in {"repository-baseline", "change-targeted"}:
        raise DeclarationError("invalid-scenario", scope)
    return [
        {
            **copy.deepcopy(check),
            "scope": scope,
            "triggers": copy.deepcopy(check["triggers"])
            if scope == "change-targeted"
            else [],
        }
        for check in checks
        if scope in check["scenarios"]
    ]


def filter_checks_by_ids(
    checks: list[dict[str, Any]], check_ids: list[str] | None
) -> list[dict[str, Any]]:
    """仅供内部定向诊断选择精确 Check ID，不扩大正式证据。"""
    if not check_ids:
        return list(checks)
    wanted = set(check_ids)
    return [check for check in checks if check["check_id"] in wanted]
