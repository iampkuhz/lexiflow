"""将一个 Catalog Task 与一份已持久化的日常 Verify 报告绑定为 submission。"""

from __future__ import annotations
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from scripts.acceptance.evidence.acceptance_actor_identity import discover_authority
from scripts.acceptance.task_definition.task_acceptance_plan import acceptance_plan
from scripts.acceptance.evidence.task_review_diff import review_patch
from scripts.verification.planning.change_risk_assessment import assess
from scripts.acceptance.evidence.implementation_run_evidence import (
    ProducerError,
    resolve_producer,
    producer_identity,
)
from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    delivery_gate_locator,
    content_hash,
    publish_bytes,
    publish_json,
)
from scripts.acceptance.task_definition.task_acceptance_requirements import (
    load_task_requirements,
)
from scripts.verification import freeze_inputs, read_report
from scripts.verification.planning.check_scope_selection import changed_paths
from scripts.acceptance.task_definition.task_acceptance_scope import (
    derive_task_subject,
    subject_paths,
)


class SubmissionError(ValueError):
    """送验输入、身份或冻结范围无效时返回稳定错误代码。"""

    def __init__(self, code: str, detail: str, *, status: str = "BLOCKED") -> None:
        self.code, self.detail = code, detail
        self.status = status
        super().__init__(f"{code}: {detail}")


def _err(e: Exception) -> SubmissionError:
    return SubmissionError(
        getattr(e, "code", "submission-invalid"),
        getattr(e, "detail", str(e)),
        status=getattr(e, "status", "BLOCKED"),
    )


def _discover_runtime(repo: Path) -> dict[str, Any]:
    return discover_authority(repo, SubmissionError)


def submit(
    root: str | Path,
    *,
    task_id: str,
    change_report_id: str,
    producer_run_id: str | None = None,
) -> dict[str, Any]:
    """绑定单个 Task、已持久化的 PASS Change Verify 报告与真实 producer。先核对变更范围和当前输入，再冻结 Check 闭包及 diff；仅在这些条件满足后发布不可变 submission。"""
    repo = Path(root).resolve()
    submitter = _discover_runtime(repo)
    try:
        requirements = load_task_requirements(repo, task_id)
        report, report_descriptor = read_report(repo, change_report_id)
        producer = resolve_producer(repo, requirements, producer_run_id)
    except (RecordError, ValueError, ProducerError) as exc:
        raise _err(exc) from None
    if report.get("scope") != "development-change" or report.get("result") != "PASS":
        raise SubmissionError(
            "change-report-not-pass",
            "submit requires persisted PASS development-change report",
        )
    scope = report.get("scope_review", {})
    changed = scope.get("changed_files") if isinstance(scope, dict) else None
    base = report.get("base")
    if (
        not isinstance(changed, list)
        or any(not isinstance(x, str) for x in changed)
        or not isinstance(base, str)
        or not base
    ):
        raise SubmissionError(
            "change-report-invalid", "scope changed_files/base missing"
        )
    try:
        current_changed = changed_paths(repo, base)
    except Exception as exc:
        raise SubmissionError("scope-input-drift", str(exc)) from None
    if sorted(set(changed)) != current_changed:
        raise SubmissionError(
            "scope-input-drift", "daily report changed-file set is no longer current"
        )
    try:
        subject = derive_task_subject(repo, requirements, current_changed)
    except ValueError as exc:
        code, _, detail = str(exc).partition(": ")
        raise SubmissionError(code, detail or code) from None
    try:
        assessment = assess(
            repo,
            base=base,
            required_check_ids=tuple(requirements["required_check_ids"]),
            _subject_paths=tuple(subject_paths(subject)),
        )
        plan = acceptance_plan(repo, assessment, requirements)
    except (RecordError, ValueError) as exc:
        raise _err(exc) from None
    if assessment["changed_files"] != subject_paths(subject):
        raise SubmissionError(
            "scope-input-drift", "task subject changed during submission"
        )
    frozen = freeze_inputs(
        repo,
        required_check_ids=requirements["required_check_ids"],
        verification_scope=plan["verification_scope"],
        base=assessment["base"],
        _subject_changed_files=(
            subject_paths(subject)
            if plan["verification_scope"] == "development-change"
            else None
        ),
    )
    if frozen.get("result") != "PASS":
        raise SubmissionError(
            "freeze-inputs-failed", str(frozen.get("reason", "unknown"))
        )
    submission_id = str(uuid.uuid4())
    patch = review_patch(repo, assessment["base"], subject_paths(subject))
    try:
        patch_descriptor = publish_bytes(
            repo,
            f"tmp/quality/delivery-gate/current/submissions/{submission_id}.diff.patch",
            patch,
        )
        created = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        record = {
            "schema_version": "lexiflow.delivery-gate-submission.v6",
            "risk_assessment": assessment,
            "acceptance_plan": plan,
            "submission_id": submission_id,
            "task_requirements": requirements,
            "change_report": report_descriptor,
            "diff": patch_descriptor,
            "submitter_identity": submitter["identity"],
            "runtime_proof": submitter["proof"],
            "authority": submitter["authority"],
            "producer": producer,
            "verification_freeze": frozen,
            "task_subject": subject,
            "created_at": created,
        }
        from scripts.acceptance.task_definition.task_acceptance_plan import verify_plan
        from scripts.verification import verify_frozen_inputs

        verify_plan(repo, record)
        if not verify_frozen_inputs(repo, frozen):
            raise SubmissionError(
                "frozen-input-drift", "input changed before publication"
            )
        published = publish_json(
            repo, delivery_gate_locator("submissions", submission_id), record
        )
    except RecordError as exc:
        raise _err(exc) from None
    return {
        "result": "PASS",
        "submission_id": submission_id,
        "task_id": task_id,
        "content_hash": content_hash(record),
        "record_sha256": published["sha256"],
        "producer_actor_id": producer_identity(record).get(
            "actor_id", producer_identity(record).get("agent_id")
        ),
        "frozen_input_fingerprint": frozen["input_fingerprint"],
    }
