"""Verify 冻结来源到 release mechanism fixture 的内部桥接。"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.verification.declarations import DeclarationError, load_declarations
from scripts.verification.kernel import fingerprint_json, snapshot_check_inputs

CHECK_ID = "eng.release.lifecycle-runtime"
CHANGE_CHECK_ID = "eng.release.lifecycle-runtime-on-change"
VERIFY_CONTEXT_FIELDS = frozenset({"run_id", "check_id", "check_config_fingerprint"})
SNAPSHOT_FIELDS = frozenset({"fingerprint", "files", "missing"})
KIND = "verify-release-source-fixture"
PURPOSE = "mechanism-fixture"
FIXED_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
    "TZ": "UTC",
}

# The list is intentionally fixed here; a caller cannot widen the release closure.
FIXED_FILES = frozenset(
    {
        ".gitignore",
        "infra/postgres/schema.sql",
        "ops/release/build.mjs",
        "ops/release/archive-identity.mjs",
        "ops/release/embedded-identity.mjs",
        "ops/release/build-command.mjs",
        "ops/release/build-execution.mjs",
        "ops/release/candidate.mjs",
        "ops/release/verified-candidate.mjs",
        "ops/release/lifecycle.mjs",
        "ops/release/lifecycle-docker.sh",
        "ops/release/lifecycle-state.sh",
        "ops/release/lifecycle.sh",
        "ops/release/manifest.mjs",
        "ops/release/package.mjs",
        "ops/release/pipeline.mjs",
        "ops/release/runtime-entry.mjs",
        "ops/release/runtime-verification.sh",
        "ops/release/version.mjs",
        "ops/release/version.txt",
        "ops/release/third-party-notices.md",
        "ops/docker/base-images.json",
        "ops/docker/Dockerfile",
        "ops/docker/Dockerfile.postgres",
        "ops/docker/.dockerignore",
        "ops/docker/bootstrap.sh",
        "ops/docker/compose.yaml",
        "ops/docker/entrypoint.sh",
        "extension/manifest.json",
        "extension/package.json",
        "extension/package-lock.json",
        "extension/tsconfig.json",
        "backend/build.gradle.kts",
        "backend/settings.gradle.kts",
        "backend/settings-gradle.lockfile",
        "backend/gradle.properties",
        "backend/gradle.lockfile",
        "backend/gradle/libs.versions.toml",
        "backend/gradle/wrapper/gradle-wrapper.jar",
        "backend/gradle/wrapper/gradle-wrapper.properties",
        "backend/gradlew",
        "backend/gradle/build-logic/build.gradle.kts",
        "backend/gradle/build-logic/gradle.lockfile",
        "backend/gradle/build-logic/settings-gradle.lockfile",
        "backend/gradle/build-logic/settings.gradle.kts",
        "backend/product/api/gradle.lockfile",
        "backend/product/adapters/gradle.lockfile",
        "backend/product/enrichment/gradle.lockfile",
        "backend/product/lexicon/gradle.lockfile",
        "backend/verification/architecture/gradle.lockfile",
        "backend/verification/integration/gradle.lockfile",
        "backend/verification/quality-gates/build.gradle.kts",
        "backend/verification/quality-gates/gradle.lockfile",
    }
)
FIXED_DIRS = (
    "backend/gradle/build-logic",
    "backend/gradle/config",
    "backend/product/api",
    "backend/product/adapters",
    "backend/product/enrichment",
    "backend/product/lexicon",
    "backend/verification/architecture",
    "backend/verification/integration",
    "backend/verification/quality-gates",
    "extension/assets",
    "extension/scripts",
    "extension/src",
)
_GENERATED = frozenset({"build", "dist", "node_modules", ".gradle", "__pycache__"})
_PRIVATE = frozenset({".git", ".local", ".npmrc", "secrets"})
_SUPPORT_FILES = frozenset(
    {
        "scripts/verification/release_source_bridge.py",
        "tests/verification/test_release_source_bridge.py",
        "tests/verification/test_release_runtime_check.py",
        "tests/verification/test_release_runtime_artifacts.py",
        "tests/verification/test_release_runtime_lifecycle.py",
        "tests/verification/test_environment.py",
        "ops/release/check.mjs",
        "ops/release/build-check.mjs",
        "ops/release/candidate-proof.mjs",
        "ops/release/tests/candidate-proof.test.mjs",
        "ops/release/workflow.mjs",
        "ops/release/tests/workflow.test.mjs",
        "ops/release/tests/verified-candidate-fixture.mjs",
        "ops/release/tests/lifecycle-fixture.mjs",
        "ops/release/tests/fake-docker-daemon.mjs",
        "ops/release/tests/build.test.mjs",
        "ops/release/tests/zip-fixture.mjs",
        "ops/release/tests/archive-identity.test.mjs",
        "ops/release/tests/embedded-identity.test.mjs",
        "ops/release/tests/build-execution.test.mjs",
        "ops/release/tests/candidate.test.mjs",
        "ops/release/tests/verified-candidate.test.mjs",
        "ops/release/tests/pipeline.test.mjs",
        "ops/docker/tests/update-recovery.sh",
        "harness/module-checks.yaml",
        "harness/test-services.json",
        "tests/verification/test_release_checks.py",
        "scripts/verification/input_snapshot.py",
        "scripts/verification/kernel.py",
        "scripts/verification/scenarios.py",
        "scripts/verification/reports.py",
        "tests/verification/test_input_snapshot.py",
    }
)
_SUPPORT_DIRS = ("extension/tests", "scripts/environment")


class BridgeError(RuntimeError):
    """带固定状态与错误码的来源桥接拒绝结果。"""

    def __init__(self, status: str, code: str) -> None:
        self.status, self.code = status, code
        super().__init__(f"{status}/{code}")


class FrozenSource:
    """仅作为本进程 API 交接载体；对象类型不构成安全认证。"""

    __slots__ = (
        "_seal",
        "check",
        "check_config_fingerprint",
        "identity",
        "root",
        "snapshot",
    )

    def __init__(
        self,
        root: Path,
        check: dict[str, Any],
        snapshot: dict[str, Any],
        identity: dict[str, Any],
        seal: object,
    ) -> None:
        self.root, self.check, self.snapshot = root, check, snapshot
        self.check_config_fingerprint = fingerprint_json(check)
        self.identity, self._seal = identity, seal


_SEAL = object()


def _fail(code: str) -> None:
    raise BridgeError("FAIL", code)


def _blocked(code: str) -> None:
    raise BridgeError("BLOCKED", code)


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> bytes:
    merged = dict(FIXED_ENV)
    merged["PATH"] = os.defpath
    if env:
        merged.update(env)
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=merged,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        _blocked("git-metadata-unavailable")


def _git_text(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    try:
        return _git(root, *args, env=env).decode("ascii").strip()
    except UnicodeDecodeError:
        _blocked("git-metadata-unavailable")


def _rename_noreplace_syscall(
    source: Path, destination: Path
) -> tuple[int, int] | None:
    """Invoke a verified platform no-replace rename primitive; None means unsupported."""
    if sys.platform != "darwin":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = libc.renamex_np
        # macOS SDK sys/stdio.h: int renamex_np(const char *, const char *, unsigned int);
        # RENAME_EXCL is 0x00000004; SDK rename(2) documents EEXIST on any existing target.
        renamex_np.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renamex_np(
            os.fsencode(source), os.fsencode(destination), ctypes.c_uint(0x00000004)
        )
        return result, ctypes.get_errno() if result != 0 else 0
    except (AttributeError, OSError):
        return None


def _publish_noreplace(stage: Path, final: Path) -> None:
    """Atomically publish a directory without replacing any existing name."""
    outcome = _rename_noreplace_syscall(stage, final)
    if outcome is None:
        _blocked("atomic-no-replace-unavailable")
    result, error_number = outcome
    if result == 0:
        return
    if error_number == errno.EEXIST:
        _fail("output-target-exists")
    unsupported = {errno.ENOSYS, errno.EINVAL}
    for name in ("ENOTSUP", "EOPNOTSUPP"):
        value = getattr(errno, name, None)
        if value is not None:
            unsupported.add(value)
    if error_number in unsupported:
        _blocked("atomic-no-replace-unavailable")
    _fail("output-publish-failed")


def _read_dirty_path(root: Path, path: str) -> tuple[str, int, bool]:
    """以 no-follow descriptor 读取 dirty/untracked 字节；不读取特殊入口。"""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parts = path.split("/")
    try:
        for part in parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        before = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            _blocked("git-dirty-state-unrepresentable")
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        try:
            opened = os.fstat(file_fd)
            if not stat.S_ISREG(opened.st_mode) or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns):
                _fail("repository-identity-drift")
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
            after = os.fstat(file_fd)
            named = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if (
                (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)
                or size != after.st_size
            ):
                _fail("repository-identity-drift")
            return digest.hexdigest(), size, bool(after.st_mode & 0o111)
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def _safe_path(path: str) -> bool:
    PurePosixPath(path)
    return (
        bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and all(x not in {"", ".", ".."} for x in path.split("/"))
        and not any(ord(c) < 32 or ord(c) == 127 for c in path)
    )


def _private(path: str) -> bool:
    return any(
        part in _PRIVATE or part == ".env" or part.startswith(".env.")
        for part in path.split("/")
    )


def _load_check(root: Path, check_id: str) -> dict[str, Any]:
    if check_id not in {CHECK_ID, CHANGE_CHECK_ID}:
        _fail("check-identity-mismatch")
    try:
        document = load_declarations(root)
    except DeclarationError:
        _fail("check-configuration-invalid")
    checks = document.get("checks") if isinstance(document, dict) else None
    if not isinstance(checks, list):
        _fail("check-configuration-invalid")
    matches = [
        item
        for item in checks
        if isinstance(item, dict) and item.get("check_id") == check_id
    ]
    if len(matches) != 1:
        _fail("check-identity-mismatch")
    check = matches[0]
    if not isinstance(check.get("input_paths"), list):
        _fail("check-configuration-invalid")
    return check


def _exact_json_equal(left: Any, right: Any) -> bool:
    """比较 JSON 值及其精确类型，禁止 bool 与 int 相等绕过。"""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _exact_json_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _exact_json_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _rebuild_effective_check(
    root: Path, effective_check: Any, check_id: str
) -> dict[str, Any]:
    """从固定声明重建 Verify 实际使用的 lifecycle Check。"""
    if (
        not isinstance(effective_check, dict)
        or effective_check.get("check_id") != check_id
    ):
        _fail("verify-context-invalid")
    try:
        document = load_declarations(root)
    except DeclarationError:
        _fail("check-configuration-invalid")
    checks = document.get("checks") if isinstance(document, dict) else None
    if not isinstance(checks, list):
        _fail("check-configuration-invalid")
    matches = [
        item
        for item in checks
        if isinstance(item, dict) and item.get("check_id") == check_id
    ]
    if len(matches) != 1:
        _fail("check-identity-mismatch")
    declared = matches[0]
    expected = dict(declared)
    expected["input_paths"] = list(
        dict.fromkeys([*declared.get("input_paths", []), "harness/module-checks.yaml"])
    )
    reasons = effective_check.get("selection_reasons")
    if check_id == CHECK_ID:
        if "selection_reasons" in effective_check:
            _fail("verify-context-invalid")
    else:
        if (
            declared.get("scope") != "change-targeted"
            or not isinstance(reasons, list)
            or not reasons
        ):
            _fail("verify-context-invalid")
        _validate_selection_reasons(declared, reasons, checks)
        expected["selection_reasons"] = reasons
    if not _exact_json_equal(effective_check, expected):
        _fail("verify-context-invalid")
    return expected


def _validate_selection_reasons(
    declared: dict[str, Any], reasons: list[Any], checks: list[Any]
) -> None:
    """仅接受 Verify scope selector 可生成且由声明支持的选择原因。"""
    triggers = declared.get("triggers", [])
    for reason in reasons:
        if not isinstance(reason, dict) or not isinstance(reason.get("kind"), str):
            _fail("verify-context-invalid")
        if reason["kind"] == "changed-file":
            if set(reason) != {"kind", "changed_file", "trigger_path"}:
                _fail("verify-context-invalid")
            changed, trigger_path = (
                reason.get("changed_file"),
                reason.get("trigger_path"),
            )
            declared_paths = {
                item.get("path") for item in triggers if isinstance(item, dict)
            }
            if (
                not isinstance(changed, str)
                or not changed
                or not isinstance(trigger_path, str)
                or trigger_path not in declared_paths
                or not (
                    changed == trigger_path
                    or changed.startswith(trigger_path.rstrip("/") + "/")
                )
            ):
                _fail("verify-context-invalid")
        elif reason["kind"] == "module-dependency":
            if set(reason) != {"kind", "required_by"} or not isinstance(
                reason.get("required_by"), str
            ):
                _fail("verify-context-invalid")
            parents = [
                item
                for item in checks
                if isinstance(item, dict)
                and item.get("check_id") == reason["required_by"]
            ]
            if len(parents) != 1 or declared.get("module") not in parents[0].get(
                "module_dependencies", []
            ):
                _fail("verify-context-invalid")
        else:
            _fail("verify-context-invalid")


def _git_identity(root: Path) -> dict[str, Any]:
    head = _git_text(root, "rev-parse", "HEAD")
    # Build the index tree with an independent temporary index. The user's index is read only.
    index_path = Path(_git_text(root, "rev-parse", "--git-path", "index"))
    if not index_path.is_absolute():
        index_path = root / index_path
    try:
        before = index_path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            _blocked("git-index-unrepresentable")
        index_fd = os.open(index_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(index_fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ):
                _fail("repository-identity-drift")
            raw_parts: list[bytes] = []
            while True:
                chunk = os.read(index_fd, 1024 * 1024)
                if not chunk:
                    break
                raw_parts.append(chunk)
            raw_index = b"".join(raw_parts)
            after_fd = os.fstat(index_fd)
            after_path = index_path.stat(follow_symlinks=False)
            if (
                (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                != (
                    after_fd.st_dev,
                    after_fd.st_ino,
                    after_fd.st_size,
                    after_fd.st_mtime_ns,
                )
                or (after_fd.st_dev, after_fd.st_ino)
                != (after_path.st_dev, after_path.st_ino)
                or len(raw_index) != after_fd.st_size
            ):
                _fail("repository-identity-drift")
        finally:
            os.close(index_fd)
        after = index_path.stat(follow_symlinks=False)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            _fail("repository-identity-drift")
        with tempfile.TemporaryDirectory(prefix="lexiflow-index-objects-") as td:
            private = Path(td)
            idx = str(private / "index")
            object_dir = private / "objects"
            object_dir.mkdir(mode=0o700)
            source_objects = Path(_git_text(root, "rev-parse", "--git-path", "objects"))
            if not source_objects.is_absolute():
                source_objects = root / source_objects
            source_objects = source_objects.resolve(strict=True)
            if ":" in str(source_objects):
                _blocked("git-object-store-path-unrepresentable")
            idxenv = {
                "GIT_INDEX_FILE": idx,
                "GIT_OBJECT_DIRECTORY": str(object_dir),
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(source_objects),
            }
            _git(root, "read-tree", "--empty", env=idxenv)
            entries = _git(root, "ls-files", "--stage", "-z")
            for entry in entries.split(b"\0"):
                if not entry:
                    continue
                try:
                    meta, path = entry.split(b"\t", 1)
                    mode, oid, stage = meta.decode("ascii").split()
                    name = path.decode("utf-8", "strict")
                except (ValueError, UnicodeDecodeError):
                    _blocked("git-index-unrepresentable")
                if not _safe_path(name) or _private(name):
                    _blocked("git-path-unrepresentable")
                if stage != "0" or mode == "160000" or set(oid) == {"0"}:
                    _blocked("git-index-unrepresentable")
                _git(
                    root,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    mode,
                    oid,
                    name,
                    env=idxenv,
                )
            tree = _git_text(root, "write-tree", env=idxenv)
        # raw_index binds the real index bytes as well as its semantic tree.
        index_bytes = hashlib.sha256(raw_index).hexdigest()
    except (OSError, ValueError):
        _blocked("git-index-unrepresentable")
    status = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    try:
        records = status.decode("utf-8", "strict").split("\0")
    except UnicodeDecodeError:
        _blocked("git-path-unrepresentable")
    changes: list[dict[str, Any]] = []
    for record in records:
        if not record:
            continue
        if (
            len(record) < 4
            or any(x in record[:2] for x in "RC")
            or record[:2] in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}
        ):
            _blocked("git-dirty-state-unrepresentable")
        if record[:2] != "??" and record[0] != " " and record[1] != " ":
            _blocked("git-dirty-state-unrepresentable")
        path = record[3:]
        if not _safe_path(path) or _private(path):
            _blocked("git-path-unrepresentable")
        p = root / path
        try:
            st = p.lstat()
        except FileNotFoundError:
            st = None
        if st is not None and (not stat.S_ISREG(st.st_mode)):
            _blocked("git-dirty-state-unrepresentable")
        data_hash, data_size, executable = (
            _read_dirty_path(root, path) if st else ("", 0, False)
        )
        changes.append(
            {
                "path": path,
                "status": record[:2],
                "sha256": data_hash,
                "sizeBytes": data_size,
                "executable": executable,
            }
        )
    changes.sort(key=lambda x: x["path"])
    return {
        "head": head,
        "indexTree": tree,
        "indexBytesSha256": index_bytes,
        "changes": changes,
        "dirtyFingerprint": fingerprint_json(changes),
    }


def freeze_release_source(repo_root: str | Path, check_id: str) -> FrozenSource:
    """冻结精确 Check 声明输入，并立即以真实 kernel 复算。"""
    supplied_root = Path(repo_root)
    if supplied_root.is_symlink():
        _fail("repository-root-invalid")
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        _fail("repository-root-invalid")
    check = _load_check(root, check_id)
    before = _git_identity(root)
    snapshot = snapshot_check_inputs(root, check)
    if snapshot["missing"]:
        if (
            ".gitignore" in snapshot["missing"]
            or "ops/release/third-party-notices.md" in snapshot["missing"]
        ):
            _blocked("missing-frozen-input")
        _fail("missing-frozen-input")
    if not isinstance(snapshot.get("files"), list) or not snapshot.get("fingerprint"):
        _fail("snapshot-shape-invalid")
    after_snapshot = snapshot_check_inputs(root, check)
    after = _git_identity(root)
    if snapshot != after_snapshot or before != after:
        _fail("repository-identity-drift")
    return FrozenSource(root, check, snapshot, before, _SEAL)


def _closure(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    files = snapshot["files"]
    by_path: dict[str, dict[str, Any]] = {}
    for item in files:
        path = item.get("locator") if isinstance(item, dict) else None
        if not isinstance(path, str) or not _safe_path(path) or _private(path):
            _fail("unsafe-frozen-locator")
        if any(part in _GENERATED or part.endswith(".pyc") for part in path.split("/")):
            _fail("generated-locator-rejected")
        folded = path.casefold()
        if folded in {x.casefold() for x in by_path} or path in by_path:
            _fail("duplicate-or-case-colliding-locator")
        by_path[path] = item
    if [x.get("locator") for x in files] != sorted(by_path):
        _fail("frozen-locators-not-canonical")
    missing_required = FIXED_FILES - by_path.keys()
    if missing_required:
        if {".gitignore", "ops/release/third-party-notices.md"} & missing_required:
            _blocked("missing-frozen-input")
        _fail("release-closure-incomplete")
    closure = set(FIXED_FILES)
    for root in FIXED_DIRS:
        descendants = {
            p
            for p in by_path
            if p.startswith(root + "/") and _included_tree_file(root, p)
        }
        if not descendants:
            _fail("release-closure-incomplete")
        closure.update(descendants)
    permitted = set(closure) | set(_SUPPORT_FILES)
    for root in _SUPPORT_DIRS:
        permitted.update(path for path in by_path if path.startswith(root + "/"))
    unknown = by_path.keys() - permitted
    if unknown:
        _fail("unknown-frozen-locator")
    selected = [by_path[p] for p in sorted(closure)]
    if len(selected) != len(closure):
        _fail("release-closure-duplicate-file")
    return selected


def _reject_git_attributes(root: Path, paths: list[str]) -> None:
    """Git attributes can transform staged/checked-out bytes, so fail closed."""
    if not paths:
        return
    raw = _git(root, "check-attr", "-a", "-z", "--", *paths)
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 3:
        _blocked("git-attributes-unrepresentable")
    for index in range(0, len(fields), 3):
        try:
            fields[index + 1].decode("ascii")
            value = fields[index + 2].decode("ascii")
        except UnicodeDecodeError:
            _blocked("git-attributes-unrepresentable")
        if value not in {"unspecified", "unset"}:
            _blocked("git-attributes-unrepresentable")


def _included_tree_file(root: str, path: str) -> bool:
    """按合同限定 Gradle module 子树，避免隐式吞入无关文件。"""
    relative = path[len(root) + 1 :]
    if root in {
        "backend/product/api",
        "backend/product/adapters",
        "backend/product/enrichment",
        "backend/product/lexicon",
    }:
        return relative.startswith("src/")
    if root in {
        "backend/verification/architecture",
        "backend/verification/integration",
    }:
        return relative.startswith("src/")
    if root == "backend/verification/quality-gates":
        return relative.startswith("src/")
    if root == "backend/gradle/build-logic":
        return relative.startswith("src/")
    if root == "extension/assets":
        return True
    if root in {"extension/scripts", "extension/src", "backend/gradle/config"}:
        return True
    return False


def _read_source(root: Path, path: str, expected: dict[str, Any]) -> bytes:
    if _private(path):
        _fail("private-path-rejected")
    if not _safe_path(path):
        _fail("unsafe-frozen-locator")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = path.split("/")
        for part in parts[:-1]:
            nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nfd
        before = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            _fail("source-special-file")
        src = os.open(parts[-1], flags, dir_fd=fd)
        try:
            opened = os.fstat(src)
            if not stat.S_ISREG(opened.st_mode) or (
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            ) != (before.st_ino, before.st_size, before.st_mtime_ns):
                _fail("source-identity-drift")
            chunks: list[bytes] = []
            remaining = int(expected["size_bytes"])
            while remaining:
                chunk = os.read(src, min(1024 * 1024, remaining))
                if not chunk:
                    _fail("source-short-read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(src, 1):
                _fail("source-size-drift")
            data = b"".join(chunks)
            after = os.fstat(src)
            named = os.stat(parts[-1], dir_fd=fd, follow_symlinks=False)
            if (opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ) or (after.st_ino, after.st_size) != (named.st_ino, named.st_size):
                _fail("source-identity-drift")
            if (
                hashlib.sha256(data).hexdigest() != expected["sha256"]
                or bool(opened.st_mode & 0o111) != expected["executable"]
            ):
                _fail("source-content-drift")
            return data
        finally:
            os.close(src)
    finally:
        os.close(fd)


def _is_private_dir(path: Path) -> bool:
    return any(p in _PRIVATE or p == ".local" for p in path.parts)


def _validate_output(root: Path, output_parent: str | Path) -> Path:
    raw = Path(output_parent)
    absolute = raw.absolute()
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        try:
            if stat.S_ISLNK(cursor.lstat().st_mode):
                _fail("output-parent-invalid")
        except FileNotFoundError:
            _fail("output-parent-invalid")
    if raw.is_symlink() or not raw.exists() or not raw.is_dir():
        _fail("output-parent-invalid")
    parent = raw.resolve(strict=True)
    if _is_private_dir(parent):
        _fail("private-output-parent")
    try:
        parent.relative_to(root)
        _fail("output-parent-overlaps-checkout")
    except ValueError:
        pass
    try:
        root.relative_to(parent)
        _fail("output-parent-overlaps-checkout")
    except ValueError:
        pass
    return parent


def _closure_description() -> dict[str, Any]:
    return {
        "fixedFiles": sorted(FIXED_FILES),
        "fixedDirectories": list(FIXED_DIRS),
        "generatedExcluded": sorted(_GENERATED),
    }


def create_release_source_fixture(
    repo_root: str | Path, frozen_source: FrozenSource, output_parent: str | Path
) -> dict[str, Any]:
    """把冻结闭包复制到隔离 Git fixture，并写出不可用于正式发行的映射 receipt。"""
    supplied_root = Path(repo_root)
    if supplied_root.is_symlink():
        _fail("repository-root-invalid")
    root = supplied_root.resolve(strict=True)
    if not isinstance(frozen_source, FrozenSource) or frozen_source._seal is not _SEAL:
        _fail("frozen-object-not-issued-here")
    if frozen_source.root != root or frozen_source.check.get("check_id") != CHECK_ID:
        _fail("frozen-object-context-mismatch")
    check = _load_check(root, CHECK_ID)
    if fingerprint_json(check) != frozen_source.check_config_fingerprint:
        _fail("check-configuration-drift")
    current = snapshot_check_inputs(root, check)
    identity = _git_identity(root)
    if current != frozen_source.snapshot or identity != frozen_source.identity:
        _fail("frozen-source-drift")
    return _create_fixture_from_snapshot(
        root,
        check,
        current,
        identity,
        frozen_source.check_config_fingerprint,
        output_parent,
    )


def consume_verify_snapshot(
    repo_root: str | Path,
    effective_check: dict[str, Any],
    verify_context: dict[str, Any],
    snapshot: dict[str, Any],
    output_parent: str | Path,
) -> dict[str, Any]:
    """复算并消费 Verify 交付的同一输入快照，不建立第二份冻结值。"""
    supplied_root = Path(repo_root)
    if supplied_root.is_symlink():
        _fail("repository-root-invalid")
    root = supplied_root.resolve(strict=True)
    if not root.is_dir():
        _fail("repository-root-invalid")
    if not isinstance(effective_check, dict):
        _fail("verify-context-invalid")
    check_id = effective_check.get("check_id")
    if check_id not in {CHECK_ID, CHANGE_CHECK_ID}:
        _fail("verify-context-invalid")
    check = _rebuild_effective_check(root, effective_check, check_id)
    if (
        not isinstance(verify_context, dict)
        or set(verify_context) != VERIFY_CONTEXT_FIELDS
        or verify_context.get("check_id") != check_id
        or not isinstance(verify_context.get("run_id"), str)
        or not verify_context["run_id"].strip()
        or verify_context.get("check_config_fingerprint") != fingerprint_json(check)
    ):
        _fail("verify-context-invalid")
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        _fail("verify-snapshot-mismatch")
    current = snapshot_check_inputs(root, check)
    if not _exact_json_equal(current, snapshot) or snapshot.get("missing"):
        _fail("verify-snapshot-mismatch")
    identity = _git_identity(root)
    # Repeat the same kernel snapshot before copying; never substitute a newly frozen value.
    if not _exact_json_equal(snapshot_check_inputs(root, check), snapshot):
        _fail("verify-snapshot-mismatch")
    return _create_fixture_from_snapshot(
        root,
        check,
        snapshot,
        identity,
        verify_context["check_config_fingerprint"],
        output_parent,
        verify_context,
    )


def _create_fixture_from_snapshot(
    root: Path,
    check: dict[str, Any],
    current: dict[str, Any],
    identity: dict[str, Any],
    check_config_fingerprint: str,
    output_parent: str | Path,
    verify_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """用既有字节安全 copier 消费已核对快照。"""
    if fingerprint_json(check) != check_config_fingerprint:
        _fail("check-configuration-drift")
    if (
        not _exact_json_equal(snapshot_check_inputs(root, check), current)
        or _git_identity(root) != identity
    ):
        _fail("verify-snapshot-mismatch")
    selected = _closure(current)
    _reject_git_attributes(root, [item["locator"] for item in selected])
    parent = _validate_output(root, output_parent)
    if any(_private(item["locator"]) for item in selected):
        _fail("private-path-rejected")
    stage = Path(tempfile.mkdtemp(prefix=".release-source-", dir=parent))
    stage_stat = stage.lstat()
    stage_id = (stage_stat.st_dev, stage_stat.st_ino, stage_stat.st_uid)
    published = False
    try:
        fixture = stage / "fixture"
        fixture.mkdir(mode=0o700)
        mappings = []
        for item in selected:
            src_path = item["locator"]
            data = _read_source(root, src_path, item)
            dst = fixture / src_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(
                dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fchmod(fd, 0o755 if item["executable"] else 0o644)
            finally:
                os.close(fd)
            mode = "100755" if item["executable"] else "100644"
            mappings.append(
                {
                    "sourcePath": src_path,
                    "fixturePath": src_path,
                    "type": "file",
                    "mode": mode,
                    "sizeBytes": item["size_bytes"],
                    "sha256": item["sha256"],
                }
            )
        _git(fixture, "init", "--quiet")
        # Git may consult config while initialized; all identity/config is fixed and local.
        _git(fixture, "config", "core.hooksPath", os.devnull)
        _git(fixture, "config", "core.autocrlf", "false")
        _git(fixture, "add", "--force", "--", *[m["fixturePath"] for m in mappings])
        tree = _git_text(fixture, "write-tree")
        env = dict(FIXED_ENV)
        env.update(
            {
                "PATH": os.defpath,
                "GIT_AUTHOR_NAME": "LexiFlow Source Bridge",
                "GIT_AUTHOR_EMAIL": "source-bridge@invalid",
                "GIT_COMMITTER_NAME": "LexiFlow Source Bridge",
                "GIT_COMMITTER_EMAIL": "source-bridge@invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00 +0000",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00 +0000",
            }
        )
        commit = (
            _git(
                fixture,
                "commit-tree",
                tree,
                "-m",
                "release source mechanism fixture",
                env=env,
            )
            .decode("ascii")
            .strip()
        )
        _git(fixture, "update-ref", "HEAD", commit)
        if (
            _git_text(fixture, "rev-parse", "HEAD") != commit
            or _git_text(fixture, "rev-parse", "HEAD^{tree}") != tree
        ):
            _fail("fixture-git-object-mismatch")
        if _git(fixture, "status", "--porcelain=v1", "--untracked-files=all"):
            _fail("fixture-not-clean")
        actual = (
            _git(fixture, "ls-tree", "-r", "--full-tree", "--name-only", "HEAD")
            .decode("utf-8", "strict")
            .splitlines()
        )
        if actual != sorted(m["fixturePath"] for m in mappings):
            _fail("fixture-tree-membership-mismatch")
        for mapping in mappings:
            data = (fixture / mapping["fixturePath"]).read_bytes()
            if (
                len(data) != mapping["sizeBytes"]
                or hashlib.sha256(data).hexdigest() != mapping["sha256"]
            ):
                _fail("fixture-copy-mismatch")
            st = (fixture / mapping["fixturePath"]).stat()
            if bool(st.st_mode & 0o111) != (mapping["mode"] == "100755"):
                _fail("fixture-mode-mismatch")
        source_end = _git_identity(root)
        if source_end != identity or not _exact_json_equal(
            snapshot_check_inputs(root, check), current
        ):
            _fail("repository-identity-drift")
        provenance = {
            "schemaVersion": 1,
            "kind": KIND,
            "verificationRunBound": False,
            "verifyInputFingerprint": current["fingerprint"],
            "checkConfigFingerprint": check_config_fingerprint,
            "checkoutHead": identity["head"],
            "indexTree": identity["indexTree"],
            "dirtyFingerprint": identity["dirtyFingerprint"],
            "releaseClosureSha256": fingerprint_json(_closure_description()),
            "files": mappings,
            "fixtureTree": tree,
            "fixtureCommit": commit,
            "purpose": PURPOSE,
            "formalReleaseEligible": False,
        }
        if verify_context is not None:
            provenance["verifyContext"] = {
                "run_id": verify_context["run_id"],
                "check_id": verify_context["check_id"],
                "check_config_fingerprint": verify_context["check_config_fingerprint"],
            }
        receipt = json.dumps(
            provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        receipt_fd = os.open(
            stage / "provenance.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            view = memoryview(receipt)
            while view:
                written = os.write(receipt_fd, view)
                view = view[written:]
        finally:
            os.close(receipt_fd)
        final = parent / f"release-source-{commit[:12]}"
        if final.exists() or final.is_symlink():
            _fail("output-target-exists")
        _publish_noreplace(stage, final)
        published = True
        return {
            "directory": str(final),
            "fixture": str(final / "fixture"),
            "provenance": provenance,
            "provenanceBytes": receipt,
            "provenanceSha256": hashlib.sha256(receipt).hexdigest(),
            "verificationRunBound": False,
            "formalReleaseEligible": False,
        }
    finally:
        if not published:
            try:
                current_stat = stage.lstat()
                if (
                    current_stat.st_dev,
                    current_stat.st_ino,
                    current_stat.st_uid,
                ) == stage_id:
                    shutil.rmtree(stage)
                else:
                    _blocked("staging-cleanup-ownership-unknown")
            except FileNotFoundError:
                pass


def create_release_source_fixture_from_current_check(
    *args: Any, **kwargs: Any
) -> dict[str, Any]:
    """禁止调用者以通用参数包装绕过 frozen object API。"""
    _fail("unsupported-api")
