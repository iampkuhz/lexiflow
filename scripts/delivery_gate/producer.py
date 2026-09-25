"""从已验证的单个 run 引用读取可选的 agent 执行事实，不从日志猜测结果。"""

from __future__ import annotations
from pathlib import Path
from typing import Any
from scripts.delivery_gate.records import (
    RecordError,
    parse_json,
    read_bound_bytes,
    require_uuid,
)


class ProducerError(ValueError):
    """实现来源 run 与送验 Task 不匹配时携带错误代码。"""

    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _codex(repo: Path, run_id: str, task: dict[str, Any]) -> dict[str, Any] | None:
    path = repo / f"tmp/quality/codex-work-packages/{run_id}/package-completion.json"
    if not path.exists():
        return None
    from scripts.agents.codex.work_package import (
        CodexWorkPackageError,
        CodexWorkPackagePublisher,
    )

    try:
        verified = CodexWorkPackagePublisher(repo).verify(run_id)
        descriptor = verified["artifact_locators"]["package_completion"]
        value = parse_json(
            read_bound_bytes(repo, descriptor["locator"], descriptor["sha256"]),
            "codex package completion",
        )
    except (CodexWorkPackageError, RecordError, KeyError) as exc:
        raise ProducerError("producer-run-invalid", str(exc)) from None
    tid = task["task_id"]
    if (
        tid not in value.get("task_ids", [])
        or value.get("task_versions", {}).get(tid) != task["task_version"]
        or value.get("change_versions", {}).get(tid) != task["change_version"]
    ):
        raise ProducerError("producer-task-mismatch", tid)
    if value.get("tasks", {}).get(tid, {}).get("status") != "PASS":
        raise ProducerError("producer-result-not-pass", tid)
    identity = value.get("identity")
    if not isinstance(identity, dict):
        raise ProducerError("producer-identity-invalid", tid)
    return {
        "kind": "codex-work-package",
        "run_id": run_id,
        "identity": identity,
        "completion": descriptor,
    }


def _qoder(repo: Path, run_id: str, task_req: dict[str, Any]) -> dict[str, Any] | None:
    base = f"tmp/qoder-tasks/{run_id}"
    directory = repo / base
    if not directory.exists():
        return None
    from scripts.agents.qoder import QoderFactsError, verify_qoder_work_package_facts

    try:
        facts = verify_qoder_work_package_facts(repo, run_id)
    except QoderFactsError as exc:
        raise ProducerError("producer-run-invalid", str(exc)) from None
    tid = task_req["task_id"]
    if (
        tid not in facts["task_ids"]
        or facts["task_versions"].get(tid) != task_req["task_version"]
        or facts["change_versions"].get(tid) != task_req["change_version"]
    ):
        raise ProducerError("producer-task-mismatch", tid)
    outcome = next((x for x in facts["outcomes"] if x.get("task_id") == tid), None)
    if (
        facts["result_status"] != "PASS"
        or not outcome
        or outcome.get("status") != "PASS"
    ):
        raise ProducerError("producer-result-not-pass", tid)
    return {
        "kind": "qoder-work-package",
        "run_id": run_id,
        "identity": facts["identity"],
        "artifacts": facts["artifacts"],
    }


def resolve_producer(
    repo: Path, task: dict[str, Any], submitter: dict[str, Any], run_id: str | None
) -> dict[str, Any]:
    """核对当前主任务或精确 Agent run 的原始执行事实，不从日志猜测。"""
    if run_id is None:
        return {
            "kind": "current-codex-task",
            "identity": submitter["identity"],
            "authority": submitter["authority"],
            "runtime_proof": submitter["proof"],
        }
    try:
        require_uuid(run_id, "producer-run")
    except RecordError as exc:
        raise ProducerError(exc.code, exc.detail) from None
    matches = [
        x
        for x in (_codex(repo, run_id, task), _qoder(repo, run_id, task))
        if x is not None
    ]
    if len(matches) != 1:
        raise ProducerError(
            "producer-run-unavailable" if not matches else "producer-run-ambiguous",
            run_id,
        )
    return matches[0]
