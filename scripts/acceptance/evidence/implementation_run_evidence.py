"""从已验证的单个 Codex run 引用读取执行事实，不从日志猜测结果。"""

from __future__ import annotations
from pathlib import Path
from typing import Any
from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    require_uuid,
)


class ProducerError(ValueError):
    """实现来源 run 与送验 Task 不匹配时携带错误代码。"""

    def __init__(self, code: str, detail: str, *, status: str = "FAIL"):
        self.code, self.detail = code, detail
        self.status = status
        super().__init__(f"{code}: {detail}")


def _codex(repo: Path, run_id: str, task: dict[str, Any]) -> dict[str, Any] | None:
    path = repo / f"tmp/quality/codex-work-packages/{run_id}/package-completion.json"
    if not path.exists():
        return None
    from scripts.agents.work_packages.codex_work_package_publisher import (
        CodexWorkPackageError,
        CodexWorkPackagePublisher,
    )

    try:
        verified = CodexWorkPackagePublisher(repo).verify(run_id)
        descriptor = verified["artifact_locators"]["package_completion"]
        value = verified["producer_view"]
        caller = value["caller_contract"]
    except (CodexWorkPackageError, RecordError, KeyError) as exc:
        raise ProducerError(
            "producer-run-invalid", str(exc), status=getattr(exc, "status", "FAIL")
        ) from None
    tid = task["task_id"]
    if (
        tid not in caller["task_ids"]
        or caller["task_versions"].get(tid) != task["task_version"]
        or caller["change_versions"].get(tid) != task["change_version"]
    ):
        raise ProducerError("producer-task-mismatch", tid)
    if value["tasks"][tid]["outcome"]["status"] != "PASS":
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


def resolve_producer(
    repo: Path, task: dict[str, Any], run_id: str | None
) -> dict[str, Any]:
    """核对当前主任务或精确 Codex run；显式 run 不得回退为主任务。"""
    if run_id is None:
        return {"kind": "current-codex-task"}
    try:
        require_uuid(run_id, "producer-run")
    except RecordError as exc:
        raise ProducerError(exc.code, exc.detail) from None
    producer = _codex(repo, run_id, task)
    if producer is None:
        raise ProducerError("producer-run-unavailable", run_id)
    return producer


def producer_identity(submission: dict[str, Any]) -> dict[str, Any]:
    """从明确的来源类型派生实现者身份，委派来源不得借 submitter 补缺。"""
    producer = submission.get("producer")
    if not isinstance(producer, dict):
        raise ProducerError("producer-source-invalid", "producer missing")
    if producer.get("kind") == "current-codex-task":
        if set(producer) != {"kind"}:
            raise ProducerError(
                "producer-source-invalid", "Main producer fields are not exact"
            )
        identity = submission.get("submitter_identity")
    elif producer.get("kind") == "codex-work-package":
        identity = producer.get("identity")
    else:
        raise ProducerError("producer-source-invalid", "unknown producer kind")
    if not isinstance(identity, dict):
        raise ProducerError("producer-identity-invalid", "identity missing")
    return identity
