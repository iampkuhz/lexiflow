"""使用 Verification 公开 API 对不可变 submission 执行独立 validation。"""

from __future__ import annotations
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import uuid
import yaml
from scripts.delivery_gate.authority import discover_authority, verify_authority
from scripts.delivery_gate.records import (
    RecordError,
    delivery_gate_locator,
    canonical_bytes,
    content_hash,
    list_layer,
    load_submission,
    publish_bytes,
    publish_json,
    read_bound_bytes,
    read_optional_bytes,
    read_regular_bytes,
    sha256_bytes,
)
from scripts.delivery_gate.requirements import requirements_are_current
from scripts.verification import verify_frozen_inputs, verify_profiles
from scripts.delivery_gate.acceptance import binding, verify_plan


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
    validator = _identity_values(runtime["identity"])
    if not validator:
        raise ValidationError("validator-identity-invalid", "native actor is missing")
    subjects = []
    for label, identity in (
        ("submitter", submission.get("submitter_identity")),
        ("producer", submission.get("producer", {}).get("identity")),
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
        synthetic = {
            "producer_identity": producer.get("identity"),
            "runtime_proof": producer.get("runtime_proof"),
            "authority": producer.get("authority"),
        }
        error = verify_authority(repo, synthetic, "producer_identity")
        if error:
            raise ValidationError(error, "producer")
    elif producer.get("kind") == "codex-work-package":
        desc = producer.get("completion", {})
        try:
            read_bound_bytes(repo, desc["locator"], desc["sha256"])
        except (KeyError, RecordError) as exc:
            raise ValidationError("producer-source-drift", str(exc)) from None
    elif producer.get("kind") == "qoder-work-package":
        artifacts = producer.get("artifacts")
        if not isinstance(artifacts, dict) or set(artifacts) != {
            "task",
            "completion",
            "result",
        }:
            raise ValidationError("producer-source-invalid", "qoder artifacts")
        for desc in artifacts.values():
            try:
                read_bound_bytes(repo, desc["locator"], desc["sha256"])
            except (KeyError, RecordError) as exc:
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
    if not requirements_are_current(repo, submission.get("task_requirements")):
        raise ValidationError(
            "task-requirements-drift", "task source/version/dependencies changed"
        )
    required = submission["task_requirements"].get("required_check_ids")
    if not isinstance(required, list):
        raise ValidationError("frozen-inputs-invalid", "task required checks missing")
    verify_plan(repo, submission)
    if not verify_frozen_inputs(repo, freeze):
        raise ValidationError(
            "frozen-input-drift",
            "input closure changed",
        )
    for label in ("change_report", "diff"):
        desc = submission.get(label)
        try:
            read_bound_bytes(repo, desc["locator"], desc["sha256"])
        except (KeyError, TypeError, RecordError):
            raise ValidationError("frozen-artifact-drift", label) from None
    snapshots = submission.get("changed_file_snapshots")
    if not isinstance(snapshots, dict):
        raise ValidationError("snapshot-invalid", "changed_file_snapshots")
    for relative, expected in snapshots.items():
        if not isinstance(relative, str) or not isinstance(expected, dict):
            raise ValidationError("snapshot-invalid", str(relative))
        try:
            data = read_optional_bytes(repo, relative)
        except RecordError as exc:
            raise ValidationError(exc.code, exc.detail) from None
        if expected.get("state") == "absent":
            if data is not None:
                raise ValidationError("file-tampered", relative)
        elif expected.get("state") == "present":
            if data is None or sha256_bytes(data) != expected.get("sha256"):
                raise ValidationError("file-tampered", relative)
        else:
            raise ValidationError("snapshot-invalid", relative)
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
            f"tmp/quality/delivery-gate/validations/{validation_id}/report.json",
            canonical_bytes(report),
        )
        record = {
            "schema_version": "lexiflow.delivery-gate-validation.v4",
            **binding(submission),
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
                "module": check.get("module", "unknown"),
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
    try:
        policy = yaml.safe_load(
            read_regular_bytes(repo, "harness/agent-policy.manifest.yaml")
        )
        maximum = policy["subagent_protocol"]["acceptance_submission"]["formal_batch"][
            "maximum_submissions"
        ]
        if type(maximum) is not int or maximum != 16:
            raise ValueError("formal batch maximum must be exactly 16")
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise ValidationError("formal-batch-policy-invalid", str(exc)) from None
    if (
        not isinstance(submission_ids, list)
        or not 1 <= len(submission_ids) <= maximum
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

    reports = verify_profiles(
        repo,
        frozen_profiles=freezes,
        before_profile=freshness,
        after_profile=lambda i, profile, report: freshness(i, profile),
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


def validate(root: str | Path, *, submission_id: str) -> dict[str, Any]:
    """保持既有单 Task API 和返回结构。"""
    return validate_batch(root, submission_ids=[submission_id])[0]
