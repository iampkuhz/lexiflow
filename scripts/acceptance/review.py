"""独立 Review 仅消费不可变的冻结证据，不执行交付命令或重做 validation。"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import uuid

from scripts.acceptance.authority import discover_authority, verify_authority
from scripts.acceptance.records import (
    RecordError,
    acceptance_locator,
    content_hash,
    list_layer,
    load_layer,
    load_submission,
    publish_json,
    read_bound_bytes,
)


class ReviewError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _discover_runtime(repo: Path) -> dict[str, Any]:
    return discover_authority(repo, ReviewError)


def _load_submission(repo: Path, submission_id: str) -> dict[str, Any]:
    try:
        return load_submission(repo, submission_id)
    except RecordError as exc:
        raise ReviewError(exc.code, exc.detail) from None


def _load_validation(repo: Path, validation_id: str) -> dict[str, Any]:
    try:
        return load_layer(repo, "validations", validation_id)
    except RecordError as exc:
        raise ReviewError(exc.code, exc.detail) from None


def _verify_independence(
    submission: dict[str, Any], validation: dict[str, Any], runtime: dict[str, Any]
) -> None:
    reviewer = {
        runtime["identity"].get("actor_id"),
        runtime["identity"].get("session_id"),
    }
    subjects = (
        (
            "submitter",
            submission.get("submitter_identity", {}),
            "self-review-forbidden",
        ),
        (
            "producer",
            submission.get("producer", {}).get("identity", {}),
            "self-review-forbidden",
        ),
        (
            "validator",
            validation.get("validator_identity", {}),
            "reviewer-not-independent",
        ),
    )
    for label, identity, code in subjects:
        values = {
            identity.get("actor_id"),
            identity.get("agent_id"),
            identity.get("session_id"),
        } - {None, ""}
        if not values:
            raise ReviewError(f"{label}-identity-invalid", label)
        if reviewer.intersection(values):
            raise ReviewError(code, f"reviewer identity overlaps {label}")


def _verify_validation(
    submission: dict[str, Any], validation: dict[str, Any], repo: Path
) -> None:
    if validation.get("submission_id") != submission.get(
        "submission_id"
    ) or validation.get("submission_content_hash") != submission.get("content_hash"):
        raise ReviewError(
            "validation-submission-mismatch",
            "validation is not bound to this submission",
        )
    if validation.get("frozen_input_fingerprint") != submission.get(
        "verification_freeze", {}
    ).get("input_fingerprint"):
        raise ReviewError(
            "input-drift", "validation frozen input does not match submission"
        )
    authority_error = verify_authority(repo, validation, "validator_identity")
    if authority_error:
        raise ReviewError(authority_error, "validator")
    if validation.get("result") != "PASS":
        raise ReviewError("validation-not-pass", str(validation.get("result")))
    descriptor = validation.get("verification_report")
    if not isinstance(descriptor, dict):
        raise ReviewError("validation-evidence-missing", "report descriptor missing")
    try:
        read_bound_bytes(repo, descriptor["locator"], descriptor["sha256"])
    except (KeyError, RecordError) as exc:
        raise ReviewError(
            getattr(exc, "code", "validation-evidence-invalid"),
            getattr(exc, "detail", "report"),
        ) from None


def _findings(findings: Any, decision: str) -> list[dict[str, Any]]:
    if decision not in {"PASS", "BLOCKED", "FAIL"}:
        raise ReviewError("invalid-decision", str(decision))
    if not isinstance(findings, list) or not findings:
        raise ReviewError("evidence-incomplete", "explicit findings are required")
    ids: set[str] = set()
    severities: set[str] = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or set(finding) != {
            "finding_id",
            "severity",
            "code",
            "evidence",
        }:
            raise ReviewError("evidence-incomplete", f"finding[{index}] schema")
        finding_id, severity, code, evidence = (
            finding["finding_id"],
            finding["severity"],
            finding["code"],
            finding["evidence"],
        )
        if (
            not isinstance(finding_id, str)
            or not finding_id
            or finding_id in ids
            or severity not in {"PASS", "BLOCKED", "FAIL"}
            or not isinstance(code, str)
            or not code
        ):
            raise ReviewError("evidence-incomplete", f"finding[{index}] fields")
        if (
            not isinstance(evidence, list)
            or not evidence
            or not all(
                isinstance(item, dict)
                and isinstance(item.get("detail"), str)
                and item["detail"]
                for item in evidence
            )
        ):
            raise ReviewError("evidence-incomplete", f"finding[{index}] evidence")
        ids.add(finding_id)
        severities.add(severity)
    if decision == "PASS" and severities != {"PASS"}:
        raise ReviewError("decision-mismatch", "PASS requires only PASS findings")
    if decision not in severities:
        raise ReviewError("decision-mismatch", f"{decision} requires matching finding")
    return findings


def review(
    root: str | Path,
    *,
    submission_id: str,
    validation_id: str,
    findings: list[dict[str, Any]],
    decision: str,
) -> dict[str, Any]:
    """记录明确的独立审阅意见。输入为冻结的 submission、validation 与审阅者身份；只发布 review 记录，不运行检查命令。"""
    repo = Path(root).resolve()
    submission = _load_submission(repo, submission_id)
    validation = _load_validation(repo, validation_id)
    from scripts.acceptance.validate import _verify_frozen

    try:
        _verify_frozen(repo, submission)
    except Exception as exc:
        raise ReviewError(
            getattr(exc, "code", "submission-not-current"), str(exc)
        ) from None
    runtime = _discover_runtime(repo)
    _verify_independence(submission, validation, runtime)
    _verify_validation(submission, validation, repo)
    reviewed_findings = _findings(findings, decision)
    try:
        if list_layer(repo, "reviews", "validation_id", validation_id):
            raise ReviewError("review-already-published", validation_id)
    except RecordError as exc:
        raise ReviewError(exc.code, exc.detail) from None
    review_id = str(uuid.uuid4())
    record = {
        "schema_version": "lexiflow.acceptance-review.v3",
        "review_id": review_id,
        "submission_id": submission_id,
        "validation_id": validation_id,
        "submission_content_hash": submission["content_hash"],
        "validation_content_hash": validation["content_hash"],
        "reviewer_identity": runtime["identity"],
        "runtime_proof": runtime["proof"],
        "authority": runtime["authority"],
        "findings": reviewed_findings,
        "result": decision,
        "created_at": datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "delivery_rerun": False,
    }
    try:
        published = publish_json(repo, acceptance_locator("reviews", review_id), record)
    except RecordError as exc:
        raise ReviewError(exc.code, exc.detail) from None
    return {
        "review_id": review_id,
        "submission_id": submission_id,
        "validation_id": validation_id,
        "result": decision,
        "content_hash": content_hash(record),
        "record_sha256": published["sha256"],
        "delivery_rerun": False,
    }
