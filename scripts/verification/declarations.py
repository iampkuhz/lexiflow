"""Closed, independent module-check declaration loading and validation."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import yaml

_DECLARATION_SCHEMA = "lexiflow.module-checks.v1"
_REQUIRED_CHECK_FIELDS = frozenset({
    "check_id", "module", "command", "cwd", "timeout_seconds", "scope",
    "triggers", "module_dependencies", "required_environment", "result_contract",
    "input_paths",
})


class DeclarationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def _parse_declarations(raw: bytes) -> dict[str, Any]:
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DeclarationError("declarations-read-error", str(exc)) from None
    except yaml.YAMLError as exc:
        raise DeclarationError("declarations-parse-error", str(exc)) from None
    if not isinstance(data, dict):
        raise DeclarationError("declarations-shape", "root must be a mapping")
    if data.get("schema_version") != _DECLARATION_SCHEMA:
        raise DeclarationError("declarations-schema", f"expected {_DECLARATION_SCHEMA}")
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise DeclarationError("declarations-shape", "checks must be a list")
    _validate_checks(checks)
    return data


def load_declarations_snapshot(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Parse exactly one declaration byte stream and expose its immutable hash."""
    path = root / "harness" / "module-checks.yaml"
    if not path.is_file():
        raise DeclarationError("declarations-missing", f"module declarations not found at {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DeclarationError("declarations-read-error", str(exc)) from None
    data = _parse_declarations(raw)
    import hashlib
    return data, {"locator": "harness/module-checks.yaml", "sha256": hashlib.sha256(raw).hexdigest()}


def load_declarations(root: Path) -> dict[str, Any]:
    """Load declarations for callers that do not execute a verification run."""
    return load_declarations_snapshot(root)[0]


def _safe_relative(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value or value.startswith("/") or ".." in PurePosixPath(value).parts:
        raise DeclarationError("declarations-shape", f"{field} must be a safe non-empty relative path")


def _validate_contract(contract: Any, index: int) -> None:
    if not isinstance(contract, dict):
        raise DeclarationError("declarations-shape", f"checks[{index}].result_contract must be a mapping")
    kind = contract.get("type")
    if kind == "json-stdout":
        fields = contract.get("required_fields")
        statuses = contract.get("allowed_statuses")
        if not isinstance(fields, list) or not fields or not all(isinstance(x, str) and x for x in fields):
            raise DeclarationError("declarations-shape", f"checks[{index}].result_contract.required_fields required")
        if not isinstance(statuses, list) or not statuses or any(s not in {"PASS", "BLOCKED", "FAIL"} for s in statuses):
            raise DeclarationError("declarations-shape", f"checks[{index}].result_contract.allowed_statuses invalid")
        for key in ("minimum", "equals"):
            value = contract.get(key, {})
            if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, int) for k, v in value.items()):
                raise DeclarationError("declarations-shape", f"checks[{index}].result_contract.{key} invalid")
    elif kind == "exit-code":
        guarantee = contract.get("completeness_guarantee")
        if not isinstance(guarantee, str) or not guarantee.strip():
            raise DeclarationError("declarations-shape", f"checks[{index}] exit-code contract needs completeness_guarantee")
    else:
        raise DeclarationError("declarations-shape", f"checks[{index}].result_contract.type is unsupported")


def _validate_checks(checks: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            raise DeclarationError("declarations-shape", f"checks[{i}] must be a mapping")
        missing = _REQUIRED_CHECK_FIELDS - check.keys()
        if missing:
            raise DeclarationError("declarations-shape", f"checks[{i}] missing {sorted(missing)}")
        check_id = check["check_id"]
        if not isinstance(check_id, str) or not check_id or check_id in seen:
            raise DeclarationError("duplicate-check-id" if check_id in seen else "declarations-shape", f"invalid check_id at {i}")
        seen.add(check_id)
        if not isinstance(check["module"], str) or not check["module"]:
            raise DeclarationError("declarations-shape", f"checks[{i}].module invalid")
        command = check["command"]
        if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
            raise DeclarationError("declarations-shape", f"checks[{i}].command invalid")
        _safe_relative(check["cwd"], f"checks[{i}].cwd")
        if not isinstance(check["timeout_seconds"], int) or isinstance(check["timeout_seconds"], bool) or check["timeout_seconds"] <= 0:
            raise DeclarationError("declarations-shape", f"checks[{i}].timeout_seconds invalid")
        if check["scope"] not in {"change-targeted", "repository-baseline"}:
            raise DeclarationError("declarations-shape", f"checks[{i}].scope invalid")
        triggers = check["triggers"]
        if not isinstance(triggers, list) or any(not isinstance(t, dict) or not isinstance(t.get("path"), str) or not t["path"] for t in triggers):
            raise DeclarationError("declarations-shape", f"checks[{i}].triggers invalid")
        for field in ("module_dependencies", "required_environment", "input_paths"):
            value = check[field]
            if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
                raise DeclarationError("declarations-shape", f"checks[{i}].{field} invalid")
        for path in check["input_paths"]:
            _safe_relative(path, f"checks[{i}].input_paths")
        executable = check.get("executable")
        if executable is not None and (not isinstance(executable, str) or not executable):
            raise DeclarationError("declarations-shape", f"checks[{i}].executable invalid")
        if executable == "python3" and command[0] != "python3":
            raise DeclarationError("declarations-shape", f"checks[{i}] python executable requires python3 argv")
        _validate_contract(check["result_contract"], i)


def filter_checks_by_scope(checks: list[dict[str, Any]], scope: str) -> list[dict[str, Any]]:
    return [check for check in checks if check["scope"] == scope]


def filter_checks_by_ids(checks: list[dict[str, Any]], check_ids: list[str] | None) -> list[dict[str, Any]]:
    if not check_ids:
        return list(checks)
    wanted = set(check_ids)
    return [check for check in checks if check["check_id"] in wanted]
