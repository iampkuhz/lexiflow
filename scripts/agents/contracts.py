"""Neutral agent contracts: identity-safe locators, scope matching and raw execution records.

This module intentionally has no Gate import.  Formal acceptance adapts the immutable
agent records from its own side; agent execution never materializes Gate evidence.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import unicodedata
from pathlib import Path
from typing import Any, Mapping

CODEX_TASK_PROJECTION_SCHEMA_VERSION = "lexiflow.codex-work-package-task-projection.v1"
CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION = "lexiflow.codex-main-task-projection.v1"
REQUIRED_RESULT_FIELDS = ("status", "changed_files", "validation", "acceptance_evidence", "effect_checks", "risks")
RESULT_STATUSES = frozenset({"PASS", "BLOCKED", "FAIL"})
_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")

class AgentContractError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")

def check_locator_safety_strict(locator: Any) -> None:
    if not isinstance(locator, str) or not locator:
        raise AgentContractError("LOCATOR_EMPTY", "locator is empty or not a string")
    if os.path.isabs(locator) or re.match(r"^[A-Za-z]:", locator):
        raise AgentContractError("LOCATOR_ABSOLUTE", "locator is absolute")
    if "\\" in locator or any(ord(c) < 0x20 or ord(c) == 0x7f for c in locator):
        raise AgentContractError("LOCATOR_UNSAFE", "locator contains an unsafe character")
    if unicodedata.normalize("NFC", locator) != locator or any(c in locator for c in (",", "*", "?", "[", "]", "{", "}")):
        raise AgentContractError("LOCATOR_UNSAFE", "locator contains expression syntax")
    if any(part in ("", ".", "..") or part.lower() == "latest" for part in locator.split("/")):
        raise AgentContractError("LOCATOR_UNSAFE", "locator has an unsafe segment")

def _parse_dispatch_path_v1(pattern: Any) -> tuple[str, tuple[str, ...]]:
    if not isinstance(pattern, str) or not pattern:
        raise AgentContractError("PATH_EMPTY", "dispatch path is empty")
    if pattern.startswith("/") or re.match(r"^[A-Za-z]:", pattern) or "\\" in pattern or "," in pattern:
        raise AgentContractError("PATH_UNSAFE", "dispatch path is unsafe")
    if pattern.startswith("!") or any(c in pattern for c in ("$", "`", "~", "?", "[", "]", "{", "}")):
        raise AgentContractError("PATH_UNSAFE", "dispatch path contains expression syntax")
    parts = pattern.split("/")
    for index, part in enumerate(parts):
        if not part or part in (".", "..") or part.lower() == "latest":
            raise AgentContractError("PATH_UNSAFE", "dispatch path has an unsafe segment")
        if "*" in part and part not in ("*", "**"):
            raise AgentContractError("PATH_UNSAFE", "dispatch path has a partial wildcard")
        if part in ("*", "**") and index != len(parts) - 1:
            raise AgentContractError("PATH_UNSAFE", "wildcard must be final")
    if parts[-1] == "*": return "child", tuple(parts[:-1])
    if parts[-1] == "**": return "subtree", tuple(parts[:-1])
    return "exact", tuple(parts)

def validate_dispatch_path_v1(pattern: Any) -> bool:
    _parse_dispatch_path_v1(pattern); return True

def match_dispatch_path_v1(path: Any, pattern: Any) -> bool:
    pk, pp = _parse_dispatch_path_v1(path); kind, prefix = _parse_dispatch_path_v1(pattern)
    if pk != "exact": raise AgentContractError("PATH_NOT_EXACT", "matched path must be exact")
    if kind == "exact": return pp == prefix
    return pp[:len(prefix)] == prefix and (len(pp) == len(prefix) + 1 if kind == "child" else len(pp) > len(prefix))

def dispatch_path_contains(container: Any, member: Any) -> bool:
    ck, cp = _parse_dispatch_path_v1(container); mk, mp = _parse_dispatch_path_v1(member)
    if ck == "exact": return mk == "exact" and cp == mp
    if ck == "child":
        return (mk == "exact" and mp[:len(cp)] == cp and len(mp) == len(cp)+1) or (mk == "child" and mp == cp)
    return mp[:len(cp)] == cp and (len(mp) > len(cp) if mk == "exact" else len(mp) >= len(cp))

def dispatch_paths_intersect(left: Any, right: Any) -> bool:
    lk, lp = _parse_dispatch_path_v1(left); rk, rp = _parse_dispatch_path_v1(right)
    if lk == "exact": return match_dispatch_path_v1("/".join(lp), right)
    if rk == "exact": return match_dispatch_path_v1("/".join(rp), left)
    if lk == rk == "child": return lp == rp
    if lk == rk == "subtree":
        small, large = sorted((lp, rp), key=len); return large[:len(small)] == small
    subtree, child = (lp, rp) if lk == "subtree" else (rp, lp)
    return child[:len(subtree)] == subtree and len(child) >= len(subtree)

def normalize_scope_string(raw: Any) -> list[str]:
    if not isinstance(raw, str) or not raw.strip(): raise AgentContractError("SCOPE_INVALID", "scope string must be non-empty")
    values = [item.strip() for item in raw.split(",")]
    if any(not item for item in values) or len(values) != len(set(values)): raise AgentContractError("SCOPE_INVALID", "scope items are invalid")
    for item in values: validate_dispatch_path_v1(item)
    return sorted(values)

def _safe_file(root: Path, locator: str) -> Path:
    check_locator_safety_strict(locator)
    path = root / locator
    try: path.relative_to(root)
    except ValueError: raise AgentContractError("LOCATOR_UNSAFE", "locator escapes repository") from None
    current=root
    for part in locator.split("/"):
        current=current/part
        if current.is_symlink(): raise AgentContractError("LOCATOR_UNSAFE", "locator has a symlink")
    if not path.is_file(): raise AgentContractError("LOCATOR_UNREADABLE", "locator is not a regular file")
    return path

def sha256_file_strict(repo_root: str | Path, locator: str) -> str:
    return hashlib.sha256(_safe_file(Path(repo_root).resolve(), locator).read_bytes()).hexdigest()

def _text(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip(): raise AgentContractError("CONTRACT_INVALID", f"{path} must be non-empty")

def _identity(raw: Mapping[str, Any], path: str) -> None:
    for field in ("parent_session_id", "agent_id", "run_id", "session_id", "client", "parent_client"):_text(raw.get(field), f"{path}.{field}")
    if raw["client"] != "codex" or raw["parent_client"] != "codex": raise AgentContractError("CONTRACT_INVALID", "Codex projection identity is invalid")

def validate_codex_work_package_task_projection(raw: Any, path: str="raw task") -> None:
    if not isinstance(raw, Mapping): raise AgentContractError("CONTRACT_INVALID", f"{path} must be an object")
    required={"schema_version","work_package_id","task_ids","target_task_id","task_id","task_source","task_version","change_version","allowed_files","forbidden_files","expected_output","acceptance_criteria","acceptance_evidence","validation_command","caller_contract","parent_session_id","agent_id","run_id","session_id","client","parent_client"}
    if set(raw) != required or raw["schema_version"] != CODEX_TASK_PROJECTION_SCHEMA_VERSION: raise AgentContractError("CONTRACT_INVALID", f"{path} fields are invalid")
    ids=raw["task_ids"]
    if not isinstance(ids,list) or len(ids)<2 or len(ids)!=len(set(ids)) or any(not isinstance(x,str) or not _ID.fullmatch(x) for x in ids) or raw["target_task_id"] not in ids or raw["task_id"] != raw["target_task_id"]: raise AgentContractError("CONTRACT_INVALID", "task identity is invalid")
    _identity(raw,path); normalize_scope_string(raw["allowed_files"]); normalize_scope_string(raw["forbidden_files"])
    for field in ("task_source","expected_output","validation_command"): _text(raw[field],f"{path}.{field}")
    for field in ("acceptance_criteria","acceptance_evidence"):
        if not isinstance(raw[field],list) or not raw[field] or not all(isinstance(x,str) and x.strip() for x in raw[field]): raise AgentContractError("CONTRACT_INVALID",f"{path}.{field} is invalid")
    caller=raw["caller_contract"]
    if not isinstance(caller,Mapping) or caller.get("work_package_id") != raw["work_package_id"] or caller.get("task_ids") != ids: raise AgentContractError("CONTRACT_INVALID", "caller contract drifted")

def validate_codex_main_task_projection(raw: Any, path: str="raw task") -> None:
    required={"schema_version","task_id","task_source","task_version","change_version","allowed_files","forbidden_files","expected_output","acceptance_criteria","acceptance_evidence","validation_command","goal","required_context","failure_policy","parent_session_id","agent_id","run_id","session_id","client","parent_client"}
    if not isinstance(raw,Mapping) or set(raw)!=required or raw["schema_version"]!=CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION: raise AgentContractError("CONTRACT_INVALID",f"{path} fields are invalid")
    if not isinstance(raw["task_id"],str) or not _ID.fullmatch(raw["task_id"]): raise AgentContractError("CONTRACT_INVALID", "task id is invalid")
    _identity(raw,path)
    if raw["agent_id"] not in {"/root", "codex-session-" + raw["session_id"]}:
        raise AgentContractError("CONTRACT_INVALID", "projection must bind the canonical Main actor")
    normalize_scope_string(raw["allowed_files"]); normalize_scope_string(raw["forbidden_files"])

def validate_all_result_fields(result_fields: Mapping[str, Any], artifact_locators: set[str]) -> None:
    if not isinstance(result_fields,Mapping) or set(result_fields) != set(REQUIRED_RESULT_FIELDS): raise AgentContractError("RESULT_INVALID", "result fields are incomplete")
    if result_fields["status"] not in RESULT_STATUSES: raise AgentContractError("RESULT_INVALID", "status is invalid")
    for locator in artifact_locators: check_locator_safety_strict(locator)
