"""核验正式 Delivery Gate 条件，并在 check 场景中发布完整成功 receipt。"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.delivery_gate.acceptance import (
    binding,
    review_required,
    verify_validation_evidence,
)
from scripts.delivery_gate.authority import verify_authority
from scripts.delivery_gate.records import (
    RecordError,
    canonical_bytes,
    content_hash,
    delivery_gate_locator,
    list_layer,
    list_submissions,
    load_submission,
    publish_json,
    read_json,
    sha256_bytes,
)
from scripts.delivery_gate.requirements import load_task_requirements


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


def _bound_chain(
    repo: Path,
    submission: dict[str, Any],
    validation: dict[str, Any],
    review: dict[str, Any] | None,
) -> list[str]:
    errors = []
    if validation.get("submission_content_hash") != submission.get("content_hash"):
        errors.append("validation-submission-hash")
    if validation.get("submission_id") != submission.get("submission_id"):
        errors.append("validation-submission-id")
    if review_required(submission) != (review is not None):
        errors.append("review-layer-mismatch")
    if review is not None:
        if review.get("submission_id") != submission.get("submission_id"):
            errors.append("review-submission-id")
        if review.get("validation_id") != validation.get("validation_id"):
            errors.append("review-validation-id")
        if review.get("submission_content_hash") != submission.get("content_hash"):
            errors.append("review-submission-hash")
        if review.get("validation_content_hash") != validation.get("content_hash"):
            errors.append("review-validation-hash")
    for record in (validation, review):
        if record is not None and any(
            record.get(k) != v for k, v in binding(submission).items()
        ):
            errors.append("acceptance-binding-mismatch")
    if validation.get("frozen_input_fingerprint") != submission.get(
        "verification_freeze", {}
    ).get("input_fingerprint"):
        errors.append("validation-input-fingerprint")
    for record, key in (
        (validation, "validator_identity"),
        (review, "reviewer_identity"),
    ):
        if record is None:
            continue
        error = verify_authority(repo, record, key)
        if error:
            errors.append(error)
    # 复用签发阶段同一独立性合同；此处仅消费记录身份，不发现当前调用者身份。
    from scripts.delivery_gate.review import (
        ReviewError,
    )
    from scripts.delivery_gate.review import (
        _verify_independence as verify_reviewer_independence,
    )
    from scripts.delivery_gate.validate import (
        ValidationError,
    )
    from scripts.delivery_gate.validate import (
        _verify_independence as verify_validator_independence,
    )

    try:
        verify_validator_independence(
            submission, {"identity": validation.get("validator_identity", {})}
        )
    except ValidationError as exc:
        errors.append(exc.code)
    if review is not None:
        try:
            verify_reviewer_independence(
                submission,
                validation,
                {"identity": review.get("reviewer_identity", {})},
            )
        except ReviewError as exc:
            errors.append(exc.code)
    try:
        verify_validation_evidence(repo, submission, validation)
    except (KeyError, TypeError, RecordError):
        errors.append("validation-evidence-drift")
    if validation.get("result") != "PASS":
        errors.append("validation-not-pass")
    if review is not None and review.get("result") != "PASS":
        errors.append("review-not-pass")
    return errors


def _existing_evidence_errors(
    repo: Path,
    submission: dict[str, Any],
    validation: dict[str, Any] | None,
    review: dict[str, Any] | None,
    check: dict[str, Any] | None,
) -> list[str]:
    """校验候选已存在 receipt 的身份、绑定和附件，不要求未完成链补齐缺失层。"""
    errors = []
    sid = submission.get("submission_id")
    needs_review = review_required(submission)
    if review is not None and not needs_review:
        errors.append("review-not-required")
    for record in (validation, review, check):
        if record is not None and any(
            record.get(k) != v for k, v in binding(submission).items()
        ):
            errors.append("acceptance-binding-mismatch")
    # 历史失败只能使用协议内的结果；check 只发布成功终态，不能伪装失败来退出选择。
    for name, record, allowed in (
        ("validation", validation, {"PASS", "BLOCKED", "FAIL"}),
        ("review", review, {"PASS", "BLOCKED", "FAIL"}),
        ("check", check, {"PASS"}),
    ):
        if record is not None and record.get("result") not in allowed:
            errors.append(f"{name}-result-invalid")
    if validation is not None:
        if validation.get("submission_id") != sid:
            errors.append("validation-submission-id")
        if validation.get("submission_content_hash") != submission.get("content_hash"):
            errors.append("validation-submission-hash")
        if validation.get("frozen_input_fingerprint") != submission.get(
            "verification_freeze", {}
        ).get("input_fingerprint"):
            errors.append("validation-input-fingerprint")
        if verify_authority(repo, validation, "validator_identity"):
            errors.append("validation-identity")
        try:
            verify_validation_evidence(repo, submission, validation)
        except (KeyError, TypeError, RecordError):
            errors.append("validation-evidence-drift")
    if review is not None:
        if (
            validation is None
            or review.get("submission_id") != sid
            or review.get("submission_content_hash") != submission.get("content_hash")
            or review.get("validation_id") != validation.get("validation_id")
            or review.get("validation_content_hash") != validation.get("content_hash")
        ):
            errors.append("review-binding")
        if verify_authority(repo, review, "reviewer_identity"):
            errors.append("review-identity")
    if check is not None and (
        check.get("submission_id") != sid
        or check.get("submission_content_hash") != submission.get("content_hash")
        or (validation is None and check.get("result") == "PASS")
        or (needs_review and review is None and check.get("result") == "PASS")
        or (
            review is None
            and (
                check.get("review_id") is not None
                or check.get("review_content_hash") is not None
            )
        )
        or (
            validation is not None
            and (
                check.get("validation_id") != validation.get("validation_id")
                or check.get("validation_content_hash")
                != validation.get("content_hash")
            )
        )
        or (
            review is not None
            and (
                check.get("review_id") != review.get("review_id")
                or check.get("review_content_hash") != review.get("content_hash")
            )
        )
    ):
        errors.append("check-binding")
    return errors


def _submissions(
    repo: Path, task_id: str, version: int | None, change: str | None
) -> list[dict[str, Any]]:
    result = []
    try:
        submissions = list_submissions(repo)
    except RecordError as exc:
        raise CheckError("dependency-record-invalid", str(exc)) from None
    for submission in submissions:
        req = submission.get("task_requirements", {})
        if (
            req.get("task_id") == task_id
            and (version is None or req.get("task_version") == version)
            and (change is None or req.get("change_version") == change)
        ):
            result.append(submission)
    return result


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
            dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, bool
        ],
    ] = field(default_factory=dict)
    bound: dict[str, bool] = field(default_factory=dict)

    def submission_records(self, repo: Path) -> list[dict[str, Any]]:
        if self.submissions is None:
            self.submissions = list_submissions(repo)
        return self.submissions

    def layer_records(self, repo: Path, kind: str) -> dict[str, list[dict[str, Any]]]:
        if kind not in self.layers:
            from scripts.delivery_gate.records import _list_directory, load_layer

            indexed: dict[str, list[dict[str, Any]]] = {}
            for name in _list_directory(repo, f"tmp/quality/delivery-gate/{kind}"):
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
    repo: Path, submission: dict[str, Any], context: _EvaluationContext | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    sid = submission["submission_id"]
    if context is None:
        by_kind = {kind: None for kind in ("validations", "reviews", "checks")}
    else:
        by_kind = {
            kind: context.layer_records(repo, kind)
            for kind in ("validations", "reviews", "checks")
        }

    def records(kind):
        if context is None:
            return list_layer(repo, kind, "submission_id", sid)
        return by_kind[kind].get(sid, [])

    validation = _one(records("validations"), "validation", sid)
    review = _one(records("reviews"), "review", sid)
    check = _one(records("checks"), "check", sid)
    return validation, review, check


def _dependency_status(
    repo: Path,
    submission: dict[str, Any],
    visiting: set[str] | None = None,
    context: _EvaluationContext | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """只读取当前 Task 的依赖 receipt 并验证哈希链；缺失或不匹配留作条件结果，不执行依赖 Task。"""
    visiting = set() if visiting is None else visiting
    requirements = submission.get("task_requirements", {})
    task_id = requirements.get("task_id", "")
    if task_id in visiting:
        return False, [{"task_id": task_id, "status": "dependency-cycle"}]
    sid = submission.get("submission_id")
    if context is not None and sid not in visiting and sid in context.completed:
        return context.completed[sid]
    try:
        current = load_task_requirements(repo, task_id)
    except RecordError as exc:
        return False, [{"task_id": task_id, "status": exc.code}]
    if current != requirements:
        return False, [{"task_id": task_id, "status": "task-requirements-drift"}]
    details = []
    for edge in requirements.get("dependencies", []):
        dep_id = edge["task_id"] if isinstance(edge, dict) else edge
        version = edge.get("required_task_version") if isinstance(edge, dict) else None
        change = edge.get("required_change_version") if isinstance(edge, dict) else None
        try:
            source = (
                context.submission_records(repo) if context else list_submissions(repo)
            )
        except RecordError as exc:
            raise CheckError("dependency-record-invalid", str(exc)) from None
        candidates = [
            x
            for x in source
            if x.get("task_requirements", {}).get("task_id") == dep_id
            and (
                version is None
                or x.get("task_requirements", {}).get("task_version") == version
            )
            and (
                change is None
                or x.get("task_requirements", {}).get("change_version") == change
            )
        ]
        if not candidates:
            details.append({"task_id": dep_id, "status": "missing"})
            continue
        passing = []
        invalid = False
        for dep in candidates:
            dep_sid = dep.get("submission_id")
            cached = (
                context.evidence.get(dep_sid)
                if context is not None and isinstance(dep_sid, str)
                else None
            )
            if cached is None:
                if context is not None and isinstance(dep_sid, str):
                    context.examined.add(dep_sid)
                try:
                    validation, review, check = _records(repo, dep, context)
                except (RecordError, CheckError):
                    invalid = True
                    break
                evidence_valid = not _existing_evidence_errors(
                    repo, dep, validation, review, check
                )
                if context is not None and isinstance(dep_sid, str):
                    context.evidence[dep_sid] = (
                        validation,
                        review,
                        check,
                        evidence_valid,
                    )
            else:
                validation, review, check, evidence_valid = cached
            if not evidence_valid:
                invalid = True
                break
            # PASS 是待核验的证据声明；非 PASS 历史保留但不参与成功候选选择。
            if check and check.get("result") == "PASS":
                if not validation or (review_required(dep) and not review):
                    invalid = True
                    break
                child_ok, child_details = _dependency_status(
                    repo, dep, visiting | {task_id}, context
                )
                if (
                    context is not None
                    and isinstance(dep_sid, str)
                    and dep_sid in context.bound
                ):
                    current_errors = (
                        [] if context.bound[dep_sid] else ["bound-chain-invalid"]
                    )
                else:
                    current_errors = _bound_chain(repo, dep, validation, review)
                    if context is not None and isinstance(dep_sid, str):
                        context.bound[dep_sid] = not current_errors
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
                if current_errors or not child_ok or expected_nested != actual_nested:
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
    if context is not None and isinstance(sid, str):
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
    locator = f"tmp/quality/delivery-gate/approvals/{gate}.json"
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
    root: str | Path, *, submission_id: str, publish_missing: bool
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """共享核验；只在显式允许时发布缺失的成功 check。"""
    repo = Path(root).resolve()
    try:
        submission = load_submission(repo, submission_id)
        # 当前主体字节和来源仍须匹配；此处不执行交付命令。
        from scripts.delivery_gate.validate import _verify_frozen

        _verify_frozen(repo, submission)
        validation = _one(
            list_layer(repo, "validations", "submission_id", submission_id),
            "validation",
            submission_id,
        )
        if validation is None:
            raise CheckError("validation-missing", submission_id)
        review = _one(
            list_layer(repo, "reviews", "submission_id", submission_id),
            "review",
            submission_id,
        )
        if review is None and review_required(submission):
            raise CheckError("review-missing", validation["validation_id"])
        if review is not None and not review_required(submission):
            raise CheckError("review-not-required", submission_id)
        existing = _one(
            list_layer(repo, "checks", "submission_id", submission_id),
            "check",
            submission_id,
        )
        if existing is None and not publish_missing:
            raise CheckError("check-missing", submission_id)
        chain_errors = _bound_chain(repo, submission, validation, review)
        context = _EvaluationContext()
        # Capture the complete candidate universe before traversing; dependencies may be empty.
        try:
            context.submission_records(repo)
            for kind in ("validations", "reviews", "checks"):
                context.layer_records(repo, kind)
        except RecordError as exc:
            raise CheckError("dependency-record-invalid", str(exc)) from None
        dependencies_ok, dependency_details = _dependency_status(
            repo, submission, context=context
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
            # 结果交付边界重新读取目录、receipt、附件和身份来源；不得将索引视为新鲜度证明。
            fresh_submission = load_submission(repo, submission_id)
            if fresh_submission != submission:
                raise CheckError(
                    "current-input-invalid", "submission drift during evaluation"
                )
            fresh_validation = _one(
                list_layer(repo, "validations", "submission_id", submission_id),
                "validation",
                submission_id,
            )
            fresh_review = _one(
                list_layer(repo, "reviews", "submission_id", submission_id),
                "review",
                submission_id,
            )
            fresh_context = context.fresh_snapshot(repo)
            if (
                fresh_context.submissions != context.submissions
                or fresh_context.layers != context.layers
            ):
                raise CheckError(
                    "current-input-invalid", "evidence drift during evaluation"
                )
            from scripts.delivery_gate.validate import _verify_frozen

            _verify_frozen(repo, fresh_submission)
            if fresh_validation is None or _bound_chain(
                repo, fresh_submission, fresh_validation, fresh_review
            ):
                raise CheckError("current-input-invalid", "target evidence drift")
            # 目录快照相同仍需重读依赖链使用的附件及原生 authority 来源；复用新快照索引，
            # 不再次递归扫描目录或重复遍历共享子图。
            for candidate in fresh_context.submissions or []:
                if candidate.get("submission_id") not in context.examined:
                    continue
                val, rev, chk = _records(repo, candidate, fresh_context)
                if _existing_evidence_errors(repo, candidate, val, rev, chk):
                    raise CheckError("current-input-invalid", "evidence source drift")
                if (
                    chk is not None
                    and chk.get("result") == "PASS"
                    and (val is None or _bound_chain(repo, candidate, val, rev))
                ):
                    raise CheckError(
                        "current-input-invalid", "PASS evidence source drift"
                    )
            fresh_dependencies_ok, fresh_dependency_details = _dependency_status(
                repo, fresh_submission, context=fresh_context
            )
            fresh_approval_ok, _ = _approval(
                repo, fresh_submission, fresh_dependency_details
            )
            if (
                fresh_validation != validation
                or fresh_review != review
                or not fresh_dependencies_ok
                or not fresh_approval_ok
                or fresh_dependency_details != dependency_details
            ):
                raise CheckError("current-input-invalid", "dependency evidence drift")
            fresh_check = _one(
                fresh_context.layer_records(repo, "checks").get(submission_id, []),
                "check",
                submission_id,
            )
            if fresh_check != existing:
                raise CheckError("current-input-invalid", "check candidate set drift")
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
    except Exception as exc:
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
            **binding(submission),
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
            or existing.get("schema_version") != "lexiflow.delivery-gate-check.v4"
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
        "schema_version": "lexiflow.delivery-gate-check.v4",
        **binding(submission),
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
