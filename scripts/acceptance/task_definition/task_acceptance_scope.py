"""从真实工作树、Task 声明及 Check 闭包派生单 Task 验收主体。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.acceptance.evidence.acceptance_record_store import read_regular_bytes
from scripts.verification.planning.check_definitions import (
    _parse_declarations,
    filter_checks_by_scope,
)
from scripts.verification.planning.check_scope_selection import (
    _path_matches,
    resolve_module_dependencies,
    select_checks_for_changes,
)


def _matches(path: str, patterns: list[str]) -> bool:
    from scripts.agents.work_packages.work_package_contract_checks import (
        AgentContractError,
        match_dispatch_path_v1,
    )

    try:
        return any(match_dispatch_path_v1(path, pattern) for pattern in patterns)
    except AgentContractError as exc:
        raise ValueError(f"task-scope-invalid: {exc}") from None


def derive_task_subject(
    repo: Path, requirements: dict[str, Any], full_changed: list[str]
) -> dict[str, Any]:
    """按 claims/allowed 取主体，再递归纳入所选检查输入内的真实依赖变更。"""
    allowed = requirements["allowed_files"]
    claims = requirements["file_claims"]
    forbidden = requirements["forbidden_files"]
    body: list[str] = []
    for path in full_changed:
        # 完整工作区差异可能包含其他 Task 的工作；仅 allowed 范围构成主体。
        if allowed and not _matches(path, allowed):
            continue
        if _matches(path, forbidden):
            raise ValueError(f"forbidden-scope: {path}")
        if claims and not _matches(path, claims):
            raise ValueError(f"outside-file-claim: {path}")
        body.append(path)
    if not body:
        raise ValueError("task-subject-empty")

    declarations = read_regular_bytes(repo, "config/engineering/module-checks.yaml")
    checks = _parse_declarations(declarations)["checks"]
    change_checks = filter_checks_by_scope(checks, "change-targeted")
    related: set[str] = set()
    prior = set(body)
    while True:
        selected = select_checks_for_changes(change_checks, sorted(prior))
        selected.extend(
            check
            for check in checks
            if check["check_id"] in requirements["required_check_ids"]
            and check["check_id"] not in {item["check_id"] for item in selected}
        )
        closure = resolve_module_dependencies(selected, checks) if selected else []
        input_patterns = sorted(
            {pattern for item in closure for pattern in item["input_paths"]}
        )
        expanded = {
            path
            for path in full_changed
            if any(_path_matches(pattern, path) for pattern in input_patterns)
        }
        new_prior = set(body) | related | expanded
        if new_prior == prior:
            break
        related.update(expanded)
        prior = new_prior
    related.difference_update(body)
    return {
        "schema_version": "lexiflow.task-subject.v2",
        "task_paths": sorted(body),
        "dependency_paths": sorted(related),
    }


def subject_paths(subject: dict[str, Any]) -> list[str]:
    """由冻结的两个来源分区派生主体有序并集，不持久化第三份视图。"""
    return sorted(set(subject["task_paths"]) | set(subject["dependency_paths"]))
