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
from pathlib import Path
from typing import Any

from scripts.verification.execution.check_runner import (
    fingerprint_json,
    snapshot_check_inputs,
    resolve_child_check,
    operation_config,
)

CHECK_ID = "eng.release.lifecycle-runtime"
VERIFY_CONTEXT_FIELDS = frozenset({"run_id", "check_id"})
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

# 固定最低 build 锚点；实际复制范围只由规范 Check 的完整冻结闭包派生。
FIXED_FILES = frozenset(
    {
        ".gitignore",
        "AGENTS.md",
        "infra/postgres/schema.sql",
        "ops/release/build/release-image-build-plan.mjs",
        "ops/release/integrity/extension-archive-integrity.mjs",
        "ops/release/integrity/artifact-build-identity.mjs",
        "ops/release/build/release-build-process.mjs",
        "ops/release/build/release-image-builder.mjs",
        "ops/release/build/release-candidate-assembler.mjs",
        "ops/release/integrity/release-candidate-verifier.mjs",
        "ops/release/runtime/release-installation-docker.sh",
        "ops/release/runtime/release-installation-state.sh",
        "ops/release/runtime/release-installation-lifecycle.sh",
        "ops/release/integrity/release-artifact-manifest.mjs",
        "ops/release/build/package-release-artifacts.mjs",
        "ops/release/workflow/run-release-build-pipeline.mjs",
        "ops/release/test_tools/build-synthetic-release-candidates.mjs",
        "ops/release/build/release-runtime-entry-generator.mjs",
        "ops/release/runtime/release-artifact-verification.sh",
        "ops/release/integrity/source-build-identity.mjs",
        "ops/release/version.txt",
        "ops/release/third-party-notices.md",
        "ops/docker/base-images.json",
        "ops/docker/Dockerfile",
        "ops/docker/Dockerfile.postgres",
        "ops/docker/.dockerignore",
        "ops/docker/initialize-postgres-app-role.sh",
        "ops/docker/compose.yaml",
        "ops/docker/run-container-command.sh",
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
        "backend/product/api/build.gradle.kts",
        "backend/product/api/gradle.lockfile",
        "backend/product/adapters/build.gradle.kts",
        "backend/product/adapters/gradle.lockfile",
        "backend/product/enrichment/build.gradle.kts",
        "backend/product/enrichment/gradle.lockfile",
        "backend/product/lexicon/gradle.lockfile",
        "backend/verification/architecture/build.gradle.kts",
        "backend/verification/architecture/gradle.lockfile",
        "backend/verification/integration/build.gradle.kts",
        "backend/verification/integration/gradle.lockfile",
        "backend/verification/quality-gates/build.gradle.kts",
        "backend/verification/quality-gates/gradle.lockfile",
    }
)
# 仅实际 bootJar 来源桥要求这些资料生成器锚点；消费预建候选的 Check 不构建它们。
BUILD_GENERATION_FILES = frozenset(
    {
        "ops/dataset/dataset-generation-identity.mjs",
        "ops/dataset/generation-inputs.json",
        "ops/dataset/ecdict-source.lock.json",
        "ops/dataset/prepare_ecdict_bundle.py",
        "ops/podman/source-tools.Containerfile",
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
_GENERATED = frozenset(
    {"build", "dist", "node_modules", ".gradle", ".kotlin", "__pycache__"}
)
_SOURCE_BUILD_DIRS = frozenset({"ops/release/build", "extension/scripts/build"})
_PRIVATE = frozenset({".git", ".local", ".npmrc", "secrets"})


class BridgeError(RuntimeError):
    """带固定状态与错误码的来源桥接拒绝结果。"""

    def __init__(self, status: str, code: str) -> None:
        self.status, self.code = status, code
        super().__init__(f"{status}/{code}")


def _fail(code: str) -> None:
    raise BridgeError("FAIL", code)


def _blocked(code: str) -> None:
    raise BridgeError("BLOCKED", code)


def _git(
    root: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input: bytes | None = None,
) -> bytes:
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
            input=input,
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
    if sys.platform not in {"darwin", "linux"}:
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "linux":
            # Linux CI 的合成夹具使用 renameat2 的原子不覆盖语义。
            renameat2 = libc.renameat2
            renameat2.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            renameat2.restype = ctypes.c_int
            ctypes.set_errno(0)
            result = renameat2(
                -100, os.fsencode(source), -100, os.fsencode(destination), 1
            )
            return result, ctypes.get_errno() if result != 0 else 0
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


def _safe_path(path: str) -> bool:
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


def _generated(path: str) -> bool:
    """仅豁免两个源码 build 目录，内部产物目录和字节码仍拒绝。"""
    parts = path.split("/")
    return any(
        part.endswith(".pyc")
        or (
            part in _GENERATED
            and "/".join(parts[: index + 1]) not in _SOURCE_BUILD_DIRS
        )
        for index, part in enumerate(parts)
    )


def _closure(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    files = snapshot["files"]
    by_path: dict[str, dict[str, Any]] = {}
    for item in files:
        path = item.get("locator") if isinstance(item, dict) else None
        if not isinstance(path, str) or not _safe_path(path) or _private(path):
            _fail("unsafe-frozen-locator")
        if _generated(path):
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
    for root in FIXED_DIRS:
        descendants = {
            p
            for p in by_path
            if p.startswith(root + "/") and _included_tree_file(root, p)
        }
        if not descendants:
            _fail("release-closure-incomplete")
    # 实际冻结闭包整体复制；固定清单仅证明真实 build 必需读取的最低覆盖。
    return [by_path[p] for p in sorted(by_path)]


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
        "buildGenerationFiles": sorted(BUILD_GENERATION_FILES),
        "generatedExcluded": sorted(_GENERATED),
        "sourceBuildDirectories": sorted(_SOURCE_BUILD_DIRS),
    }


def consume_verify_snapshot(
    repo_root: str | Path,
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
    if (
        not isinstance(verify_context, dict)
        or set(verify_context) != VERIFY_CONTEXT_FIELDS
        or verify_context.get("check_id") != CHECK_ID
    ):
        _fail("verify-context-invalid")
    try:
        import uuid

        if str(uuid.UUID(verify_context["run_id"])) != verify_context["run_id"]:
            _fail("verify-context-invalid")
        check = resolve_child_check(root, CHECK_ID)
    except (KeyError, ValueError):
        _fail("verify-context-invalid")
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        _fail("verify-snapshot-mismatch")
    current = snapshot_check_inputs(root, check)
    if not _exact_json_equal(current, snapshot) or snapshot.get("missing"):
        _fail("verify-snapshot-mismatch")
    # 此入口已认证为实际 bootJar lifecycle Check；candidate 消费者不经过本分支。
    if BUILD_GENERATION_FILES - {item["locator"] for item in snapshot["files"]}:
        _fail("release-generation-closure-incomplete")
    # Repeat the same kernel snapshot before copying; never substitute a newly frozen value.
    if not _exact_json_equal(snapshot_check_inputs(root, check), snapshot):
        _fail("verify-snapshot-mismatch")
    return _create_fixture_from_snapshot(
        root,
        check,
        snapshot,
        fingerprint_json(operation_config(check)),
        output_parent,
        verify_context,
    )


def _create_fixture_from_snapshot(
    root: Path,
    check: dict[str, Any],
    current: dict[str, Any],
    check_config_fingerprint: str,
    output_parent: str | Path,
    verify_context: dict[str, Any],
) -> dict[str, Any]:
    """用既有字节安全 copier 消费已核对快照。"""
    if fingerprint_json(check) != check_config_fingerprint:
        _fail("check-configuration-drift")
    if not _exact_json_equal(snapshot_check_inputs(root, check), current):
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
        if not _exact_json_equal(snapshot_check_inputs(root, check), current):
            _fail("repository-identity-drift")
        provenance = {
            "schemaVersion": 1,
            "kind": KIND,
            "verificationRunBound": False,
            "verifyInputFingerprint": current["fingerprint"],
            "checkConfigFingerprint": check_config_fingerprint,
            "releaseClosureSha256": fingerprint_json(_closure_description()),
            "files": mappings,
            "fixtureTree": tree,
            "fixtureCommit": commit,
            "purpose": PURPOSE,
            "formalReleaseEligible": False,
        }
        provenance["verifyContext"] = {
            "run_id": verify_context["run_id"],
            "check_id": verify_context["check_id"],
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
