"""将 Catalog Task 与代码变更报告或固定发行候选绑定为不可变 submission。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.acceptance.evidence.acceptance_actor_identity import discover_authority
from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    content_hash,
    delivery_gate_locator,
    publish_bytes,
    publish_json,
)
from scripts.acceptance.evidence.implementation_run_evidence import (
    ProducerError,
    producer_identity,
    resolve_producer,
)
from scripts.acceptance.evidence.task_review_diff import review_patch
from scripts.acceptance.task_definition.task_acceptance_plan import acceptance_plan
from scripts.acceptance.task_definition.task_acceptance_requirements import (
    load_task_requirements,
)
from scripts.acceptance.task_definition.task_acceptance_scope import (
    derive_task_subject,
    subject_paths,
)
from scripts.verification import freeze_inputs, read_report
from scripts.verification.planning.change_risk_assessment import assess
from scripts.verification.planning.check_scope_selection import changed_paths


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
    change_report_id: str | None = None,
    release_handoff: bool = False,
    producer_run_id: str | None = None,
) -> dict[str, Any]:
    """绑定单个 Task、互斥的代码／发行主体与真实 producer；重核来源和输入后冻结现有检查计划。"""
    repo = Path(root).resolve()
    if not isinstance(release_handoff, bool) or (
        release_handoff and change_report_id is not None
    ):
        raise SubmissionError(
            "submit-mode-conflict", "choose exactly one submission mode"
        )
    submitter = _discover_runtime(repo)
    try:
        requirements = load_task_requirements(repo, task_id)
        producer = resolve_producer(repo, requirements, producer_run_id)
        if release_handoff:
            report = report_descriptor = None
            from scripts.verification.planning.check_definitions import (
                load_declarations_snapshot,
            )

            declarations, _ = load_declarations_snapshot(repo)
            formal = set(
                declarations["scenario_policy"]["ci_quick"]["formal_only_check_ids"]
            )
            if not set(requirements["required_check_ids"]) & formal:
                raise SubmissionError(
                    "release-task-not-formal",
                    "Task does not require a formal-only Check",
                )
            from scripts.acceptance.task_definition.release_subject import (
                read_release_subject,
            )

            subject = read_release_subject(repo)
        else:
            if not change_report_id:
                raise SubmissionError(
                    "change-report-required",
                    "ordinary submission requires a change report",
                )
            report, report_descriptor = read_report(repo, change_report_id)
            subject = None
    except (RecordError, ValueError, ProducerError) as exc:
        raise _err(exc) from None
    if release_handoff:
        try:
            assessment = {
                "schema_version": "lexiflow.release-risk-assessment.v1",
                "level": "formal-release",
                "required_check_ids": requirements["required_check_ids"],
                "coverage_gaps": [],
                "subject_hash": subject["handoff"]["sha256"],
            }
            plan = acceptance_plan(repo, assessment, requirements)
            frozen = freeze_inputs(
                repo,
                required_check_ids=requirements["required_check_ids"],
                verification_scope="repository-baseline",
            )
            if frozen.get("result") != "PASS":
                raise SubmissionError(
                    "freeze-inputs-failed", str(frozen.get("reason", "unknown"))
                )
            submission_id = str(uuid.uuid4())
            created = (
                datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            )
            record = {
                "schema_version": "lexiflow.delivery-gate-submission.v7",
                "risk_assessment": assessment,
                "acceptance_plan": plan,
                "submission_id": submission_id,
                "task_requirements": requirements,
                "change_report": None,
                "diff": None,
                "submitter_identity": submitter["identity"],
                "runtime_proof": submitter["proof"],
                "authority": submitter["authority"],
                "producer": producer,
                "verification_freeze": frozen,
                "task_subject": None,
                "release_subject": subject,
                "created_at": created,
            }
            from scripts.acceptance.task_definition.task_acceptance_plan import (
                verify_plan,
            )
            from scripts.verification import verify_frozen_inputs

            verify_plan(repo, record)
            if not verify_frozen_inputs(repo, frozen):
                raise SubmissionError(
                    "frozen-input-drift", "input changed before publication"
                )
            published = publish_json(
                repo, delivery_gate_locator("submissions", submission_id), record
            )
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
        except (RecordError, ValueError) as exc:
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
    except Exception as exc:  # noqa: BLE001 - fail closed for unavailable or invalid Git scope input
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
            "schema_version": "lexiflow.delivery-gate-submission.v7",
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
            "release_subject": None,
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
