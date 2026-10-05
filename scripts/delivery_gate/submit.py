"""将一个 Catalog Task 与一份已持久化的日常 Verify 报告绑定为 submission。"""

from __future__ import annotations
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from scripts.delivery_gate.authority import discover_authority
from scripts.delivery_gate.acceptance import acceptance_plan
from scripts.delivery_gate.source_snapshot import review_patch
from scripts.verification.risk import assess
from scripts.delivery_gate.producer import ProducerError, resolve_producer
from scripts.delivery_gate.records import (
    RecordError,
    delivery_gate_locator,
    content_hash,
    publish_bytes,
    publish_json,
    read_optional_bytes,
    sha256_bytes,
)
from scripts.delivery_gate.requirements import load_task_requirements
from scripts.verification import freeze_inputs, read_report
from scripts.verification.scope import changed_paths
from scripts.delivery_gate.task_subject import derive_task_subject


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


def _snapshots(repo: Path, changed: list[str]) -> dict[str, dict[str, str]]:
    result = {}
    for relative in sorted(set(changed)):
        try:
            data = read_optional_bytes(repo, relative)
        except RecordError as exc:
            raise _err(exc) from None
        result[relative] = (
            {"state": "absent"}
            if data is None
            else {"state": "present", "sha256": sha256_bytes(data)}
        )
    return result


def submit(
    root: str | Path,
    *,
    task_id: str,
    change_report_id: str,
    confirm_scope_report_id: str,
    producer_run_id: str | None = None,
) -> dict[str, Any]:
    """绑定单个 Task、已持久化的 PASS Change Verify 报告与真实 producer。先核对变更范围和当前输入，再冻结 Check 闭包及 diff；仅在这些条件满足后发布不可变 submission。"""
    repo = Path(root).resolve()
    if confirm_scope_report_id != change_report_id:
        raise SubmissionError(
            "scope-review-confirmation-required",
            "confirmation must equal change report id",
        )
    submitter = _discover_runtime(repo)
    try:
        requirements = load_task_requirements(repo, task_id)
        report, report_descriptor = read_report(repo, change_report_id)
        producer = resolve_producer(repo, requirements, submitter, producer_run_id)
    except (RecordError, ValueError, ProducerError) as exc:
        raise _err(exc) from None
    if (
        report.get("scope") not in {"change-targeted", "development-change"}
        or report.get("result") != "PASS"
    ):
        raise SubmissionError(
            "change-report-not-pass",
            "submit requires persisted PASS change-targeted report",
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
            _subject_paths=tuple(subject["changed_files"]),
        )
        plan = acceptance_plan(repo, assessment, requirements)
    except (RecordError, ValueError) as exc:
        raise _err(exc) from None
    if assessment["changed_files"] != subject["changed_files"]:
        raise SubmissionError(
            "scope-input-drift", "task subject changed during submission"
        )
    frozen = freeze_inputs(
        repo,
        required_check_ids=requirements["required_check_ids"],
        verification_scope=plan["verification_scope"],
        base=assessment["base"],
        _subject_changed_files=(
            subject["changed_files"]
            if plan["verification_scope"] == "development-change"
            else None
        ),
    )
    if frozen.get("result") != "PASS":
        raise SubmissionError(
            "freeze-inputs-failed", str(frozen.get("reason", "unknown"))
        )
    submission_id = str(uuid.uuid4())
    patch = review_patch(repo, assessment["base"], subject["changed_files"])
    try:
        patch_descriptor = publish_bytes(
            repo,
            f"tmp/quality/delivery-gate/submissions/{submission_id}.diff.patch",
            patch,
        )
        created = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        record = {
            "schema_version": "lexiflow.delivery-gate-submission.v5",
            "risk_assessment": assessment,
            "acceptance_plan": plan,
            "submission_id": submission_id,
            "task_requirements": requirements,
            "change_report": report_descriptor,
            "scope_confirmation": confirm_scope_report_id,
            "scope_base": base,
            "diff": patch_descriptor,
            "submitter_identity": submitter["identity"],
            "runtime_proof": submitter["proof"],
            "authority": submitter["authority"],
            "producer": producer,
            "verification_freeze": frozen,
            "changed_file_snapshots": _snapshots(repo, subject["changed_files"]),
            "task_subject": subject,
            "created_at": created,
        }
        from scripts.delivery_gate.acceptance import verify_plan
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
        "producer_actor_id": producer["identity"].get(
            "actor_id", producer["identity"].get("agent_id")
        ),
        "frozen_input_fingerprint": frozen["input_fingerprint"],
    }
