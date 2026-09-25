"""Qoder 单个 run 的只读、身份绑定快照；不派发、不推进生命周期。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.agents.qoder import lifecycle as _lifecycle
from scripts.agents.qoder.handoff import _is_valid_uuid, _validate_task

_safe_read_json = _lifecycle._safe_read_json
_TERMINAL_COMPLETION_STATUSES = frozenset({"finished", "failed", "completed"})


def _bound_task_record(run_dir: Path) -> tuple[dict[str, Any] | None, str | None]:
    """仅读取本 run 持久化的身份；畸形历史不视为可信。"""
    try:
        task = _safe_read_json(run_dir / "task.json")
        _validate_task(task, runtime_bound=True)
        if task.get("run_id") != run_dir.name:
            raise ValueError("task run_id does not match run directory")
        return task, None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, f"task-record-invalid:{type(exc).__name__}"


def _bound_started_record(
    run_dir: Path, task: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    path = _lifecycle.started_path(run_dir)
    if not path.exists():
        return None, "started-record-missing"
    try:
        started = _safe_read_json(path)
        if set(started) != {"run_id", "started_at", "pid", "session_id"}:
            raise ValueError("started fields are invalid")
        if started["run_id"] != run_dir.name or started["run_id"] != task["run_id"]:
            raise ValueError("started run_id does not match")
        if started["session_id"] != task["session_id"] or not _is_valid_uuid(
            started["session_id"]
        ):
            raise ValueError("started session_id does not match")
        if (
            isinstance(started["pid"], bool)
            or not isinstance(started["pid"], int)
            or started["pid"] <= 0
        ):
            raise ValueError("started pid is invalid")
        if isinstance(started["started_at"], bool) or not isinstance(
            started["started_at"], (int, float)
        ):
            raise ValueError("started timestamp is invalid")
        return started, None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, f"started-record-invalid:{type(exc).__name__}"


def _bound_completion_record(
    run_dir: Path, task: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    path = run_dir / "completion.json"
    if not path.exists():
        return None, "completion-record-missing"
    try:
        completion = _safe_read_json(path)
        _lifecycle._verify_completion_identity(completion, task, run_dir.name)
        if completion.get("status") not in _TERMINAL_COMPLETION_STATUSES:
            raise ValueError("completion is not terminal")
        return completion, None
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return None, f"completion-record-invalid:{type(exc).__name__}"


def _is_bound_dispatch_startup_failure(completion: dict[str, Any]) -> bool:
    """派发阶段 Popen 失败虽无 worker started.json，仍是终态。"""
    return (
        completion.get("status") == "failed"
        and completion.get("exit_code") == 126
        and isinstance(completion.get("error"), str)
        and completion["error"].startswith("worker spawn failed:")
    )


def _status_snapshot(run_dir: Path) -> tuple[str, dict[str, Any]]:
    """只读一次已协调、身份绑定的快照；不 watch 或扫描日志。"""
    task, task_error = _bound_task_record(run_dir)
    if task is None:
        return "conflict", {"reason": task_error}
    try:
        lifecycle = _lifecycle.read_lifecycle(run_dir)
        if not isinstance(lifecycle, dict):
            raise ValueError("lifecycle root is invalid")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return "conflict", {"reason": f"lifecycle-record-invalid:{type(exc).__name__}"}
    continuation: dict[str, Any] | None = None
    continuation_path = run_dir / "continuation.json"
    if continuation_path.exists():
        try:
            continuation = _safe_read_json(continuation_path)
            if (
                continuation.get("run_id") != run_dir.name
                or not isinstance(continuation.get("state"), str)
                or not isinstance(continuation.get("next_action"), str)
            ):
                raise ValueError("continuation identity is invalid")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return "conflict", {
                "reason": f"continuation-record-invalid:{type(exc).__name__}"
            }
    started, started_error = _bound_started_record(run_dir, task)
    completion, completion_error = _bound_completion_record(run_dir, task)
    completion_path = run_dir / "completion.json"
    if completion_path.exists() and completion is None:
        return "conflict", {"reason": completion_error, "continuation": continuation}
    callback_path = _lifecycle.callback_file_path(run_dir)
    if callback_path.exists():
        try:
            callback = _safe_read_json(callback_path)
            if callback.get("parent_session_id") != task[
                "parent_session_id"
            ] or callback.get("status") not in {"queued", "failed", "unknown"}:
                raise ValueError("callback identity is invalid")
            if completion is None:
                return "conflict", {
                    "reason": "callback-without-bound-terminal-completion",
                    "continuation": continuation,
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return "conflict", {
                "reason": f"callback-record-invalid:{type(exc).__name__}",
                "continuation": continuation,
            }
    ack_path = _lifecycle.ack_path(run_dir)
    if ack_path.exists():
        try:
            ack = _safe_read_json(ack_path)
            if (
                ack.get("run_id") != run_dir.name
                or ack.get("parent_session_id") != task["parent_session_id"]
                or ack.get("qoder_session_id") != task["session_id"]
                or ack.get("task_id") != task["task_id"]
            ):
                raise ValueError("ack identity is invalid")
            if completion is None:
                return "conflict", {
                    "reason": "ack-without-bound-terminal-completion",
                    "continuation": continuation,
                }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return "conflict", {
                "reason": f"ack-record-invalid:{type(exc).__name__}",
                "continuation": continuation,
            }
    if completion is not None:
        # worker spawn 失败是真实且已绑定的终态失败，可以没有 started.json；
        # 其他终态结果均需有效的启动证据。
        if started is None and not _is_bound_dispatch_startup_failure(completion):
            return "conflict", {"reason": started_error, "continuation": continuation}
        return completion["status"], {
            "completion": completion,
            "lifecycle": lifecycle,
            "continuation": continuation,
            "startup": "STARTED" if started is not None else "FAILED",
        }
    if started is not None:
        return "running", {
            "lifecycle": lifecycle,
            "continuation": continuation,
            "startup": "STARTED",
        }
    if lifecycle.get("status") in ("acknowledged", "superseded", "exhausted"):
        return "conflict", {
            "reason": "lifecycle-terminal-without-bound-completion",
            "continuation": continuation,
        }
    return "unknown", {
        "lifecycle": lifecycle,
        "continuation": continuation,
        "startup": "STARTING",
        "startup_reason": started_error,
    }
