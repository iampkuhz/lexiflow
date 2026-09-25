"""校验 Qoder 调用方交接内容与运行身份绑定。"""

from __future__ import annotations

import re
from typing import Any

REQUIRED_HANDOFF: tuple[str, ...] = (
    "goal",
    "task_id",
    "task_source",
    "task_version",
    "change_version",
    "work_package_id",
    "task_ids",
    "task_versions",
    "change_versions",
    "estimated_minutes",
    "primary_owner",
    "contract_boundary",
    "agent_profile",
    "harness_manifest",
    "allowed_files",
    "forbidden_files",
    "required_context",
    "expected_output",
    "acceptance_criteria",
    "acceptance_evidence",
    "validation_command",
    "failure_policy",
    "parent_client",
)

RUNTIME_IDENTITY_FIELDS: tuple[str, ...] = (
    "agent_id",
    "run_id",
    "session_id",
    "client",
    "harness_manifest_sha256",
    "harness_context",
)

DEFAULT_PERMISSION_MODE = "bypass_permissions"
MIN_QODER_PACKAGE_MINUTES = 10
MAX_QODER_PACKAGE_MINUTES = 360
MIN_QODER_PACKAGE_TASKS = 1

_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _is_valid_uuid(value: str) -> bool:
    """校验值是否为合法 UUID 格式。"""
    return isinstance(value, str) and bool(_UUID_RE.fullmatch(value))


def _is_non_empty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_non_empty_text_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def _validate_task(
    task: dict[str, Any], *, runtime_bound: bool = False
) -> dict[str, Any]:
    """校验 caller handoff；落盘前再校验 runner 绑定的身份。"""
    if not isinstance(task, dict):
        raise ValueError("task must be a JSON object")
    for field in REQUIRED_HANDOFF:
        if field not in task:
            raise ValueError(f"missing required handoff field: {field}")
    text_fields = set(REQUIRED_HANDOFF) - {
        "task_version",
        "task_ids",
        "task_versions",
        "change_versions",
        "estimated_minutes",
        "acceptance_criteria",
        "acceptance_evidence",
    }
    for field in sorted(text_fields):
        if not _is_non_empty_text(task[field]):
            raise ValueError(f"handoff field {field!r} must be non-empty text")
    for field in ("acceptance_criteria", "acceptance_evidence"):
        if not _is_non_empty_text_list(task[field]):
            raise ValueError(f"handoff field {field!r} must be a non-empty text list")
    task_version = task["task_version"]
    if (
        isinstance(task_version, bool)
        or not isinstance(task_version, int)
        or task_version <= 0
    ):
        raise ValueError("task_version must be a positive integer")
    change_version = task["change_version"]
    if not isinstance(change_version, str) or not _SEMVER_RE.fullmatch(change_version):
        raise ValueError("change_version must be an exact SemVer string")
    task_ids = task["task_ids"]
    if (
        not isinstance(task_ids, list)
        or len(task_ids) < MIN_QODER_PACKAGE_TASKS
        or len(task_ids) != len(set(task_ids))
        or not all(
            isinstance(item, str) and _ID_RE.fullmatch(item) for item in task_ids
        )
    ):
        raise ValueError(
            f"task_ids must contain at least {MIN_QODER_PACKAGE_TASKS} unique stable IDs"
        )
    if task["task_id"] not in task_ids:
        raise ValueError("anchor task_id must be present in task_ids")
    for field, version_type in (("task_versions", int), ("change_versions", str)):
        versions = task[field]
        if not isinstance(versions, dict) or set(versions) != set(task_ids):
            raise ValueError(f"{field} keys must exactly equal task_ids")
        for value in versions.values():
            if version_type is int and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ValueError(f"{field} values must be positive integers")
            if version_type is str and (
                not isinstance(value, str) or not _SEMVER_RE.fullmatch(value)
            ):
                raise ValueError(f"{field} values must be exact SemVer strings")
    if task["task_versions"].get(task["task_id"]) != task_version:
        raise ValueError("anchor task_version must match task_versions")
    if task["change_versions"].get(task["task_id"]) != change_version:
        raise ValueError("anchor change_version must match change_versions")
    estimated = task["estimated_minutes"]
    if (
        isinstance(estimated, bool)
        or not isinstance(estimated, int)
        or not MIN_QODER_PACKAGE_MINUTES <= estimated <= MAX_QODER_PACKAGE_MINUTES
    ):
        raise ValueError(
            f"estimated_minutes must be {MIN_QODER_PACKAGE_MINUTES}-{MAX_QODER_PACKAGE_MINUTES}"
        )
    if not _ID_RE.fullmatch(task["work_package_id"]):
        raise ValueError("work_package_id contains invalid characters")
    if not _ID_RE.fullmatch(task["agent_profile"]):
        raise ValueError("agent_profile contains invalid characters")
    if "title" in task and (
        not isinstance(task["title"], str) or not task["title"].strip()
    ):
        raise ValueError("title must be a non-empty string")
    if "task_id" in task:
        if not isinstance(task["task_id"], str) or not _ID_RE.fullmatch(
            task["task_id"]
        ):
            raise ValueError("task_id contains invalid characters")
    if not runtime_bound:
        supplied = sorted(field for field in RUNTIME_IDENTITY_FIELDS if field in task)
        if supplied:
            raise ValueError(
                "start task must omit runner-bound identity fields: "
                + ", ".join(supplied)
            )
    else:
        agent_id = task.get("agent_id", "")
        if not isinstance(agent_id, str) or not _ID_RE.fullmatch(agent_id):
            raise ValueError("agent_id contains invalid characters")
        run_id = task.get("run_id", "")
        if not isinstance(run_id, str) or not _is_valid_uuid(run_id):
            raise ValueError("run_id must be a runner-generated UUID")
        session_id = task.get("session_id", "")
        if not isinstance(session_id, str) or not _is_valid_uuid(session_id):
            raise ValueError("session_id must be a runner-bound UUID")
        if task.get("client") != "qoder":
            raise ValueError("client must be runner-forced to qoder")
    if not runtime_bound and "permission_mode" in task:
        raise ValueError("permission_mode is runner-owned; caller must omit it")
    if (
        runtime_bound
        and task.get("permission_mode", DEFAULT_PERMISSION_MODE)
        != DEFAULT_PERMISSION_MODE
    ):
        raise ValueError("runtime permission_mode must match policy")
    if task["parent_client"] != "codex":
        raise ValueError("parent_client must be codex")
    if "parent_session_id" in task and task["parent_session_id"] is not None:
        if not isinstance(task["parent_session_id"], str):
            raise ValueError("parent_session_id must be a string")
    if runtime_bound:
        parent_session_id = task.get("parent_session_id", "")
        if not isinstance(parent_session_id, str) or not _is_valid_uuid(
            parent_session_id
        ):
            raise ValueError("codex parent_session_id must be bound to a UUID")
    task["permission_mode"] = DEFAULT_PERMISSION_MODE
    return task
