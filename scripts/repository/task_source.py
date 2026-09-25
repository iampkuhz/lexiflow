"""Task 级 Catalog 来源指纹。

planning/workstreams.yaml 含多个 Task。单项 receipt 绑定规范化 Task 记录，
避免无关 Catalog 编辑被误认为该 Task 的输入变化。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from scripts.repository.catalog import catalog_tasks


def canonical_bytes(value: Any) -> bytes:
    """生成与 YAML 排版无关的规范字节，用于 Task 来源投影的哈希绑定。"""
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def task_source_hash_from_catalog(catalog: Any, task_id: str) -> str:
    """从当前 Catalog 计算 Task 来源哈希，避免复用漂移的计划事实。"""
    tasks = catalog_tasks(catalog)
    if task_id not in tasks:
        raise ValueError(f"catalog task missing: {task_id}")
    return hashlib.sha256(
        canonical_bytes({"schema_version": "lexiflow.task-source.v1", **tasks[task_id]})
    ).hexdigest()


def task_source_descriptor(repo_root: str | Path, task_id: str) -> dict[str, str]:
    """构造绑定当前 Task 来源与版本的证据描述符。"""
    root = Path(repo_root)
    content = (root / "planning/workstreams.yaml").read_bytes()
    catalog = yaml.safe_load(content)
    return {
        "locator": "planning/workstreams.yaml",
        "sha256": task_source_hash_from_catalog(catalog, task_id),
    }


def source_descriptor_is_current(
    repo_root: str | Path, task_id: str, descriptor: Any
) -> bool:
    """接受 Task 投影，以及既有的 raw-file 描述形状。

    raw-file 仍绑定整个 Catalog，消费时的哈希核对会拒绝文件漂移；Task 投影仅绑定
    该 Task，避免无关条目变化重复使证据失效。此处只判断 descriptor 形状，不执行验收。"""
    if not isinstance(descriptor, dict) or set(descriptor) != {"locator", "sha256"}:
        return False
    root = Path(repo_root)
    path = root / "planning/workstreams.yaml"
    try:
        content = path.read_bytes()
    except OSError:
        return False
    if descriptor == {
        "locator": "planning/workstreams.yaml",
        "sha256": hashlib.sha256(content).hexdigest(),
    }:
        return True
    try:
        return descriptor == task_source_descriptor(root, task_id)
    except (OSError, ValueError, yaml.YAMLError):
        return False
