"""Scope selection and git operations for verification scenarios.

Selects checks based on changed files and module dependencies.
Provides git diff operations without importing scripts.gates.

Does NOT import scripts.gates or scripts.harness.
"""
from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Any


class ScopeError(ValueError):
    """Error during scope resolution (git unavailable, unsafe paths, etc.)."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------

def git_run(root: Path, *args: str) -> str:
    """Run a git command and return stdout. Raises ScopeError on failure."""
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
    """Resolve the comparison base commit.

    If *explicit* is given, return it directly.  Otherwise try
    ``@{upstream}`` merge-base, falling back to HEAD.
    """
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
    """Return sorted unique list of changed paths since *base*.

    Includes tracked changes (ACMRD) and untracked non-ignored files.
    Raises ScopeError on unsafe paths (absolute or path traversal).
    """
    diff_out = git_run(
        root, "diff", "--name-only", "-z", "--diff-filter=ACMRD", base, "--",
    )
    untracked = git_run(root, "ls-files", "--others", "--exclude-standard", "-z")
    combined = diff_out + untracked
    values = [x for x in combined.split("\0") if x]
    for v in values:
        if v.startswith("/") or ".." in PurePosixPath(v).parts:
            raise ScopeError("unsafe-path", f"unsafe changed path: {v!r}")
    return sorted(set(values))


# ---------------------------------------------------------------------------
# Check selection
# ---------------------------------------------------------------------------

def _path_matches(trigger_path: str, changed_path: str) -> bool:
    """Check if *changed_path* falls under *trigger_path* prefix."""
    return changed_path == trigger_path or changed_path.startswith(
        trigger_path.rstrip("/") + "/"
    )


def select_checks_for_changes(
    checks: list[dict[str, Any]],
    changed: list[str],
) -> list[dict[str, Any]]:
    """Select checks whose triggers match any changed file.

    Each selected check includes ``selection_reasons`` listing the
    matched changed files.
    """
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
                    reasons.append({
                        "kind": "changed-file",
                        "changed_file": cp,
                        "trigger_path": trigger_path,
                    })
        if reasons:
            entry = dict(check)
            entry["selection_reasons"] = reasons
            selected.append(entry)
    return selected


def resolve_module_dependencies(
    selected: list[dict[str, Any]],
    all_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Resolve the complete module dependency closure deterministically.

    Unknown modules and cycles are configuration errors: continuing would make
    a partial check set look complete.
    """
    by_module: dict[str, list[dict[str, Any]]] = {}
    for check in all_checks:
        by_module.setdefault(check.get("module", ""), []).append(check)
    for candidates in by_module.values():
        candidates.sort(key=lambda candidate: candidate["check_id"])

    ordered: list[dict[str, Any]] = []
    added: set[str] = set()
    visiting: list[str] = []

    def add(check: dict[str, Any], reason: dict[str, str] | None = None) -> None:
        module = check.get("module", "")
        if module in visiting:
            cycle = " -> ".join([*visiting, module])
            raise ScopeError("module-dependency-cycle", cycle)
        if check["check_id"] in added:
            return
        visiting.append(module)
        for dependency in check.get("module_dependencies", []):
            candidates = by_module.get(dependency, [])
            if not candidates:
                raise ScopeError("unknown-module-dependency", f"{check['check_id']} requires {dependency}")
            for candidate in candidates:
                add(candidate, {"kind": "module-dependency", "required_by": check["check_id"]})
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


def uncovered_changed_paths(checks: list[dict[str, Any]], changed: list[str]) -> list[str]:
    """Return every changed path not directly covered by a targeted trigger."""
    uncovered: list[str] = []
    for changed_path in changed:
        covered = any(
            check.get("scope") == "change-targeted" and any(
                _path_matches(trigger.get("path", ""), changed_path)
                for trigger in check.get("triggers", [])
            )
            for check in checks
        )
        if not covered:
            uncovered.append(changed_path)
    return uncovered


def compute_scope_review(
    changed: list[str],
    expected_paths: tuple[str, ...] | list[str],
    executed_ids: set[str],
    declared_count: int,
) -> dict[str, Any]:
    """Compute scope_review section of the report.

    Compares changed files against expected paths for self-review
    advisory.  Reports coverage gaps when changed files are not
    covered by any executed check.
    """
    expected_list = list(expected_paths)
    if expected_list:
        expected_files = [
            p for p in changed
            if any(
                p == e or p.startswith(e.rstrip("/") + "/")
                for e in expected_list
            )
        ]
        unexpected_files = [p for p in changed if p not in expected_files]
        review_kind = "expected"
    else:
        expected_files = []
        unexpected_files = list(changed)
        review_kind = "no-context"

    advisories: list[str] = []
    if unexpected_files:
        advisories.append("review unexpected files")
    if changed and not executed_ids:
        advisories.append("no checks matched changed files")

    return {
        "kind": review_kind,
        "changed_files": changed,
        "expected": expected_files,
        "unexpected": unexpected_files,
        "advisories": advisories,
        "checks_declared": declared_count,
        "checks_executed": len(executed_ids),
    }
