"""使用 Verification 公开 API 对不可变 submission 执行独立 validation。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.acceptance.evidence.acceptance_actor_identity import (
    discover_authority,
    verify_authority,
)
from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    canonical_bytes,
    content_hash,
    delivery_gate_locator,
    list_layer,
    load_submission,
    publish_bytes,
    publish_json,
    read_bound_bytes,
)
from scripts.acceptance.evidence.implementation_run_evidence import (
    ProducerError,
    producer_identity,
)
from scripts.acceptance.task_definition.task_acceptance_plan import verify_plan
from scripts.verification import verify_frozen_inputs, verify_profiles

_MAXIMUM_SUBMISSIONS = 16


class ValidationError(ValueError):
    """独立验证身份、冻结输入或执行报告不可信时返回错误代码。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _discover_runtime(repo: Path) -> dict[str, Any]:
    return discover_authority(repo, ValidationError)


def _identity_values(identity: dict[str, Any]) -> set[str]:
    # Session 表示宿主路由；不同原生子代理可以共享它，但不能共享执行者身份。
    return {
        x
        for x in (
            identity.get("actor_id"),
            identity.get("agent_id"),
        )
        if isinstance(x, str) and x
    }


def _verify_independence(submission: dict[str, Any], runtime: dict[str, Any]) -> None:
    try:
        implementation_identity = producer_identity(submission)
    except ProducerError as exc:
        raise ValidationError(exc.code, exc.detail) from None
    validator = _identity_values(runtime["identity"])
    if not validator:
        raise ValidationError("validator-identity-invalid", "native actor is missing")
    subjects = []
    for label, identity in (
        ("submitter", submission.get("submitter_identity")),
        ("producer", implementation_identity),
    ):
        if not isinstance(identity, dict) or not _identity_values(identity):
            raise ValidationError(f"{label}-identity-invalid", label)
        subjects.extend(_identity_values(identity))
    if validator.intersection(subjects):
        raise ValidationError(
            "self-validation-forbidden",
            "validator identity overlaps producer/submitter",
        )


def _verify_producer(repo: Path, submission: dict[str, Any]) -> None:
    producer = submission.get("producer")
    if not isinstance(producer, dict):
        raise ValidationError("producer-source-invalid", "producer missing")
    if producer.get("kind") == "current-codex-task":
        if set(producer) != {"kind"}:
            raise ValidationError(
                "producer-source-invalid", "Main producer fields are not exact"
            )
        # 同记录 submitter authority 已由 _verify_frozen 认证；不是另一个来源。
    elif producer.get("kind") == "codex-work-package":
        from scripts.acceptance.evidence.implementation_run_evidence import (
            ProducerError,
            resolve_producer,
        )

        desc = producer.get("completion", {})
        try:
            read_bound_bytes(repo, desc["locator"], desc["sha256"])
            current = resolve_producer(
                repo, submission["task_requirements"], producer["run_id"]
            )
            if current != producer:
                raise ValueError("delegated producer binding drifted")
        except (KeyError, RecordError, ProducerError, ValueError) as exc:
            raise ValidationError("producer-source-drift", str(exc)) from None
    else:
        raise ValidationError("producer-source-invalid", str(producer.get("kind")))


def _verify_frozen(repo: Path, submission: dict[str, Any]) -> dict[str, Any]:
    auth = verify_authority(repo, submission, "submitter_identity")
    if auth:
        raise ValidationError(auth, "submission")
    _verify_producer(repo, submission)
    freeze = submission.get("verification_freeze")
    if not isinstance(freeze, dict) or freeze.get("result") != "PASS":
        raise ValidationError(
            "frozen-inputs-invalid", "submission has no complete verification freeze"
        )
    verify_plan(repo, submission)
    if not verify_frozen_inputs(repo, freeze):
        raise ValidationError(
            "frozen-input-drift",
            "input closure changed",
        )
    if submission.get("release_subject") is None:
        for label in ("change_report", "diff"):
            desc = submission.get(label)
            try:
                read_bound_bytes(repo, desc["locator"], desc["sha256"])
            except (KeyError, TypeError, RecordError):
                raise ValidationError("frozen-artifact-drift", label) from None
    return freeze


def _gaps(report: dict[str, Any]) -> list[dict[str, str]]:
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        return [{"check_id": "verification", "kind": "not-run", "reason": "no-checks"}]
    values = []
    for check in checks:
        if not isinstance(check, dict):
            values.append(
                {"check_id": "unknown", "kind": "invalid", "reason": "non-object"}
            )
            continue
        status = check.get("status")
        process = check.get("process", {})
        if status != "PASS":
            values.append(
                {
                    "check_id": str(check.get("check_id", "unknown")),
                    "kind": str(status or "invalid").lower(),
                    "reason": str(check.get("reason", "not-pass")),
                }
            )
        if not isinstance(process, dict) or process.get("exit_reason") in {
            "not-run",
            "skipped",
            "queued",
            "unavailable",
        }:
            values.append(
                {
                    "check_id": str(check.get("check_id", "unknown")),
                    "kind": "not-run",
                    "reason": (
                        str(process.get("exit_reason", "missing"))
                        if isinstance(process, dict)
                        else "invalid"
                    ),
                }
            )
    for gap in report.get("coverage_gaps", []):
        values.append(
            {"check_id": str(gap), "kind": "coverage-gap", "reason": str(gap)}
        )
    return values


def _publish_validation(
    repo: Path,
    submission_id: str,
    submission: dict[str, Any],
    runtime: dict[str, Any],
    freeze: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    gaps = _gaps(report)
    result = (
        "PASS"
        if report.get("result") == "PASS" and not gaps
        else ("FAIL" if report.get("result") == "FAIL" else "BLOCKED")
    )
    validation_id = str(uuid.uuid4())
    created = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        descriptor = publish_bytes(
            repo,
            f"tmp/quality/delivery-gate/current/validations/{validation_id}/report.json",
            canonical_bytes(report),
        )
        record = {
            "schema_version": "lexiflow.delivery-gate-validation.v5",
            "validation_id": validation_id,
            "submission_id": submission_id,
            "submission_content_hash": submission["content_hash"],
            "validator_identity": runtime["identity"],
            "runtime_proof": runtime["proof"],
            "authority": runtime["authority"],
            "verification_report": descriptor,
            "frozen_input_fingerprint": freeze["input_fingerprint"],
            "gaps": gaps,
            "result": result,
            "created_at": created,
        }
        published = publish_json(
            repo, delivery_gate_locator("validations", validation_id), record
        )
    except RecordError as exc:
        raise ValidationError(exc.code, exc.detail) from None
    return {
        "validation_id": validation_id,
        "submission_id": submission_id,
        "result": result,
        "content_hash": content_hash(record),
        "record_sha256": published["sha256"],
        "gaps": gaps,
    }


def _bind_terminal_report(
    report: dict[str, Any], freeze: dict[str, Any]
) -> dict[str, Any]:
    """让未启动 profile 的终态报告仍绑定当前 Task 计划，并明确所有 Check 未运行。"""
    if report.get("frozen_input_fingerprint") == freeze.get("input_fingerprint"):
        return report
    if report.get("frozen_input_fingerprint") not in (None, ""):
        raise ValidationError(
            "frozen-input-mismatch", "verification did not consume submitted closure"
        )
    checks = report.get("checks")
    if not checks:
        checks = [
            {
                "check_id": check["check_id"],
                "status": "BLOCKED",
                "reason": report.get("reason", "profile-not-run"),
                "process": {"executed_argv": [], "exit_reason": "not-run"},
            }
            for check in freeze.get("checks", [])
        ]
    return {
        **report,
        "scope": freeze["verification_scope"],
        "checks": checks,
        "frozen_input_fingerprint": freeze["input_fingerprint"],
        "required_check_ids": freeze["required_check_ids"],
        "coverage_gaps": report.get("coverage_gaps")
        or [check["check_id"] for check in freeze.get("checks", [])],
    }


def validate_batch(
    root: str | Path, *, submission_ids: list[str]
) -> list[dict[str, Any]]:
    """在单一调用中预检并顺序验证 1 至 16 个互异 submission。"""
    repo = Path(root).resolve()
    if (
        not isinstance(submission_ids, list)
        or not 1 <= len(submission_ids) <= _MAXIMUM_SUBMISSIONS
        or any(not isinstance(value, str) or not value for value in submission_ids)
        or len(set(submission_ids)) != len(submission_ids)
    ):
        raise ValidationError(
            "submission-batch-invalid",
            "submission_ids must contain 1..16 unique non-empty IDs",
        )
    runtime = _discover_runtime(repo)
    submissions, freezes = [], []
    try:
        for sid in submission_ids:
            submission = load_submission(repo, sid)
            _verify_independence(submission, runtime)
            freeze = _verify_frozen(repo, submission)
            if list_layer(repo, "validations", "submission_id", sid):
                raise ValidationError("validation-already-published", sid)
            submissions.append(submission)
            freezes.append(freeze)
    except RecordError as exc:
        raise ValidationError(exc.code, exc.detail) from None

    freshness_errors: dict[int, tuple[str, str]] = {}

    def freshness(index: int, _profile: dict[str, Any]) -> bool:
        try:
            current = _discover_runtime(repo)
            if current.get("identity") != runtime.get("identity"):
                freshness_errors[index] = (
                    "validator-identity-drift",
                    "the native validator identity changed during this batch",
                )
                return False
            _verify_independence(submissions[index], current)
            _verify_frozen(repo, submissions[index])
            if list_layer(repo, "validations", "submission_id", submission_ids[index]):
                freshness_errors[index] = (
                    "validation-already-published",
                    submission_ids[index],
                )
                return False
            freshness_errors.pop(index, None)
            return True
        except (ValidationError, RecordError) as exc:
            freshness_errors[index] = (
                getattr(exc, "code", "submission-drift"),
                getattr(exc, "detail", str(exc)),
            )
            return False

    def after_profile(
        index: int, profile: dict[str, Any], report: dict[str, Any]
    ) -> bool:
        if not freshness(index, profile):
            return False
        if report.get("result") != "PASS":
            return True
        # Verify 已发布真实 source report 来证明同事务复用；Gate 仅重读，不再发布第二份源。
        from scripts.verification import read_report

        try:
            source, descriptor = read_report(repo, report["run_id"])
            if descriptor != report["publication"] or source != {
                key: value for key, value in report.items() if key != "publication"
            }:
                raise ValueError("published source differs from executed profile")
        except (ValueError, KeyError, OSError) as exc:
            freshness_errors[index] = ("validation-source-drift", str(exc))
            return False
        return True

    reports = verify_profiles(
        repo,
        frozen_profiles=freezes,
        before_profile=freshness,
        after_profile=after_profile,
    )
    outputs = []
    for index, (sid, submission, freeze, report) in enumerate(
        zip(submission_ids, submissions, freezes, reports, strict=True)
    ):
        report = _bind_terminal_report(report, freeze)
        if not freshness(index, freeze):
            code, detail = freshness_errors.get(index, ("submission-drift", sid))
            if code in {"validator-identity-drift", "validation-already-published"}:
                raise ValidationError(code, detail)
            report = {
                **report,
                "result": "FAIL",
                "reason": code,
                "detail": detail,
            }
        outputs.append(
            _publish_validation(repo, sid, submission, runtime, freeze, report)
        )
    return outputs
