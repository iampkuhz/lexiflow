"""公开、只读的 Qoder 执行事实读取器。核验 runner 拥有的 Task、completion 与身份绑定，不用日志推断结果。"""

from __future__ import annotations
import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from typing import Any
from scripts.agents.qoder.lifecycle import _verify_completion_identity


class QoderFactsError(ValueError):
    """Qoder 原始事实无法证明精确 run 结果时携带拒绝代码。"""

    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _uuid(value: Any) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise QoderFactsError("qoder-run-invalid", "run_id must be UUID") from None
    if str(parsed) != value:
        raise QoderFactsError("qoder-run-invalid", "run_id must be canonical UUID")
    return value


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _read(run_dir: Path, name: str) -> tuple[dict[str, Any], dict[str, str]]:
    path = run_dir / name
    try:
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("not a regular file")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("not a regular file")
            chunks = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(fd)
        data = b"".join(chunks)
        value = json.loads(data, object_pairs_hook=_unique)
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise QoderFactsError("qoder-record-invalid", f"{name}: {exc}") from None
    if not isinstance(value, dict):
        raise QoderFactsError("qoder-record-invalid", f"{name} is not an object")
    return value, {
        "locator": f"tmp/qoder-tasks/{run_dir.name}/{name}",
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _validate_task_identity(task: dict[str, Any], run_id: str) -> None:
    """核对 runner 持久化的 Task 身份、run 和版本字段；任何不匹配都不能作为当前执行事实。"""
    required = (
        "work_package_id",
        "task_id",
        "task_ids",
        "task_version",
        "change_version",
        "task_versions",
        "change_versions",
        "parent_session_id",
        "agent_id",
        "session_id",
        "client",
        "parent_client",
    )
    if any(field not in task for field in required):
        raise QoderFactsError("qoder-task-invalid", "identity fields")
    task_ids = task["task_ids"]
    if (
        not isinstance(task_ids, list)
        or not task_ids
        or any(not isinstance(x, str) or not x for x in task_ids)
        or len(set(task_ids)) != len(task_ids)
    ):
        raise QoderFactsError("qoder-task-invalid", "task_ids")
    if (
        task.get("task_id") != task_ids[0]
        or not isinstance(task.get("work_package_id"), str)
        or not task["work_package_id"]
    ):
        raise QoderFactsError("qoder-task-invalid", "primary task/work package")
    versions, changes = task["task_versions"], task["change_versions"]
    if (
        not isinstance(versions, dict)
        or set(versions) != set(task_ids)
        or any(
            isinstance(x, bool) or not isinstance(x, int) or x < 1
            for x in versions.values()
        )
    ):
        raise QoderFactsError("qoder-task-invalid", "task_versions")
    if (
        not isinstance(changes, dict)
        or set(changes) != set(task_ids)
        or any(not isinstance(x, str) or not x for x in changes.values())
    ):
        raise QoderFactsError("qoder-task-invalid", "change_versions")
    if (
        task.get("task_version") != versions[task["task_id"]]
        or task.get("change_version") != changes[task["task_id"]]
    ):
        raise QoderFactsError("qoder-task-invalid", "primary versions")
    if (
        task.get("run_id") != run_id
        or task.get("client") != "qoder"
        or task.get("parent_client") != "codex"
    ):
        raise QoderFactsError("qoder-task-invalid", "run/client identity")
    for field in ("parent_session_id", "agent_id", "session_id"):
        if not isinstance(task.get(field), str) or not task[field]:
            raise QoderFactsError("qoder-task-invalid", field)


def validate_structured_result(
    run_dir: Path, task: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """核对 Qoder 结构化结果的身份与逐 Task 证据，不从退出码推断 PASS。"""
    result, descriptor = _read(run_dir, "result.json")
    if result.get("schema_version") != "lexiflow.qoder-work-package-result.v1":
        raise QoderFactsError("qoder-result-invalid", "schema")
    for field in (
        "work_package_id",
        "task_ids",
        "task_versions",
        "change_versions",
        "run_id",
    ):
        expected = task["run_id"] if field == "run_id" else task[field]
        if result.get(field) != expected:
            raise QoderFactsError("qoder-result-invalid", f"identity mismatch: {field}")
    if result.get("status") not in {"PASS", "BLOCKED", "FAIL"}:
        raise QoderFactsError("qoder-result-invalid", "status")
    outcomes = result.get("outcomes")
    if (
        not isinstance(outcomes, list)
        or [x.get("task_id") for x in outcomes if isinstance(x, dict)]
        != task["task_ids"]
    ):
        raise QoderFactsError("qoder-result-invalid", "ordered outcomes")
    for outcome in outcomes:
        if outcome.get("status") not in {"PASS", "BLOCKED", "FAIL"} or not isinstance(
            outcome.get("acceptance_evidence"), list
        ):
            raise QoderFactsError("qoder-result-invalid", "outcome")
    expected_status = (
        "FAIL"
        if any(x["status"] == "FAIL" for x in outcomes)
        else "BLOCKED"
        if any(x["status"] == "BLOCKED" for x in outcomes)
        else "PASS"
    )
    if result.get("status") != expected_status:
        raise QoderFactsError("qoder-result-invalid", "aggregate status")
    for field in (
        "changed_files",
        "validation",
        "acceptance_evidence",
        "effect_checks",
        "risks",
    ):
        if not isinstance(result.get(field), list):
            raise QoderFactsError("qoder-result-invalid", field)
    return result, descriptor


def verify_qoder_work_package_facts(root: str | Path, run_id: str) -> dict[str, Any]:
    """只读核验给定 run 的 Task、结构化结果与 completion 的身份和哈希绑定。返回公开的有界事实，不读取私有 Prompt 或以退出码推断验收。"""
    repo = Path(root).resolve()
    rid = _uuid(run_id)
    run_dir = repo / f"tmp/qoder-tasks/{rid}"
    try:
        relative = run_dir.relative_to(repo)
    except ValueError:
        raise QoderFactsError("qoder-run-invalid", rid) from None
    current = repo
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            raise QoderFactsError("qoder-run-missing", rid) from None
        if stat.S_ISLNK(info.st_mode):
            raise QoderFactsError("qoder-record-unsafe", rid)
    task, task_desc = _read(run_dir, "task.json")
    completion, completion_desc = _read(run_dir, "completion.json")
    _validate_task_identity(task, rid)
    try:
        _verify_completion_identity(completion, task, rid)
    except (ValueError, TypeError) as exc:
        raise QoderFactsError("qoder-completion-invalid", str(exc)) from None
    for field in ("work_package_id", "task_ids", "task_versions", "change_versions"):
        if completion.get(field) != task.get(field):
            raise QoderFactsError(
                "qoder-completion-invalid", f"identity mismatch: {field}"
            )
    if (
        completion.get("status") != "finished"
        or type(completion.get("exit_code")) is not int
        or completion["exit_code"] != 0
        or type(completion.get("qoder_exit_code")) is not int
        or completion["qoder_exit_code"] != 0
    ):
        raise QoderFactsError("qoder-completion-not-success", "terminal status/exit")
    result, result_desc = validate_structured_result(run_dir, task)
    identity = {
        field: task[field]
        for field in (
            "parent_session_id",
            "agent_id",
            "run_id",
            "session_id",
            "client",
            "parent_client",
        )
    }
    return {
        "schema_version": "lexiflow.qoder-work-package-facts.v1",
        "run_id": rid,
        "work_package_id": task["work_package_id"],
        "task_ids": task["task_ids"],
        "task_versions": task["task_versions"],
        "change_versions": task["change_versions"],
        "identity": identity,
        "terminal_status": completion["status"],
        "result_status": result["status"],
        "outcomes": result["outcomes"],
        "artifacts": {
            "task": task_desc,
            "completion": completion_desc,
            "result": result_desc,
        },
    }
