"""从真实变更、输入闭包和覆盖状态确定性评估工作风险。"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from scripts.verification.declarations import _parse_declarations
from scripts.verification.scope import (
    ScopeError,
    changed_paths,
    resolve_module_dependencies,
    select_checks_for_changes,
    uncovered_changed_paths,
)

_POLICY_PATHS = (
    "harness/agent-policy.manifest.yaml",
    "harness/ci-policy.yaml",
    "harness/module-checks.yaml",
)


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "--literal-pathspecs", *args],
            cwd=root,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("risk-assessment-git-unavailable") from exc
    if result.returncode:
        raise ValueError("risk-assessment-git-input-invalid")
    return result.stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_regular(root: Path, locator: str) -> bytes:
    """通过 dirfd 和 O_NOFOLLOW 逐段读取文件并复核文件身份。"""
    if (
        not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_DIRECTORY")
        or not locator
        or locator.startswith("/")
        or "\\" in locator
        or any(part in {"", ".", ".."} for part in locator.split("/"))
    ):
        raise ValueError("risk-assessment-unsafe-file")
    parts = locator.split("/")
    directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            raise ValueError("risk-assessment-unsafe-file")
        for part in parts[:-1]:
            before = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise ValueError("risk-assessment-unsafe-file")
            child_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd
            )
            opened = os.fstat(child_fd)
            named = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
            if _identity(before) != _identity(opened) or _identity(opened) != _identity(
                named
            ):
                os.close(child_fd)
                raise ValueError("risk-assessment-unsafe-file")
            os.close(directory_fd)
            directory_fd = child_fd
        leaf = parts[-1]
        before = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("risk-assessment-unsafe-file")
        file_fd = os.open(
            leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd
        )
        try:
            opened = os.fstat(file_fd)
            if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(
                opened
            ):
                raise ValueError("risk-assessment-unsafe-file")
            chunks: list[bytes] = []
            while chunk := os.read(file_fd, 1024 * 1024):
                chunks.append(chunk)
            after_fd = os.fstat(file_fd)
            after_name = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
            if _identity(opened) != _identity(after_fd) or _identity(
                after_fd
            ) != _identity(after_name):
                raise ValueError("risk-assessment-input-drift")
            return b"".join(chunks)
        finally:
            os.close(file_fd)
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        raise ValueError("risk-assessment-unsafe-file") from exc
    finally:
        os.close(directory_fd)


def _fixed_base(root: Path, base: str) -> str:
    output = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}")
    return output.decode("ascii", errors="strict").strip()


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "--verify", "HEAD").decode("ascii").strip()


def _changed(root: Path, base: str) -> list[str]:
    return changed_paths(root, base)


def _matches_pattern(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern) or PurePosixPath(path).match(pattern)


def _risk_policy(root: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(
            _read_regular(root, "harness/agent-policy.manifest.yaml")
        )
        policy = document["risk_classification"]
        if policy.get("schema_version") != "lexiflow.risk-classification.v1":
            raise ValueError
        required = (
            "levels",
            "high_risk_paths",
            "local_owner_roots",
            "sensitive_content_patterns",
            "private_paths",
        )
        if any(not isinstance(policy.get(key), (list, dict)) for key in required):
            raise ValueError
        if len(set(policy["levels"])) != len(policy["levels"]) or set(
            policy["levels"]
        ) != {
            "mechanical",
            "local-function",
            "high-risk-engineering",
            "formal-release",
        }:
            raise ValueError
        if not all(
            isinstance(item, str)
            for item in [
                *policy["high_risk_paths"],
                *policy["sensitive_content_patterns"],
                *policy["private_paths"],
                *policy["local_owner_roots"].values(),
            ]
        ):
            raise ValueError
        return policy
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError("risk-assessment-policy-invalid") from exc


def _state_hash(root: Path, path: str, _base: str) -> dict[str, str]:
    try:
        content = _read_regular(root, path)
    except FileNotFoundError:
        return {"state": "absent"}
    return {"state": "present", "sha256": _sha(content)}


def _base_bytes(root: Path, base: str, path: str) -> bytes | None:
    try:
        return _git(root, "show", f"{base}:{path}")
    except ValueError:
        return None


def formal_only_ids(root: Path) -> set[str]:
    """读取正式发行专属 Check，并加入其变更视图 ID。"""
    try:
        document = yaml.safe_load(_read_regular(root, "harness/ci-policy.yaml"))
        identifiers = document["formal_only_check_ids"]
        if (
            not isinstance(identifiers, list)
            or not identifiers
            or any(not isinstance(value, str) or not value for value in identifiers)
        ):
            raise ValueError
        if len(set(identifiers)) != len(identifiers):
            raise ValueError
        return set(identifiers) | {f"{value}-on-change" for value in identifiers}
    except (OSError, KeyError, TypeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError("risk-assessment-formal-policy-invalid") from exc


def _high_path(path: str, policy: dict[str, Any]) -> bool:
    return any(_matches_pattern(path, pattern) for pattern in policy["high_risk_paths"])


def _signal_content(content: bytes, policy: dict[str, Any]) -> bool:
    text = content.decode("utf-8", errors="ignore")
    try:
        return any(
            re.search(pattern, text) for pattern in policy["sensitive_content_patterns"]
        )
    except re.error as exc:
        raise ValueError("risk-assessment-policy-invalid") from exc


def _changed_content(root: Path, base: str, path: str, after: bytes) -> bytes:
    if _base_bytes(root, base, path) is None:
        return after
    diff = _git(
        root, "diff", "--no-ext-diff", "--no-textconv", "--unified=0", base, "--", path
    )
    changed_lines = [
        line[1:]
        for line in diff.splitlines()
        if line[:1] in {b"+", b"-"} and not line.startswith((b"+++", b"---"))
    ]
    return b"\n".join(changed_lines)


def _classify_path(
    root: Path, base: str, path: str, snapshot: dict[str, str], policy: dict[str, Any]
) -> tuple[str, str]:
    if _high_path(path, policy):
        return "high-risk-engineering", "high-risk-path"
    before = _base_bytes(root, base, path)
    after = _read_regular(root, path) if snapshot["state"] == "present" else b""
    changed_content = _changed_content(root, base, path, after)
    if any(b"\0" in content for content in (before or b"", after, changed_content)):
        return "high-risk-engineering", "binary-content"
    for content in (before or b"", after, changed_content):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return "high-risk-engineering", "binary-content"
    if path.startswith("docs/user/") and path.lower().endswith(".md"):
        if before is not None and before.replace(b"\r\n", b"\n") == after.replace(
            b"\r\n", b"\n"
        ):
            return "mechanical", "line-ending-only"
        if (
            b"```" in (before or b"")
            or b"```" in after
            or _signal_content(before or b"", policy)
            or _signal_content(after, policy)
        ):
            return "high-risk-engineering", "sensitive-document-content"
        return "local-function", "user-document-content"
    local_roots = policy["local_owner_roots"]
    local_root = next(
        (prefix for prefix in local_roots.values() if path.startswith(prefix)), None
    )
    if path.startswith("backend/product/") and local_root is None:
        return "high-risk-engineering", "unknown-product-owner"
    if (
        local_root
        and local_root != local_roots.get("extension")
        and path.startswith("backend/product/")
    ):
        if not path.endswith(".java"):
            return "high-risk-engineering", "unclassified-domain-file"
        if before != after and _signal_content(changed_content, policy):
            return "high-risk-engineering", "sensitive-product-content"
        return "local-function", "private-product-code"
    if local_root == local_roots.get("extension"):
        if Path(path).suffix.lower() not in {".ts", ".tsx", ".js", ".jsx", ".mjs"}:
            return "high-risk-engineering", "unclassified-client-file"
        if before != after and _signal_content(changed_content, policy):
            return "high-risk-engineering", "sensitive-client-content"
        return "local-function", "client-code"
    if snapshot["state"] != "present":
        return "high-risk-engineering", "deleted-or-unclassified"
    if b"\0" in after:
        return "high-risk-engineering", "binary-content"
    return "high-risk-engineering", "unclassified-path"


def _check_snapshot(
    root: Path,
    base: str,
    required: tuple[str, ...],
    subject_paths: tuple[str, ...] | None = None,
) -> tuple[list[str], list[str], dict[str, str], dict[str, Any], list[str]]:
    head = _head(root)
    if subject_paths is None:
        paths = _changed(root, base)
    else:
        if (
            not subject_paths
            or any(not isinstance(path, str) or not path for path in subject_paths)
            or tuple(sorted(set(subject_paths))) != subject_paths
            or any(
                path.startswith("/")
                or ".." in PurePosixPath(path).parts
                or PurePosixPath(path).as_posix() != path
                or "\\" in path
                or "\0" in path
                for path in subject_paths
            )
        ):
            raise ValueError("risk-assessment-subject-paths-invalid")
        paths = list(subject_paths)
    if not paths:
        raise ValueError("risk-assessment-empty-change-set")
    policy = _risk_policy(root)
    if any(
        any(_matches_pattern(path, pattern) for pattern in policy["private_paths"])
        for path in paths
    ):
        raise ValueError("risk-assessment-private-path")
    files = {path: _state_hash(root, path, base) for path in paths}
    module_checks_bytes = _read_regular(root, "harness/module-checks.yaml")
    declarations = _parse_declarations(module_checks_bytes)
    checks = declarations["checks"]
    by_id = {check["check_id"]: check for check in checks}
    if len(by_id) != len(checks):
        raise ValueError("risk-assessment-duplicate-check-id")
    required_formal = formal_only_ids(root)
    if not required_formal.issubset(by_id):
        raise ValueError("risk-assessment-formal-checks-invalid")
    missing_required = sorted(set(required) - set(by_id))
    if missing_required:
        raise ValueError("risk-assessment-unknown-required-check")
    selected = select_checks_for_changes(checks, paths)
    selected.extend(
        by_id[check_id]
        for check_id in required
        if check_id not in {c["check_id"] for c in selected}
    )
    try:
        closure = resolve_module_dependencies(selected, checks) if selected else []
    except ScopeError as exc:
        raise ValueError("risk-assessment-dependency-closure-invalid") from exc
    covered = uncovered_changed_paths(checks, paths)
    policy_hashes = {
        path: _sha(
            module_checks_bytes
            if path == "harness/module-checks.yaml"
            else _read_regular(root, path)
        )
        for path in _POLICY_PATHS
    }
    diff_bytes = _git(
        root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--binary",
        base,
        "--",
        *paths,
    )
    return (
        paths,
        covered,
        files,
        {
            "head": head,
            "diff_sha256": _sha(diff_bytes),
            "policy_sha256": policy_hashes,
            "selected": closure,
            "selected_ids": [check["check_id"] for check in closure],
            "risk_policy": policy,
        },
        checks,
    )


def assess(
    root: Path,
    *,
    base: str,
    required_check_ids: tuple[str, ...] = (),
    _subject_paths: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """依据固定 commit、真实工作树及依赖闭包生成风险和主体摘要。"""
    root = root.resolve()
    fixed_base = _fixed_base(root, base)
    try:
        before = _check_snapshot(root, fixed_base, required_check_ids, _subject_paths)
        paths, uncovered, files, details, _checks = before
        formal = formal_only_ids(root)
        required_formal = bool(set(required_check_ids) & formal)
        policy = details["risk_policy"]
        levels = [
            _classify_path(root, fixed_base, path, files[path], policy)
            for path in paths
        ]
        reasons = sorted({reason for _level, reason in levels})
        owners = {
            owner
            for path in paths
            for owner, prefix in policy["local_owner_roots"].items()
            if owner != "user-documentation" and path.startswith(prefix)
        }
        if uncovered:
            reasons.append("coverage-gap")
        if len(owners) > 1:
            reasons.append("multiple-product-owners")
        level = max(
            (item[0] for item in levels),
            key=_level_rank,
            default="high-risk-engineering",
        )
        if uncovered or len(owners) > 1:
            level = "high-risk-engineering"
        if required_formal:
            level = "formal-release"
            reasons.append("formal-check-required")
        result = {
            "schema_version": "lexiflow.risk-assessment.v1",
            "level": level,
            "reason_codes": sorted(set(reasons)),
            "base": fixed_base,
            "head": details["head"],
            "changed_files": paths,
            "file_snapshots": files,
            "selected_check_ids": details["selected_ids"],
            "required_check_ids": sorted(set(required_check_ids)),
            "coverage_gaps": uncovered,
        }
        result["subject_hash"] = _sha(
            json.dumps(
                {
                    **result,
                    "diff_sha256": details["diff_sha256"],
                    "policy_sha256": details["policy_sha256"],
                    "selected_checks": details["selected"],
                },
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        after = _check_snapshot(root, fixed_base, required_check_ids, _subject_paths)
        if before != after or _head(root) != details["head"]:
            raise ValueError("risk-assessment-input-drift")
        return result
    except (OSError, UnicodeError, yaml.YAMLError, ScopeError) as exc:
        raise ValueError("risk-assessment-input-invalid") from exc


def _level_rank(level: str) -> int:
    return {
        "mechanical": 0,
        "local-function": 1,
        "high-risk-engineering": 2,
        "formal-release": 3,
    }.get(level, 2)
