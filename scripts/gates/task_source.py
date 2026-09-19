"""Task-scoped catalog source fingerprints.

``planning/workstreams.yaml`` is a catalog, not a single task.  A receipt for
one task must therefore bind the canonical task record, rather than treating an
unrelated catalog edit as a mutation of that task.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


def canonical_bytes(value: Any) -> bytes:
    """Canonical YAML-independent bytes for a task source projection."""
    import json
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
                    # Include the owning workstream as topology affects the task's authority.
                    result[task["id"]] = {"owner": owner, "task": task}
    return result


def task_source_hash_from_catalog(catalog: Any, task_id: str) -> str:
    tasks = catalog_tasks(catalog)
    if task_id not in tasks:
        raise ValueError(f"catalog task missing: {task_id}")
    return hashlib.sha256(canonical_bytes({"schema_version": "lexiflow.task-source.v1", **tasks[task_id]})).hexdigest()


def task_source_descriptor(repo_root: str | Path, task_id: str) -> dict[str, str]:
    root = Path(repo_root)
    content = (root / "planning/workstreams.yaml").read_bytes()
    catalog = yaml.safe_load(content)
    return {"locator": "planning/workstreams.yaml", "sha256": task_source_hash_from_catalog(catalog, task_id)}


def source_descriptor_is_current(repo_root: str | Path, task_id: str, descriptor: Any) -> bool:
    """Accept current task projections and only the historical raw-file shape.

    Old immutable fixtures/receipts bound the whole catalog file.  They remain
    safe to consume because the normal consumed-input hash check rejects any
    catalog edit.  New packets use the narrower task projection so unrelated
    catalog edits do not repeatedly invalidate an otherwise valid task.
    """
    if not isinstance(descriptor, dict) or set(descriptor) != {"locator", "sha256"}:
        return False
    root = Path(repo_root)
    path = root / "planning/workstreams.yaml"
    try:
        content = path.read_bytes()
    except OSError:
        return False
    if descriptor == {"locator": "planning/workstreams.yaml", "sha256": hashlib.sha256(content).hexdigest()}:
        return True
    try:
        return descriptor == task_source_descriptor(root, task_id)
    except (OSError, ValueError, yaml.YAMLError):
        return False
