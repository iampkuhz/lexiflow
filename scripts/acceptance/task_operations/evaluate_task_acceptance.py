"""核验正式 Delivery Gate 条件，并在 check 场景中发布完整成功 receipt。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.acceptance.evidence.acceptance_actor_identity import verify_authority
from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    canonical_bytes,
    content_hash,
    delivery_gate_locator,
    list_submissions,
    load_submission,
    publish_json,
    read_bound_bytes,
    read_json,
    sha256_bytes,
)
from scripts.acceptance.task_definition.task_acceptance_plan import (
    review_required,
    verify_validation_evidence,
)
from scripts.acceptance.task_definition.task_acceptance_requirements import (
    load_task_requirements,
)


class CheckError(ValueError):
    """正式条件核对失败时返回拒绝原因，不重跑交付命令。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _one(
    records: list[dict[str, Any]], kind: str, subject: str
) -> dict[str, Any] | None:
    if not records:
        return None
    if len(records) != 1:
        raise CheckError(
            "ambiguous-receipt", f"{kind} has {len(records)} records for {subject}"
        )
    return records[0]


@dataclass(frozen=True)
class _EvidenceInspection:
    """同一读取阶段一次认证已有层；缺席与非法证据分别表达。"""

    errors: tuple[str, ...]
    complete: bool


def _inspect_evidence(
    repo: Path,
    submission: dict[str, Any],
    validation: dict[str, Any] | None,
    review: dict[str, Any] | None,
    check: dict[str, Any] | None,
) -> _EvidenceInspection:
    from scripts.acceptance.task_operations.record_independent_task_review import (
        ReviewError,
    )
    from scripts.acceptance.task_operations.record_independent_task_review import (
        _verify_independence as reviewer_independence,
    )
    from scripts.acceptance.task_operations.run_independent_task_validation import (
        ValidationError,
    )
    from scripts.acceptance.task_operations.run_independent_task_validation import (
        _verify_independence as validator_independence,
    )

    errors = []
    sid = submission["submission_id"]
    needs_review = review_required(submission)
    if review is not None and not needs_review:
        errors.append("review-not-required")
    for name, record, allowed in (
        ("validation", validation, {"PASS", "BLOCKED", "FAIL"}),
        ("review", review, {"PASS", "BLOCKED", "FAIL"}),
        ("check", check, {"PASS"}),
    ):
        if record is None:
            continue
        if record.get("result") not in allowed:
            errors.append(f"{name}-result-invalid")
        if record.get("submission_id") != sid:
            errors.append(f"{name}-submission-id")
        if record.get("submission_content_hash") != submission["content_hash"]:
            errors.append(f"{name}-submission-hash")
    if validation is not None:
        if (
            validation.get("frozen_input_fingerprint")
            != submission["verification_freeze"]["input_fingerprint"]
        ):
            errors.append("validation-input-fingerprint")
        authority = verify_authority(repo, validation, "validator_identity")
        if authority:
            errors.append(authority)
        try:
            validator_independence(
                submission, {"identity": validation.get("validator_identity", {})}
            )
        except (ValidationError, ValueError) as exc:
            errors.append(getattr(exc, "code", "validation-identity"))
        try:
            verify_validation_evidence(repo, submission, validation)
        except (KeyError, TypeError, RecordError):
            errors.append("validation-evidence-drift")
    if review is not None:
        from scripts.acceptance.task_operations.record_independent_task_review import (
            _finding_result,
            _findings,
        )

        try:
            if _finding_result(_findings(review["findings"])) != review["result"]:
                errors.append("review-findings-result-mismatch")
        except (ReviewError, KeyError, TypeError):
            errors.append("review-findings-invalid")
        if validation is None or (
            review.get("validation_id") != validation["validation_id"]
            or review.get("validation_content_hash") != validation["content_hash"]
        ):
            errors.append("review-binding")
        authority = verify_authority(repo, review, "reviewer_identity")
        if authority:
            errors.append(authority)
        if validation is not None:
            try:
                reviewer_independence(
                    submission,
                    validation,
                    {"identity": review.get("reviewer_identity", {})},
                )
            except (ReviewError, ValueError) as exc:
                errors.append(getattr(exc, "code", "review-identity"))
    complete = (
        validation is not None
        and validation.get("result") == "PASS"
        and (
            (review is not None and review.get("result") == "PASS")
            if needs_review
            else review is None
        )
    )
    if check is not None and (
        not complete
        or check.get("validation_id")
        != (validation["validation_id"] if validation else None)
        or check.get("validation_content_hash")
        != (validation["content_hash"] if validation else None)
        or check.get("review_id") != (review["review_id"] if review else None)
        or check.get("review_content_hash")
        != (review["content_hash"] if review else None)
    ):
        errors.append("check-binding")
    return _EvidenceInspection(tuple(errors), complete)


def _inspect_dependency(
    repo: Path,
    submission: dict[str, Any],
    validation: dict[str, Any] | None,
    review: dict[str, Any] | None,
    check: dict[str, Any] | None,
) -> _EvidenceInspection:
    """认证所有候选的历史来源闭包，不要求历史 subject 等于当前工作树。"""
    from scripts.acceptance.task_operations.run_independent_task_validation import (
        ValidationError,
        _verify_producer,
    )

    inspection = _inspect_evidence(repo, submission, validation, review, check)
    errors = list(inspection.errors)
    authority = verify_authority(repo, submission, "submitter_identity")
    if authority:
        errors.append(authority)
    try:
        _verify_producer(repo, submission)
        if submission.get("release_subject") is None:
            for label in ("change_report", "diff"):
                descriptor = submission[label]
                read_bound_bytes(repo, descriptor["locator"], descriptor["sha256"])
    except (ValidationError, KeyError, RecordError) as exc:
        errors.append(getattr(exc, "code", "dependency-source-invalid"))
    return _EvidenceInspection(tuple(errors), inspection.complete)


@dataclass
class _EvaluationContext:
    """仅供一次求值使用的安全记录索引与已完成依赖节点结果。"""

    submissions: list[dict[str, Any]] | None = None
    layers: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    completed: dict[str, tuple[bool, list[dict[str, Any]]]] = field(
        default_factory=dict
    )
    examined: set[str] = field(default_factory=set)
    evidence: dict[
        str,
        tuple[
            dict[str, Any] | None,
            dict[str, Any] | None,
            dict[str, Any] | None,
            _EvidenceInspection,
        ],
    ] = field(default_factory=dict)
    requirements: dict[str, dict[str, Any]] = field(default_factory=dict)

    def submission_records(self, repo: Path) -> list[dict[str, Any]]:
        if self.submissions is None:
            self.submissions = list_submissions(repo)
        return self.submissions

    def layer_records(self, repo: Path, kind: str) -> dict[str, list[dict[str, Any]]]:
        if kind not in self.layers:
            from scripts.acceptance.evidence.acceptance_record_store import (
                _list_directory,
                load_layer,
            )

            indexed: dict[str, list[dict[str, Any]]] = {}
            for name in _list_directory(
                repo, f"tmp/quality/delivery-gate/current/{kind}"
            ):
                rid = name.removesuffix(".json")
                record = load_layer(repo, kind, rid)
                sid = record.get("submission_id")
                if isinstance(sid, str):
                    indexed.setdefault(sid, []).append(record)
            self.layers[kind] = indexed
        return self.layers[kind]

    def fresh_snapshot(self, repo: Path) -> _EvaluationContext:
        """重新安全读取候选集合和所有 receipt；调用方再重核附件与身份来源。"""
        fresh = _EvaluationContext(submissions=list_submissions(repo))
        for kind in ("validations", "reviews", "checks"):
            fresh.layer_records(repo, kind)
        return fresh


def _records(
    repo: Path, submission: dict[str, Any], context: _EvaluationContext
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    sid = submission["submission_id"]
    return tuple(
        _one(context.layer_records(repo, kind).get(sid, []), kind.rstrip("s"), sid)
        for kind in ("validations", "reviews", "checks")
    )


def _dependency_status(
    repo: Path,
    submission: dict[str, Any],
    visiting: set[str],
    context: _EvaluationContext,
) -> tuple[bool, list[dict[str, Any]]]:
    """只读取当前 Task 的依赖 receipt 并验证哈希链；缺失或不匹配留作条件结果，不执行依赖 Task。"""
    requirements = submission.get("task_requirements", {})
    task_id = requirements.get("task_id", "")
    if task_id in visiting:
        return False, [{"task_id": task_id, "status": "dependency-cycle"}]
    sid = submission.get("submission_id")
    if sid in context.completed:
        return context.completed[sid]
    try:
        current = load_task_requirements(repo, task_id)
    except RecordError as exc:
        return False, [{"task_id": task_id, "status": exc.code}]
    context.requirements[task_id] = current
    if current != requirements:
        return False, [{"task_id": task_id, "status": "task-requirements-drift"}]
    details = []
    for edge in requirements.get("dependencies", []):
        dep_id = edge["task_id"]
        version = edge["required_task_version"]
        change = edge["required_change_version"]
        try:
            source = context.submission_records(repo)
        except RecordError as exc:
            raise CheckError("dependency-record-invalid", str(exc)) from None
        candidates = [
            x
            for x in source
            if x.get("task_requirements", {}).get("task_id") == dep_id
            and x["task_requirements"]["task_version"] == version
            and x["task_requirements"]["change_version"] == change
        ]
        if not candidates:
            details.append({"task_id": dep_id, "status": "missing"})
            continue
        passing = []
        invalid = False
        for dep in candidates:
            dep_sid = dep.get("submission_id")
            cached = context.evidence.get(dep_sid)
            if cached is None:
                context.examined.add(dep_sid)
                try:
                    validation, review, check = _records(repo, dep, context)
                except (RecordError, CheckError):
                    invalid = True
                    break
                inspection = _inspect_dependency(repo, dep, validation, review, check)
                context.evidence[dep_sid] = (validation, review, check, inspection)
            else:
                validation, review, check, inspection = cached
            if inspection.errors:
                invalid = True
                break
            # PASS 是待核验的证据声明；非 PASS 历史保留但不参与成功候选选择。
            if check and check.get("result") == "PASS":
                if not inspection.complete:
                    invalid = True
                    break
                child_ok, child_details = _dependency_status(
                    repo, dep, visiting | {task_id}, context
                )
                expected_nested = check.get("conditions", {}).get("dependency_receipts")
                actual_nested = [
                    {
                        "task_id": x["task_id"],
                        "submission_content_hash": x["submission_content_hash"],
                        "check_content_hash": x["check_content_hash"],
                    }
                    for x in child_details
                    if x.get("status") == "PASS"
                ]
                if not child_ok or expected_nested != actual_nested:
                    invalid = True
                    break
                passing.append((dep, check, child_details))
        if invalid:
            details.append({"task_id": dep_id, "status": "receipt-invalid"})
        elif len(passing) > 1:
            details.append({"task_id": dep_id, "status": "ambiguous"})
        elif not passing:
            details.append({"task_id": dep_id, "status": "not-pass"})
        else:
            dep, check, child_details = passing[0]
            details.append(
                {
                    "task_id": dep_id,
                    "status": "PASS",
                    "task_version": dep["task_requirements"]["task_version"],
                    "change_version": dep["task_requirements"]["change_version"],
                    "submission_content_hash": dep["content_hash"],
                    "check_content_hash": check["content_hash"],
                    "nested": child_details,
                }
            )
    result = (all(x["status"] == "PASS" for x in details), details)
    context.completed[sid] = result
    return result


def _approval(
    repo: Path, submission: dict[str, Any], dependencies: list[dict[str, Any]]
) -> tuple[bool, dict[str, Any]]:
    """核对任务所需的明确批准事实；不把状态文本或缺失记录解释成批准。"""
    requirement = submission["task_requirements"].get("approval_requirement")
    if requirement is None:
        return True, {"required": False}
    gate = requirement.get("previous_gate_id")
    if not isinstance(gate, str) or not re.fullmatch(r"G[1-9][0-9]*", gate):
        return False, {"required": True, "status": "invalid-requirement"}
    locator = f"tmp/quality/delivery-gate/current/approvals/{gate}.json"
    try:
        record = read_json(repo, locator, "user approval")
    except RecordError as exc:
        return False, {"required": True, "status": exc.code, "locator": locator}
    expected = {
        "schema_version",
        "approval_id",
        "gate_id",
        "decision",
        "subject_task_id",
        "subject_task_version",
        "subject_change_version",
        "subject_check_content_hash",
        "statement",
        "approved_at",
        "content_hash",
    }
    dep_pass = next(
        (
            x
            for x in dependencies
            if x.get("status") == "PASS"
            and x.get("task_id") == requirement.get("subject_task_id")
        ),
        None,
    )
    valid = (
        set(record) == expected
        and record.get("schema_version") == "lexiflow.user-approval.v1"
        and record.get("gate_id") == gate
        and record.get("decision") == "APPROVED"
        and isinstance(record.get("statement"), str)
        and bool(record["statement"].strip())
        and dep_pass is not None
        and record.get("subject_task_id") == dep_pass["task_id"]
        and record.get("subject_task_version") == dep_pass["task_version"]
        and record.get("subject_change_version") == dep_pass["change_version"]
        and record.get("subject_check_content_hash") == dep_pass["check_content_hash"]
    )
    return valid, {
        "required": True,
        "status": "PASS" if valid else "approval-mismatch",
        "locator": locator,
        "content_hash": record.get("content_hash"),
    }


def _evaluate_conditions(
    root: str | Path,
    *,
    submission_id: str,
    publish_missing: bool,
    _candidate_directory: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """共享核验；只在显式允许时发布缺失的成功 check。"""
    repo = Path(root).resolve()
    candidate_proof = None
    if _candidate_directory is not None and publish_missing:
        raise ValueError("candidate consumption cannot publish Gate records")
    try:
        submission = load_submission(repo, submission_id)
        # 当前主体字节和来源仍须匹配；此处不执行交付命令。
        from scripts.acceptance.task_operations.run_independent_task_validation import (
            _verify_frozen,
        )

        _verify_frozen(repo, submission)
        context = _EvaluationContext()
        try:
            context.submission_records(repo)
            for kind in ("validations", "reviews", "checks"):
                context.layer_records(repo, kind)
        except RecordError as exc:
            raise CheckError("dependency-record-invalid", str(exc)) from None
        validation, review, existing = _records(repo, submission, context)
        if validation is None:
            raise CheckError("validation-missing", submission_id)
        if review is None and review_required(submission):
            raise CheckError("review-missing", validation["validation_id"])
        if review is not None and not review_required(submission):
            raise CheckError("review-not-required", submission_id)
        if existing is None and not publish_missing:
            raise CheckError("check-missing", submission_id)
        inspection = _inspect_evidence(repo, submission, validation, review, existing)
        chain_errors = list(inspection.errors)
        if not inspection.complete:
            chain_errors.append(
                "validation-not-pass"
                if validation["result"] != "PASS"
                else "review-not-pass"
            )
        dependencies_ok, dependency_details = _dependency_status(
            repo, submission, set(), context
        )
        approval_ok, approval = _approval(repo, submission, dependency_details)
        result = (
            "PASS"
            if not chain_errors and dependencies_ok and approval_ok
            else "FAIL"
            if chain_errors
            else "BLOCKED"
        )
        reason = (
            ""
            if result == "PASS"
            else (
                chain_errors[0]
                if chain_errors
                else (
                    "dependency-not-pass"
                    if not dependencies_ok
                    else "user-approval-missing"
                )
            )
        )
        if result == "PASS":
            candidate_snapshot = None
            if _candidate_directory is not None:
                from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import (
                    _prove_candidate,
                )
                from scripts.acceptance.evidence.verify_existing_acceptance_chain import (
                    _chain_proof,
                )

                candidate_snapshot = _prove_candidate(
                    repo,
                    _chain_proof(
                        {
                            "submission": submission,
                            "validation": validation,
                            "review": review,
                            "check": existing,
                        }
                    ),
                    _candidate_directory,
                )
            # 结果交付边界重新读取目录、receipt、附件和身份来源；不得将索引视为新鲜度证明。
            fresh_submission = load_submission(repo, submission_id)
            if fresh_submission != submission:
                raise CheckError(
                    "current-input-invalid", "submission drift during evaluation"
                )
            fresh_context = context.fresh_snapshot(repo)
            fresh_validation, fresh_review, fresh_check = _records(
                repo, fresh_submission, fresh_context
            )
            if (
                fresh_context.submissions != context.submissions
                or fresh_context.layers != context.layers
            ):
                raise CheckError(
                    "current-input-invalid", "evidence drift during evaluation"
                )
            from scripts.acceptance.task_operations.run_independent_task_validation import (
                _verify_frozen,
            )

            _verify_frozen(repo, fresh_submission)
            if (
                fresh_validation is None
                or (
                    fresh_inspection := _inspect_evidence(
                        repo, fresh_submission, fresh_validation, fresh_review, existing
                    )
                ).errors
                or not fresh_inspection.complete
            ):
                raise CheckError("current-input-invalid", "target evidence drift")
            # 目录快照相同仍需重读依赖链使用的附件及原生 authority 来源；复用新快照索引，
            # 不再次递归扫描目录或重复遍历共享子图。
            for candidate in fresh_context.submissions or []:
                if candidate.get("submission_id") not in context.examined:
                    continue
                val, rev, chk = _records(repo, candidate, fresh_context)
                inspection = _inspect_dependency(repo, candidate, val, rev, chk)
                if inspection.errors or (chk is not None and not inspection.complete):
                    raise CheckError("current-input-invalid", "evidence source drift")
            # 记录全集及每条已有层相同，拓扑和唯一 PASS 选择不会改变；
            # 当前 Catalog 要求、附件与 actor 来源独立重核后无需再遍历同一 DAG。
            for task_id, requirements in context.requirements.items():
                if load_task_requirements(repo, task_id) != requirements:
                    raise CheckError(
                        "current-input-invalid", "dependency requirements drift"
                    )
            fresh_approval_ok, fresh_approval = _approval(
                repo, fresh_submission, dependency_details
            )
            if (
                fresh_validation != validation
                or fresh_review != review
                or not fresh_approval_ok
                or fresh_approval != approval
            ):
                raise CheckError("current-input-invalid", "dependency evidence drift")
            if fresh_check != existing:
                raise CheckError("current-input-invalid", "check candidate set drift")
            if candidate_snapshot is not None:
                from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import (
                    _recheck_candidate,
                )

                candidate_proof = _recheck_candidate(
                    repo,
                    _chain_proof(
                        {
                            "submission": fresh_submission,
                            "validation": fresh_validation,
                            "review": fresh_review,
                            "check": fresh_check,
                        }
                    ),
                    candidate_snapshot,
                )
    except RecordError as exc:
        return {
            "submission_id": submission_id,
            "result": "BLOCKED",
            "reason": exc.code,
            "detail": exc.detail,
            "delivery_rerun": False,
        }, None
    except CheckError as exc:
        return {
            "submission_id": submission_id,
            "result": "BLOCKED",
            "reason": exc.code,
            "detail": exc.detail,
            "delivery_rerun": False,
        }, None
    except Exception as exc:  # noqa: BLE001 - convert unexpected evidence failures to BLOCKED
        return {
            "submission_id": submission_id,
            "result": "BLOCKED",
            "reason": getattr(exc, "code", "current-input-invalid"),
            "detail": str(exc),
            "delivery_rerun": False,
        }, None
    receipts = [
        {
            "task_id": x["task_id"],
            "submission_content_hash": x["submission_content_hash"],
            "check_content_hash": x["check_content_hash"],
        }
        for x in dependency_details
        if x["status"] == "PASS"
    ]
    conditions = {
        "review_required": review_required(submission),
        "hash_dag_errors": chain_errors,
        "dependencies_ok": dependencies_ok,
        "dependency_receipts": receipts,
        "dependency_details": dependency_details,
        "user_approval": approval,
    }
    if result != "PASS":
        return {
            "submission_id": submission_id,
            "result": result,
            "reason": reason,
            "conditions": conditions,
            "published": False,
            "delivery_rerun": False,
        }, None
    if existing is not None:
        expected = {
            "submission_id": submission_id,
            "validation_id": validation["validation_id"],
            "review_id": review["review_id"] if review else None,
            "submission_content_hash": submission["content_hash"],
            "validation_content_hash": validation["content_hash"],
            "review_content_hash": review["content_hash"] if review else None,
            "result": "PASS",
            "reason": "",
            "conditions": conditions,
            "delivery_rerun": False,
        }
        if (
            set(existing)
            != set(expected)
            | {"schema_version", "check_id", "created_at", "content_hash"}
            or existing.get("schema_version") != "lexiflow.delivery-gate-check.v5"
            or any(existing.get(k) != v for k, v in expected.items())
        ):
            return {
                "submission_id": submission_id,
                "result": "BLOCKED",
                "reason": "existing-check-mismatch",
                "published": False,
                "delivery_rerun": False,
            }, None
        return {
            "check_id": existing["check_id"],
            "submission_id": submission_id,
            "result": "PASS",
            "reason": "",
            "content_hash": existing["content_hash"],
            "record_sha256": sha256_bytes(canonical_bytes(existing)),
            "conditions": conditions,
            "published": True,
            "idempotent": True,
            "delivery_rerun": False,
        }, {
            "submission": submission,
            "validation": validation,
            "review": review,
            "check": existing,
            **(
                {"candidate_proof": candidate_proof}
                if candidate_proof is not None
                else {}
            ),
        }
    if not publish_missing:
        return {
            "submission_id": submission_id,
            "result": "BLOCKED",
            "reason": "check-missing",
            "published": False,
            "delivery_rerun": False,
        }, None
    check_id = str(uuid.uuid4())
    record = {
        "schema_version": "lexiflow.delivery-gate-check.v5",
        "check_id": check_id,
        "submission_id": submission_id,
        "validation_id": validation["validation_id"],
        "review_id": review["review_id"] if review else None,
        "submission_content_hash": submission["content_hash"],
        "validation_content_hash": validation["content_hash"],
        "review_content_hash": review["content_hash"] if review else None,
        "result": "PASS",
        "reason": "",
        "conditions": conditions,
        "delivery_rerun": False,
        "created_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
    }
    try:
        published = publish_json(
            repo, delivery_gate_locator("checks", check_id), record
        )
    except RecordError as exc:
        raise CheckError(exc.code, exc.detail) from None
    return {
        "check_id": check_id,
        "submission_id": submission_id,
        "result": "PASS",
        "reason": "",
        "content_hash": content_hash(record),
        "record_sha256": published["sha256"],
        "conditions": conditions,
        "published": True,
        "idempotent": False,
        "delivery_rerun": False,
    }, None


def check_conditions(root: str | Path, *, submission_id: str) -> dict[str, Any]:
    """核验并在完整条件通过时发布 check receipt；不运行交付命令。"""
    result, _ = _evaluate_conditions(
        root, submission_id=submission_id, publish_missing=True
    )
    return result
