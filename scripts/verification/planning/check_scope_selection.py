"""Verification 场景的范围选择和 Git 操作。依变更路径选择 Check，明确未覆盖路径并确定依赖闭包。"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


class ScopeError(ValueError):
    """范围解析失败，例如 Git 不可用或路径不安全。"""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# Git 操作
# ---------------------------------------------------------------------------


def git_run(root: Path, *args: str) -> str:
    """运行 Git 命令并返回 stdout；失败时抛出 ScopeError。"""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except FileNotFoundError:
        raise ScopeError("git-unavailable", "git executable not found")
    except subprocess.TimeoutExpired:
        raise ScopeError("git-timeout", "git command timed out")
    if r.returncode != 0:
        raise ScopeError("git-error", r.stderr.strip() or "git command failed")
    return r.stdout


def resolve_base(root: Path, explicit: str | None = None) -> str:
    """解析比较基点 commit；显式指定时直接使用，否则从当前仓库推导。"""
    if explicit:
        return explicit
    try:
        upstream = git_run(root, "rev-parse", "--verify", "@{upstream}")
        merge = git_run(root, "merge-base", "HEAD", upstream.strip())
        return merge.strip()
    except ScopeError:
        head = git_run(root, "rev-parse", "HEAD")
        return head.strip()


def changed_paths(root: Path, base: str) -> list[str]:
    """返回相对基点变更路径的排序去重列表，包含 tracked 与未跟踪路径。"""
    diff_out = git_run(
        root,
        "diff",
        "--no-renames",
        "--name-only",
        "-z",
        "--diff-filter=ACMRD",
        base,
        "--",
    )
    from scripts.checks.repository.catalog.task_catalog_rules import (
        repository_source_inventory,
    )

    inventory = repository_source_inventory(root)
    tracked = set(git_run(root, "ls-files", "--cached", "-z").split("\0"))
    untracked = "\0".join(
        path for path in inventory["candidate_paths"] if path not in tracked
    )
    combined = diff_out + untracked
    values = [x for x in combined.split("\0") if x]
    for v in values:
        if v.startswith("/") or ".." in PurePosixPath(v).parts:
            raise ScopeError("unsafe-path", f"unsafe changed path: {v!r}")
    return sorted(set(values))


# ---------------------------------------------------------------------------
# Check 选择
# ---------------------------------------------------------------------------


def _path_matches(trigger_path: str, changed_path: str) -> bool:
    """判断变更路径是否落在 trigger 路径前缀下。"""
    return changed_path == trigger_path or changed_path.startswith(
        trigger_path.rstrip("/") + "/"
    )


def select_checks_for_changes(
    checks: list[dict[str, Any]],
    changed: list[str],
) -> list[dict[str, Any]]:
    """依据变更文件与 trigger 选择 Check，并保留选择原因供报告解释。"""
    from scripts.verification.planning.check_definitions import filter_checks_by_scope

    checks = (
        filter_checks_by_scope(checks, "change-targeted")
        if any("scenarios" in c and "scope" not in c for c in checks)
        else checks
    )
    selected: list[dict[str, Any]] = []
    for check in checks:
        if check.get("scope") != "change-targeted":
            continue
        triggers = check.get("triggers", [])
        reasons: list[dict[str, str]] = []
        for trigger in triggers:
            trigger_path = trigger.get("path", "")
            for cp in changed:
                if _path_matches(trigger_path, cp):
                    reasons.append(
                        {
                            "kind": "changed-file",
                            "changed_file": cp,
                            "trigger_path": trigger_path,
                        }
                    )
        if reasons:
            entry = dict(check)
            entry["selection_reasons"] = reasons
            selected.append(entry)
    return selected


def resolve_module_dependencies(
    selected: list[dict[str, Any]],
    all_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """确定性求出规范 Check 依赖闭包；未知 ID 和环不能静默通过。"""
    by_id = {check["check_id"]: check for check in all_checks}
    if len(by_id) != len(all_checks):
        raise ScopeError("duplicate-check-id", "canonical Check IDs must be unique")

    ordered: list[dict[str, Any]] = []
    added: set[str] = set()
    visiting: list[str] = []

    def add(check: dict[str, Any], reason: dict[str, str] | None = None) -> None:
        check_id = check["check_id"]
        if check_id in visiting:
            cycle = " -> ".join([*visiting, check_id])
            raise ScopeError("module-dependency-cycle", cycle)
        if check["check_id"] in added:
            return
        visiting.append(check_id)
        for dependency in check.get("module_dependencies", []):
            if dependency not in by_id:
                raise ScopeError(
                    "unknown-check-dependency",
                    f"{check['check_id']} requires {dependency}",
                )
            add(
                by_id[dependency],
                {"kind": "module-dependency", "required_by": check["check_id"]},
            )
        visiting.pop()
        entry = dict(check)
        if reason:
            entry["selection_reasons"] = [reason]
        elif "selection_reasons" in check:
            entry["selection_reasons"] = list(check["selection_reasons"])
        added.add(entry["check_id"])
        ordered.append(entry)

    for check in selected:
        add(check)
    return ordered


def uncovered_changed_paths(
    checks: list[dict[str, Any]], changed: list[str]
) -> list[str]:
    """返回没有被 targeted trigger 直接覆盖的全部变更路径。"""
    from scripts.verification.planning.check_definitions import filter_checks_by_scope

    checks = (
        filter_checks_by_scope(checks, "change-targeted")
        if any("scenarios" in c and "scope" not in c for c in checks)
        else checks
    )
    uncovered: list[str] = []
    for changed_path in changed:
        covered = any(
            check.get("scope") == "change-targeted"
            and any(
                _path_matches(trigger.get("path", ""), changed_path)
                for trigger in check.get("triggers", [])
            )
            for check in checks
        )
        if not covered:
            uncovered.append(changed_path)
    return uncovered
