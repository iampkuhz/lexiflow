"""提供 Delivery Gate 消费的 Task 要求；日常 Verify 不拥有或推断这些要求。"""

from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml
from scripts.delivery_gate.records import (
    RecordError,
    canonical_bytes,
    read_regular_bytes,
    sha256_bytes,
)


def _catalog_tasks(catalog: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(catalog, dict) or not isinstance(
        catalog.get("workstreams"), list
    ):
        raise ValueError("catalog topology invalid")
    tasks = {}
    for ws in catalog["workstreams"]:
        if not isinstance(ws, dict):
            raise ValueError("catalog workstream invalid")
        for epic in ws.get("epics", []):
            for capability in (
                epic.get("capabilities", []) if isinstance(epic, dict) else []
            ):
                for task in (
                    capability.get("seed_tasks", [])
                    if isinstance(capability, dict)
                    else []
                ):
                    if (
                        not isinstance(task, dict)
                        or not isinstance(task.get("id"), str)
                        or task["id"] in tasks
                    ):
                        raise ValueError("catalog task invalid")
                    tasks[task["id"]] = {
                        "owner": task.get("owner", ws.get("id")),
                        "task": task,
                    }
    return tasks


def _dependencies(task: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
    raw = task.get("depends_on", task.get("dependencies", []))
    result = []
    if not isinstance(raw, list):
        raise RecordError("task-dependencies-invalid", task_id)
    for item in raw:
        if isinstance(item, str):
            dep = {
                "task_id": item,
                "required_task_version": None,
                "required_change_version": None,
                "required_result": "PASS",
            }
        elif isinstance(item, dict):
            dep = {
                "task_id": item.get("task_id"),
                "required_task_version": item.get("required_task_version"),
                "required_change_version": item.get("required_change_version"),
                "required_result": item.get("required_result", "PASS"),
            }
        else:
            raise RecordError("task-dependencies-invalid", task_id)
        if (
            not isinstance(dep["task_id"], str)
            or not dep["task_id"]
            or dep["required_result"] != "PASS"
        ):
            raise RecordError("task-dependencies-invalid", task_id)
        if dep["required_task_version"] is not None and (
            isinstance(dep["required_task_version"], bool)
            or not isinstance(dep["required_task_version"], int)
            or dep["required_task_version"] < 1
        ):
            raise RecordError("task-dependencies-invalid", task_id)
        if dep["required_change_version"] is not None and (
            not isinstance(dep["required_change_version"], str)
            or not dep["required_change_version"]
        ):
            raise RecordError("task-dependencies-invalid", task_id)
        result.append(dep)
    if len({x["task_id"] for x in result}) != len(result):
        raise RecordError("task-dependencies-invalid", task_id)
    return sorted(result, key=lambda x: x["task_id"])


def load_task_requirements(root: Path, task_id: str) -> dict[str, Any]:
    """从当前 Catalog 加载单个 Task 的要求和版本，不从旧 submission 推断。返回经核对的范围及必需 Check，未知或不完整 Task 直接失败。"""
    try:
        catalog = yaml.safe_load(read_regular_bytes(root, "planning/workstreams.yaml"))
        entries = _catalog_tasks(catalog)
    except (OSError, yaml.YAMLError, ValueError, TypeError, AttributeError) as exc:
        raise RecordError("task-catalog-invalid", str(exc)) from None
    entry = entries.get(task_id)
    if entry is None:
        raise RecordError("task-not-found", task_id)
    task = entry["task"]
    version = task.get("task_version", task.get("version"))
    change = task.get("change_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or not isinstance(change, str)
        or not change
    ):
        raise RecordError("task-version-invalid", task_id)
    if "required_check_ids" not in task:
        raise RecordError("task-required-checks-missing", task_id)
    required = task["required_check_ids"]
    allowed = task.get("allowed_files", [])
    forbidden = task.get("forbidden_files", [])
    raw_claims = task.get("file_claims", [])
    if not isinstance(required, list) or not all(
        isinstance(x, str) and x for x in required
    ):
        raise RecordError("task-required-checks-invalid", task_id)
    if not all(isinstance(x, list) for x in (allowed, forbidden, raw_claims)) or any(
        not isinstance(x, dict) or set(x) - {"path", "mode", "owner"}
        for x in raw_claims
    ):
        raise RecordError("task-scope-invalid", task_id)
    claims = [x.get("path") for x in raw_claims]
    if not all(isinstance(x, str) and x for x in [*allowed, *forbidden, *claims]):
        raise RecordError("task-scope-invalid", task_id)
    approval = None
    prereq = task.get("phase_entry_prerequisite")
    if isinstance(prereq, dict) and prereq.get("required_user_approval") == "APPROVED":
        approval = {
            "required": True,
            "previous_gate_id": prereq.get("previous_gate_id"),
            "subject_task_id": prereq.get("previous_gate_exit_task_id"),
            "evidence_type": "phase-gate-approval-receipt",
        }
        if not isinstance(approval["previous_gate_id"], str) or not isinstance(
            approval["subject_task_id"], str
        ):
            raise RecordError("task-approval-invalid", task_id)
    projection = {
        "schema_version": "lexiflow.delivery-gate-task-requirements.v2",
        "owner": entry["owner"],
        "task": task,
    }
    return {
        "task_id": task_id,
        "task_version": version,
        "change_version": change,
        "dependencies": _dependencies(task, task_id),
        "required_check_ids": sorted(set(required)),
        "allowed_files": sorted(set(allowed)),
        "forbidden_files": sorted(set(forbidden)),
        "file_claims": sorted(set(claims)),
        "approval_requirement": approval,
        "source": {
            "locator": "planning/workstreams.yaml",
            "sha256": sha256_bytes(canonical_bytes(projection)),
        },
    }


def requirements_are_current(root: Path, requirements: Any) -> bool:
    """重读当前 Catalog 要求并比较已冻结送验要求是否仍有效。"""
    if not isinstance(requirements, dict) or not isinstance(
        requirements.get("task_id"), str
    ):
        return False
    try:
        return load_task_requirements(root, requirements["task_id"]) == requirements
    except RecordError:
        return False
