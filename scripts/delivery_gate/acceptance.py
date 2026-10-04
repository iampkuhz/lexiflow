"""按当前实际风险绑定验收层与 Verification 范围，不接受调用者自报低风险。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from scripts.delivery_gate.records import (
    RecordError,
    canonical_bytes,
    parse_json,
    read_bound_bytes,
    read_regular_bytes,
    sha256_bytes,
)
from scripts.verification.risk import assess
from scripts.delivery_gate.source_snapshot import review_patch
from scripts.delivery_gate.task_subject import derive_task_subject
from scripts.delivery_gate.requirements import load_task_requirements
from scripts.verification.scope import changed_paths


def acceptance_plan(
    repo: Path, assessment: dict[str, Any], requirements: dict[str, Any]
) -> dict[str, Any]:
    """由机器评估和当前 Task 要求导出唯一层计划；任何层都不允许实现者自签验证。"""
    try:
        policy = yaml.safe_load(
            read_regular_bytes(repo, "harness/agent-policy.manifest.yaml")
        )
        settings = policy["risk_classification"]
        level = assessment["level"]
        scope = {
            "mechanical": "development-change",
            "local-function": "development-change",
            "high-risk-engineering": "development-baseline",
            "formal-release": "repository-baseline",
        }[level]
        review_levels = settings["independent_review_levels"]
        if (
            set(review_levels) != {"high-risk-engineering", "formal-release"}
            or settings["validation_actor"] != "different-from-producer-for-all-tiers"
            or assessment["schema_version"] != "lexiflow.risk-assessment.v1"
            or assessment["coverage_gaps"]
            or sorted(requirements["required_check_ids"])
            != assessment["required_check_ids"]
        ):
            raise ValueError("risk/requirements/independence contract invalid")
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RecordError("acceptance-plan-invalid", str(exc)) from None
    return {
        "schema_version": "lexiflow.acceptance-plan.v1",
        "risk_assessment_hash": sha256_bytes(canonical_bytes(assessment)),
        "task_requirements_hash": sha256_bytes(canonical_bytes(requirements)),
        "verification_scope": scope,
        "required_check_ids": sorted(requirements["required_check_ids"]),
        "required_layers": ["TASK_VALIDATION"]
        + (["INDEPENDENT_REVIEW"] if level in review_levels else []),
        "validator_independent": True,
    }


def binding(submission: dict[str, Any]) -> dict[str, str]:
    """给每一层绑定同一风险与验收计划哈希，不用缺席 review 冒充 review PASS。"""
    return {
        "risk_assessment_hash": sha256_bytes(
            canonical_bytes(submission["risk_assessment"])
        ),
        "acceptance_plan_hash": sha256_bytes(
            canonical_bytes(submission["acceptance_plan"])
        ),
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
    try:
        original = submission["risk_assessment"]
        requirements = submission["task_requirements"]
        paths = tuple(sorted(submission["changed_file_snapshots"]))
        task_subject = submission["task_subject"]
        current_requirements = load_task_requirements(repo, requirements["task_id"])
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
            or task_subject.get("changed_files") != list(paths)
            or current_subject != task_subject
            or current_requirements != requirements
            or not set(paths).issubset(set(report["scope_review"]["changed_files"]))
            or original["file_snapshots"] != submission["changed_file_snapshots"]
            or report.get("result") != "PASS"
            or report.get("scope") not in {"change-targeted", "development-change"}
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


def verify_validation_evidence(
    repo: Path, submission: dict[str, Any], validation: dict[str, Any]
) -> None:
    """核对验证报告实际范围与冻结计划一致，开发范围不能冒充完整发行验收。"""
    from scripts.verification.reports import validate_report

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
