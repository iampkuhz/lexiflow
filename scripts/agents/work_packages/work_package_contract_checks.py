"""中立的 agent contract：身份安全的 locator、写入范围匹配和原始执行记录。"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

CODEX_TASK_PROJECTION_SCHEMA_VERSION = "lexiflow.codex-work-package-task-projection.v2"
REQUIRED_RESULT_FIELDS = (
    "status",
    "changed_files",
    "validation",
    "acceptance_evidence",
    "effect_checks",
    "risks",
)
RESULT_STATUSES = frozenset({"PASS", "BLOCKED", "FAIL"})
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


class AgentContractError(ValueError):
    """Agent 合同或路径范围无效时携带稳定拒绝代码。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def check_locator_safety_strict(locator: Any) -> None:
    """拒绝绝对、穿越和表达式路径，确保文件定位符只指向受控相对路径。"""
    if not isinstance(locator, str) or not locator:
        raise AgentContractError("LOCATOR_EMPTY", "locator is empty or not a string")
    if os.path.isabs(locator) or re.match(r"^[A-Za-z]:", locator):
        raise AgentContractError("LOCATOR_ABSOLUTE", "locator is absolute")
    if "\\" in locator or any(ord(c) < 0x20 or ord(c) == 0x7F for c in locator):
        raise AgentContractError(
            "LOCATOR_UNSAFE", "locator contains an unsafe character"
        )
    if unicodedata.normalize("NFC", locator) != locator or any(
        c in locator for c in (",", "*", "?", "[", "]", "{", "}")
    ):
        raise AgentContractError("LOCATOR_UNSAFE", "locator contains expression syntax")
    if any(
        part in ("", ".", "..") or part.lower() == "latest"
        for part in locator.split("/")
    ):
        raise AgentContractError("LOCATOR_UNSAFE", "locator has an unsafe segment")


def parse_dispatch_path_v1(pattern: Any) -> tuple[str, tuple[str, ...]]:
    """解析唯一严格派发路径语法，返回 exact、child 或 subtree 与路径段。"""
    if not isinstance(pattern, str) or not pattern:
        raise AgentContractError("PATH_EMPTY", "dispatch path is empty")
    if (
        pattern.startswith("/")
        or re.match(r"^[A-Za-z]:", pattern)
        or "\\" in pattern
        or "," in pattern
        or unicodedata.normalize("NFC", pattern) != pattern
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in pattern)
    ):
        raise AgentContractError("PATH_UNSAFE", "dispatch path is unsafe")
    if pattern.startswith("!") or any(
        c in pattern for c in ("$", "`", "~", "?", "[", "]", "{", "}")
    ):
        raise AgentContractError(
            "PATH_UNSAFE", "dispatch path contains expression syntax"
        )
    parts = pattern.split("/")
    for index, part in enumerate(parts):
        if not part or part in (".", "..") or part.lower() == "latest":
            raise AgentContractError(
                "PATH_UNSAFE", "dispatch path has an unsafe segment"
            )
        if "*" in part and part not in ("*", "**"):
            raise AgentContractError(
                "PATH_UNSAFE", "dispatch path has a partial wildcard"
            )
        if part in ("*", "**") and index != len(parts) - 1:
            raise AgentContractError("PATH_UNSAFE", "wildcard must be final")
    if parts[-1] == "*":
        return "child", tuple(parts[:-1])
    if parts[-1] == "**":
        return "subtree", tuple(parts[:-1])
    return "exact", tuple(parts)


def validate_dispatch_path_v1(pattern: Any) -> bool:
    """校验派发路径表达式只使用受支持的末尾通配形式。"""
    parse_dispatch_path_v1(pattern)
    return True


def match_dispatch_path_v1(path: Any, pattern: Any) -> bool:
    """判断一个精确文件路径是否被派发范围允许。"""
    pk, pp = parse_dispatch_path_v1(path)
    kind, prefix = parse_dispatch_path_v1(pattern)
    if pk != "exact":
        raise AgentContractError("PATH_NOT_EXACT", "matched path must be exact")
    if kind == "exact":
        return pp == prefix
    return pp[: len(prefix)] == prefix and (
        len(pp) == len(prefix) + 1 if kind == "child" else len(pp) > len(prefix)
    )


def dispatch_paths_intersect(left: Any, right: Any) -> bool:
    """判断两个严格派发范围是否可能共同授权同一精确文件。"""
    lk, lp = parse_dispatch_path_v1(left)
    rk, rp = parse_dispatch_path_v1(right)
    if lk == "exact":
        return match_dispatch_path_v1("/".join(lp), right)
    if rk == "exact":
        return match_dispatch_path_v1("/".join(rp), left)
    if lk == rk == "child":
        return lp == rp
    if lk == rk == "subtree":
        small, large = sorted((lp, rp), key=len)
        return large[: len(small)] == small
    subtree, child = (lp, rp) if lk == "subtree" else (rp, lp)
    return child[: len(subtree)] == subtree and len(child) >= len(subtree)


def normalize_scope_string(raw: Any) -> list[str]:
    """规范化声明范围，拒绝无法稳定比较的路径表达。"""
    if not isinstance(raw, str) or not raw.strip():
        raise AgentContractError("SCOPE_INVALID", "scope string must be non-empty")
    values = [item.strip() for item in raw.split(",")]
    if any(not item for item in values) or len(values) != len(set(values)):
        raise AgentContractError("SCOPE_INVALID", "scope items are invalid")
    for item in values:
        validate_dispatch_path_v1(item)
    return sorted(values)


def _safe_file(root: Path, locator: str) -> Path:
    check_locator_safety_strict(locator)
    path = root / locator
    try:
        path.relative_to(root)
    except ValueError:
        raise AgentContractError(
            "LOCATOR_UNSAFE", "locator escapes repository"
        ) from None
    current = root
    for part in locator.split("/"):
        current = current / part
        if current.is_symlink():
            raise AgentContractError("LOCATOR_UNSAFE", "locator has a symlink")
    if not path.is_file():
        raise AgentContractError("LOCATOR_UNREADABLE", "locator is not a regular file")
    return path


def sha256_file_strict(repo_root: str | Path, locator: str) -> str:
    """仅对受控普通文件计算哈希，拒绝符号链接及越界路径。"""
    return hashlib.sha256(
        _safe_file(Path(repo_root).resolve(), locator).read_bytes()
    ).hexdigest()


def validate_codex_work_package_task_projection(
    raw: Any, path: str = "raw task"
) -> None:
    """校验 Task 引用投影的精确字段；认证与父记录解析由发布器负责。"""
    fields = {
        "schema_version",
        "target_task_id",
        "frozen_caller_descriptor",
        "runtime_binding_descriptor",
    }
    if (
        not isinstance(raw, Mapping)
        or set(raw) != fields
        or raw["schema_version"] != CODEX_TASK_PROJECTION_SCHEMA_VERSION
        or not isinstance(raw["target_task_id"], str)
        or not _ID.fullmatch(raw["target_task_id"])
    ):
        raise AgentContractError("CONTRACT_INVALID", f"{path} fields are invalid")
    for field in ("frozen_caller_descriptor", "runtime_binding_descriptor"):
        value = raw[field]
        if (
            not isinstance(value, Mapping)
            or set(value) != {"locator", "sha256", "bytes"}
            or not isinstance(value["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or type(value["bytes"]) is not int
            or value["bytes"] < 1
        ):
            raise AgentContractError("CONTRACT_INVALID", f"{path}.{field} is invalid")
        check_locator_safety_strict(value["locator"])


def validate_all_result_fields(
    result_fields: Mapping[str, Any], artifact_locators: set[str]
) -> None:
    """要求结果的每个必填字段存在且结构合法，不能凭状态字串通过。"""
    if not isinstance(result_fields, Mapping) or set(result_fields) != set(
        REQUIRED_RESULT_FIELDS
    ):
        raise AgentContractError("RESULT_INVALID", "result fields are incomplete")
    if (
        not isinstance(result_fields["status"], str)
        or result_fields["status"] not in RESULT_STATUSES
    ):
        raise AgentContractError("RESULT_INVALID", "status is invalid")
    validation = result_fields["validation"]
    effects = result_fields["effect_checks"]
    evidence = result_fields["acceptance_evidence"]
    if (
        not isinstance(validation, Mapping)
        or not isinstance(validation.get("status"), str)
        or validation["status"] not in RESULT_STATUSES
        or not isinstance(validation.get("evidence_locator"), str)
        or validation["evidence_locator"] not in artifact_locators
        or not isinstance(effects, Mapping)
        or not effects
        or any(
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or value not in RESULT_STATUSES
            for key, value in effects.items()
        )
        or not isinstance(evidence, list)
        or not evidence
        or any(
            not isinstance(value, str) or value not in artifact_locators
            for value in evidence
        )
        or not isinstance(result_fields["risks"], list)
        or any(not isinstance(value, str) for value in result_fields["risks"])
    ):
        raise AgentContractError(
            "RESULT_INVALID", "validation or acceptance evidence is invalid"
        )
    for locator in artifact_locators:
        check_locator_safety_strict(locator)
