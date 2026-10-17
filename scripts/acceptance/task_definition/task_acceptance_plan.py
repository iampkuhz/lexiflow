"""按当前实际风险绑定验收层与 Verification 范围，不接受调用者自报低风险。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.acceptance.evidence.acceptance_record_store import (
    RecordError,
    parse_json,
    read_bound_bytes,
)
from scripts.acceptance.evidence.task_review_diff import review_patch
from scripts.acceptance.task_definition.task_acceptance_requirements import (
    load_task_requirements,
)
from scripts.acceptance.task_definition.task_acceptance_scope import derive_task_subject
from scripts.verification.planning.change_risk_assessment import (
    INDEPENDENT_REVIEW_LEVELS,
    RISK_ASSESSMENT_SCHEMA,
    assess,
)
from scripts.verification.planning.check_scope_selection import changed_paths


def acceptance_plan(
    repo: Path, assessment: dict[str, Any], requirements: dict[str, Any]
) -> dict[str, Any]:
    """由机器评估和当前 Task 要求导出唯一层计划；任何层都不允许实现者自签验证。"""
    try:
        level = assessment["level"]
        scope = {
            "mechanical": "development-change",
            "local-function": "development-change",
            "high-risk-engineering": "development-baseline",
            "formal-release": "repository-baseline",
        }[level]
        review_levels = INDEPENDENT_REVIEW_LEVELS
        if (
            assessment["schema_version"]
            not in {RISK_ASSESSMENT_SCHEMA, "lexiflow.release-risk-assessment.v1"}
            or (
                assessment["schema_version"] == "lexiflow.release-risk-assessment.v1"
                and level != "formal-release"
            )
            or assessment["coverage_gaps"]
            or sorted(requirements["required_check_ids"])
            != assessment["required_check_ids"]
        ):
            raise ValueError("risk/requirements/independence contract invalid")
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RecordError("acceptance-plan-invalid", str(exc)) from None
    return {
        "schema_version": "lexiflow.acceptance-plan.v2",
        "verification_scope": scope,
        "required_check_ids": sorted(requirements["required_check_ids"]),
        "required_layers": ["TASK_VALIDATION"]
        + (["INDEPENDENT_REVIEW"] if level in review_levels else []),
        "validator_independent": True,
    }


def review_required(submission: dict[str, Any]) -> bool:
    """读取已验证计划的独立审查要求；缺计划失败关闭而不是默认跳过。"""
    plan = submission.get("acceptance_plan")
    if not isinstance(plan, dict) or plan.get("required_layers") not in (
        ["TASK_VALIDATION"],
        ["TASK_VALIDATION", "INDEPENDENT_REVIEW"],
    ):
        raise RecordError("acceptance-plan-invalid", "required layers missing")
    return "INDEPENDENT_REVIEW" in plan["required_layers"]


def verify_plan(repo: Path, submission: dict[str, Any]) -> None:
    """重算冻结主体的风险和层要求；额外无关写入不改变已绑定主体或授权低风险。"""
    requirements = submission.get("task_requirements")
    if not isinstance(requirements, dict) or not isinstance(
        requirements.get("task_id"), str
    ):
        raise RecordError("task-requirements-drift", "task requirements invalid")
    try:
        current_requirements = load_task_requirements(repo, requirements["task_id"])
    except RecordError as exc:
        raise RecordError("task-requirements-drift", str(exc)) from None
    if current_requirements != requirements:
        raise RecordError(
            "task-requirements-drift", "task source/version/dependencies changed"
        )
    if submission.get("release_subject") is not None:
        _verify_release_plan(repo, submission, current_requirements)
        return
    try:
        original = submission["risk_assessment"]
        paths = tuple(sorted(original["file_snapshots"]))
        task_subject = submission["task_subject"]
        current_subject = derive_task_subject(
            repo, current_requirements, changed_paths(repo, original["base"])
        )
        descriptor = submission["change_report"]
        report = parse_json(
            read_bound_bytes(repo, descriptor["locator"], descriptor["sha256"]),
            "change report",
        )
        if (
            original["changed_files"] != list(paths)
            or sorted(
                set(task_subject["task_paths"]) | set(task_subject["dependency_paths"])
            )
            != list(paths)
            or current_subject != task_subject
            or not set(paths).issubset(set(report["scope_review"]["changed_files"]))
            or report.get("base") != original["base"]
            or report.get("result") != "PASS"
            or report.get("scope") != "development-change"
        ):
            raise ValueError("risk subject differs from initial verified change")
        patch = submission["diff"]
        if read_bound_bytes(repo, patch["locator"], patch["sha256"]) != review_patch(
            repo, original["base"], paths
        ):
            raise ValueError("review patch does not cover current frozen subject bytes")
        current = assess(
            repo,
            base=original["base"],
            required_check_ids=tuple(requirements["required_check_ids"]),
            _subject_paths=paths,
        )
        if current != original:
            raise ValueError("risk subject, dependencies or policy changed")
        if (
            acceptance_plan(repo, current, requirements)
            != submission["acceptance_plan"]
        ):
            raise ValueError("risk-selected plan differs from submission")
        freeze = submission["verification_freeze"]
        if freeze.get("verification_scope") == "development-change" and freeze.get(
            "changed_files"
        ) != list(paths):
            raise ValueError("frozen verification subject differs from risk subject")
        if (
            freeze.get("verification_scope")
            != submission["acceptance_plan"]["verification_scope"]
            or freeze.get("required_check_ids")
            != submission["acceptance_plan"]["required_check_ids"]
        ):
            raise ValueError("frozen verification scope differs from required plan")
    except (KeyError, TypeError, ValueError) as exc:
        raise RecordError("risk-assessment-drift", str(exc)) from None


def _verify_release_plan(
    repo: Path, submission: dict[str, Any], requirements: dict[str, Any]
) -> None:
    """从当前 Registry 重派生正式层、完整 baseline Check 集合及发行 subject。"""
    from scripts.acceptance.task_definition.release_subject import (
        verify_release_subject,
    )
    from scripts.verification.execution.check_plan_runner import (
        _select_repository_checks,
    )
    from scripts.verification.planning.check_definitions import (
        load_declarations_snapshot,
    )

    try:
        subject = submission["release_subject"]
        verify_release_subject(repo, subject)
        declarations, _ = load_declarations_snapshot(repo)
        formal = set(
            declarations["scenario_policy"]["ci_quick"]["formal_only_check_ids"]
        )
        if not set(requirements["required_check_ids"]) & formal:
            raise ValueError("task is not eligible for release subject")
        selected, _baseline = _select_repository_checks(
            declarations["checks"], tuple(requirements["required_check_ids"])
        )
        freeze = submission["verification_freeze"]
        frozen_checks = freeze.get("checks")
        expected_check_ids = [item["check_id"] for item in selected]
        if (
            not isinstance(frozen_checks, list)
            or [
                item.get("check_id") for item in frozen_checks if isinstance(item, dict)
            ]
            != expected_check_ids
            or len(frozen_checks) != len(expected_check_ids)
        ):
            raise ValueError(
                "freeze omits or changes the current full repository baseline"
            )
        risk = {
            "schema_version": "lexiflow.release-risk-assessment.v1",
            "level": "formal-release",
            "required_check_ids": requirements["required_check_ids"],
            "coverage_gaps": [],
            "subject_hash": subject["handoff"]["sha256"],
        }
        plan = acceptance_plan(repo, risk, requirements)
        if (
            submission["risk_assessment"] != risk
            or submission["acceptance_plan"] != plan
            or freeze.get("verification_scope") != "repository-baseline"
            or freeze.get("result") != "PASS"
            or freeze.get("required_check_ids") != plan["required_check_ids"]
            or submission.get("change_report") is not None
            or submission.get("diff") is not None
            or submission.get("task_subject") is not None
        ):
            raise ValueError("release assessment or freeze differs from derived plan")
    except (KeyError, TypeError, ValueError, RecordError, yaml.YAMLError) as exc:
        raise RecordError("release-plan-drift", str(exc)) from None


def verify_validation_evidence(
    repo: Path, submission: dict[str, Any], validation: dict[str, Any]
) -> None:
    """核对验证报告实际范围与冻结计划一致，开发范围不能冒充完整发行验收。"""
    from scripts.verification.reports.verification_report_store import validate_report

    try:
        descriptor = validation["verification_report"]
        report = parse_json(
            read_bound_bytes(repo, descriptor["locator"], descriptor["sha256"]),
            "validation report",
        )
        validate_report(repo, report)
        if (
            report.get("scope") != submission["acceptance_plan"]["verification_scope"]
            or report.get("frozen_input_fingerprint")
            != submission["verification_freeze"]["input_fingerprint"]
            or report.get("required_check_ids")
            != submission["acceptance_plan"]["required_check_ids"]
            or [item.get("check_id") for item in report.get("checks", [])]
            != [
                item["check_id"] for item in submission["verification_freeze"]["checks"]
            ]
            or report.get("result") != validation.get("result")
            or (
                validation.get("result") == "PASS"
                and (validation.get("gaps") != [] or report.get("coverage_gaps") != [])
            )
        ):
            raise ValueError("report differs from risk-selected validation plan")
    except (KeyError, TypeError, ValueError) as exc:
        raise RecordError("validation-evidence-drift", str(exc)) from None
