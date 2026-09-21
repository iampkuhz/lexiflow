"""Read-only catalog lookup for the few non-planning consumers that need it.

Missing/unparseable catalog or task is never equivalent to an empty dependency
list: callers must fail closed rather than accidentally certify a task.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml


def catalog_tasks(catalog: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(catalog, dict) or not isinstance(catalog.get("workstreams"), list):
        raise ValueError("catalog topology invalid")
    result: dict[str, dict[str, Any]] = {}
    for workstream in catalog["workstreams"]:
        if not isinstance(workstream, dict):
            raise ValueError("catalog workstream invalid")
        owner = workstream.get("id")
        for epic in workstream.get("epics", []):
            for capability in epic.get("capabilities", []) if isinstance(epic, dict) else []:
                for task in capability.get("seed_tasks", []) if isinstance(capability, dict) else []:
                    if not isinstance(task, dict) or not isinstance(task.get("id"), str):
                        raise ValueError("catalog task invalid")
                    if task["id"] in result:
                        raise ValueError(f"duplicate catalog task: {task['id']}")
                    result[task["id"]] = {"owner": owner, "task": task}
    return result


def read_task_dependencies(repo_root: Path, task_id: str) -> tuple[bool, list[str]]:
    catalog_path = repo_root / "planning/workstreams.yaml"
    try:
        if not catalog_path.is_file():
            return False, []
        entries = catalog_tasks(yaml.safe_load(catalog_path.read_bytes()))
        entry = entries.get(task_id)
        if entry is None:
            return False, []
        dependencies = entry["task"].get("dependencies", [])
        if not isinstance(dependencies, list) or not all(isinstance(v, str) and v for v in dependencies):
            return False, []
        return True, sorted(set(dependencies))
    except (OSError, yaml.YAMLError, ValueError, TypeError):
        return False, []
