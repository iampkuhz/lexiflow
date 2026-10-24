"""从固定的本机 handoff 派生并重验正式发行主体。"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.acceptance.evidence.acceptance_record_store import RecordError, parse_json

WORKFLOW_ROOT = "tmp/quality/release-workflow"
MAX_JSON = 2_000_000
MAX_LICENSE_TABLE = 256 * 1024
MAX_ARTIFACT = 2**40
MAX_LICENSES = 64
HEX = re.compile(r"^[a-f0-9]{64}$")
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, int, int]:
    """目录仅绑定节点身份，不把无关兄弟项变化视作来源漂移。"""
    return info.st_dev, info.st_ino, info.st_mode


def _read_relative(
    root: Path, locator: str, *, limit: int = MAX_JSON, digest_only: bool = False
) -> tuple[bytes, str]:
    """经逐段 dirfd/O_NOFOLLOW 读取有界普通文件，并核对目录、文件身份未变。"""
    path = PurePosixPath(locator)
    if (
        path.is_absolute()
        or not locator
        or "\\" in locator
        or "\0" in locator
        or "/".join(path.parts) != locator
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RecordError("release-path-invalid", "relative path is unsafe")
    parts = path.parts
    try:
        directory_fd = os.open(root, _DIR_FLAGS)
    except OSError as exc:
        raise RecordError("release-path-invalid", str(exc)) from None
    ancestors: list[tuple[tuple[str, ...], tuple[int, int, int]]] = []
    try:
        root_info = os.fstat(directory_fd)
        ancestors.append(((), _directory_identity(root_info)))
        for index, part in enumerate(parts[:-1]):
            before = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise RecordError(
                    "release-path-invalid", "ancestor is not a real directory"
                )
            child_fd = os.open(part, _DIR_FLAGS, dir_fd=directory_fd)
            opened = os.fstat(child_fd)
            named = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
            if _directory_identity(before) != _directory_identity(
                opened
            ) or _directory_identity(opened) != _directory_identity(named):
                os.close(child_fd)
                raise RecordError(
                    "release-path-drift", "ancestor changed while opening"
                )
            ancestors.append((parts[: index + 1], _directory_identity(opened)))
            os.close(directory_fd)
            directory_fd = child_fd
        parent_identity = _directory_identity(os.fstat(directory_fd))
        if not ancestors or ancestors[-1][0] != parts[:-1]:
            ancestors.append((parts[:-1], parent_identity))
        leaf = parts[-1]
        before = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size < 1
            or before.st_size > limit
        ):
            raise RecordError("release-file-invalid", "file type or size is invalid")
        fd = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or _identity(before) != _identity(
                opened
            ):
                raise RecordError("release-path-drift", "file changed while opening")
            chunks: list[bytes] = []
            digest = hashlib.sha256()
            remaining = before.st_size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    raise RecordError("release-path-drift", "file shrank while reading")
                digest.update(chunk)
                if not digest_only:
                    chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1):
                raise RecordError("release-path-drift", "file grew while reading")
            after_fd = os.fstat(fd)
            after_name = os.stat(leaf, dir_fd=directory_fd, follow_symlinks=False)
            if _identity(opened) != _identity(after_fd) or _identity(
                after_fd
            ) != _identity(after_name):
                raise RecordError("release-path-drift", "file changed while reading")
            data = (
                str(before.st_size).encode("ascii") if digest_only else b"".join(chunks)
            )
        finally:
            os.close(fd)
        for parts_prefix, expected in ancestors:
            ancestor_fd = os.open(root, _DIR_FLAGS)
            try:
                for component in parts_prefix:
                    child = os.open(component, _DIR_FLAGS, dir_fd=ancestor_fd)
                    os.close(ancestor_fd)
                    ancestor_fd = child
                after = os.fstat(ancestor_fd)
                if _directory_identity(after) != expected:
                    raise RecordError(
                        "release-path-drift", "ancestor changed during read"
                    )
            finally:
                os.close(ancestor_fd)
        return data, digest.hexdigest() if digest_only else hashlib.sha256(
            data
        ).hexdigest()
    except RecordError:
        raise
    except OSError as exc:
        raise RecordError("release-path-invalid", str(exc)) from None
    finally:
        os.close(directory_fd)


def _read_absolute(value: str, *, limit: int = MAX_JSON) -> tuple[bytes, str]:
    """安全读取规范绝对文件；不允许祖先或叶节点为 symlink。"""
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(value) != value:
        raise RecordError("release-path-invalid", "absolute path is not canonical")
    parts = path.parts[1:]
    if not parts:
        raise RecordError("release-path-invalid", "absolute path has no filename")
    return _read_relative(Path(path.anchor), "/".join(parts), limit=limit)


def _digest_absolute(value: str, *, limit: int) -> tuple[int, str]:
    """安全流式摘要规范绝对路径下的普通文件。"""
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(value) != value:
        raise RecordError("release-path-invalid", "absolute path is not canonical")
    parts = path.parts[1:]
    if not parts:
        raise RecordError("release-path-invalid", "absolute path has no filename")
    size_bytes, digest = _read_relative(
        Path(path.anchor), "/".join(parts), limit=limit, digest_only=True
    )
    size = int(size_bytes)
    return size, digest


def _validate_source_bindings(repo: Path, request: dict[str, Any]) -> dict[str, Any]:
    """核验请求所消费的资料字节并返回可复派生绑定。"""
    dataset = request["dataset"]
    if not isinstance(dataset["path"], str) or not HEX.fullmatch(
        str(dataset["sha256"])
    ):
        raise RecordError("release-source-invalid", "dataset locator or digest invalid")
    _, dataset_sha = _digest_absolute(dataset["path"], limit=MAX_ARTIFACT)
    if dataset_sha != dataset["sha256"]:
        raise RecordError("release-source-drift", "dataset digest mismatch")
    ledger_path = "ops/release/distribution-licenses.json"
    if _git(repo, "ls-files", "--error-unmatch", "--", ledger_path) != ledger_path:
        raise RecordError("release-source-invalid", "license ledger is not tracked")
    ledger_bytes, ledger_sha = _read_relative(
        repo, ledger_path, limit=MAX_LICENSE_TABLE
    )
    try:
        ledger = parse_json(ledger_bytes, "distribution license ledger")
    except RecordError:
        raise RecordError(
            "release-source-invalid", "license ledger JSON invalid"
        ) from None
    if (
        not isinstance(ledger, dict)
        or set(ledger) != {"schema_version", "licenses"}
        or ledger["schema_version"] != "lexiflow.distribution-licenses.v1"
        or not isinstance(ledger["licenses"], list)
        or not 1 <= len(ledger["licenses"]) <= MAX_LICENSES
    ):
        raise RecordError("release-source-invalid", "license ledger shape invalid")
    notices_root = request["noticesRoot"]
    if (
        not isinstance(notices_root, str)
        or not Path(notices_root).is_absolute()
        or os.path.normpath(notices_root) != notices_root
    ):
        raise RecordError("release-path-invalid", "notice root is not canonical")
    declared: dict[str, tuple[int, str]] = {}
    for item in ledger["licenses"]:
        if not isinstance(item, dict):
            raise RecordError("release-source-invalid", "license declaration invalid")
        relative, size, expected = (
            item.get("noticePath"),
            item.get("noticeBytes"),
            item.get("noticeSha256"),
        )
        if (
            not isinstance(relative, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 1 <= size <= MAX_ARTIFACT
            or not isinstance(expected, str)
            or not HEX.fullmatch(expected)
        ):
            raise RecordError("release-source-invalid", "notice declaration invalid")
        if relative in declared:
            if declared[relative] != (size, expected):
                raise RecordError(
                    "release-source-invalid", "duplicate notice declaration conflicts"
                )
            continue
        actual_size, actual_sha = _digest_relative_absolute_root(notices_root, relative)
        if actual_size != size or actual_sha != expected:
            raise RecordError("release-source-drift", "notice digest or size mismatch")
        declared[relative] = (size, expected)
    return {
        "schema_version": "lexiflow.release-source-bindings.v1",
        "dataset": {"path": dataset["path"], "sha256": dataset_sha},
        "license_ledger": {"path": ledger_path, "sha256": ledger_sha},
        "notices": [
            {"path": path, "bytes": value[0], "sha256": value[1]}
            for path, value in sorted(declared.items())
        ],
    }


def _digest_relative_absolute_root(root: str, locator: str) -> tuple[int, str]:
    """从规范绝对目录根安全流式读取声明的相对notice路径。"""
    path = PurePosixPath(locator)
    if (
        path.is_absolute()
        or not locator
        or "\\" in locator
        or "\0" in locator
        or "/".join(path.parts) != locator
        or any(p in {"", ".", ".."} for p in path.parts)
    ):
        raise RecordError("release-path-invalid", "notice relative path is unsafe")
    if not Path(root).is_absolute() or os.path.normpath(root) != root:
        raise RecordError("release-path-invalid", "notice root is not canonical")
    return _digest_absolute(str(Path(root) / locator), limit=MAX_ARTIFACT)


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RecordError("release-source-invalid", "git source unavailable") from None
    if result.returncode:
        raise RecordError("release-source-invalid", "git source unavailable")
    return result.stdout.strip()


def _producer_build_identity(repo: Path, tag: str) -> dict[str, Any]:
    """调用仓库实际身份生产器，仅在建立发行主体时完整计算一次源码身份。"""
    script = repo / "ops/release/integrity/source-build-identity.mjs"
    if script.is_symlink() or not script.is_file():
        raise RecordError(
            "release-build-identity-unavailable", "identity producer missing"
        )
    try:
        result = subprocess.run(
            ["node", str(script), "--tag", tag],
            cwd=repo,
            capture_output=True,
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RecordError(
            "release-build-identity-unavailable", "identity producer unavailable"
        ) from None
    if result.returncode:
        raise RecordError(
            "release-build-identity-invalid", "identity producer rejected source"
        )
    try:
        identity = parse_json(result.stdout, "source build identity")
    except RecordError:
        raise RecordError(
            "release-build-identity-invalid", "identity producer output invalid"
        ) from None
    if not isinstance(identity, dict):
        raise RecordError(
            "release-build-identity-invalid", "identity must be an object"
        )
    return identity


def read_release_subject(repo: Path, *, verify_identity: bool = True) -> dict[str, Any]:
    """读取固定 handoff，派生发行主体并核验源码、请求与候选绑定。"""
    repo = repo.resolve()
    version_path, _ = _read_relative(repo, "ops/release/version.txt", limit=64)
    version = version_path.decode("utf-8").strip()
    if (
        not re.fullmatch(
            r"(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})",
            version,
        )
        or version == "0.0.0"
    ):
        raise RecordError("release-version-invalid", "version file invalid")
    workflow = f"{WORKFLOW_ROOT}/{version}"
    handoff_locator = f"{workflow}/handoff.json"
    runtime_locator = f"{workflow}/candidate-runtime-request.json"
    raw, handoff_sha = _read_relative(repo, handoff_locator)
    try:
        handoff = parse_json(raw, "release handoff")
    except RecordError:
        raise RecordError("release-handoff-invalid", "invalid JSON") from None
    keys = {
        "schemaVersion",
        "host",
        "root",
        "releaseTag",
        "buildIdentity",
        "requestSha256",
        "runtimeRequestSha256",
        "requestFile",
        "target",
    }
    if (
        not isinstance(handoff, dict)
        or set(handoff) != keys
        or handoff["schemaVersion"] != 1
    ):
        raise RecordError("release-handoff-invalid", "handoff keys/schema differ")
    if handoff["root"] != str(repo) or handoff["host"] != socket.gethostname():
        raise RecordError("release-handoff-environment-drift", "host/root mismatch")
    commit = _git(repo, "rev-parse", "--verify", "HEAD^{commit}")
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise RecordError("release-source-dirty", "repository must be clean")
    tag = handoff["releaseTag"]
    if not isinstance(tag, str) or not re.fullmatch(
        r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", tag
    ):
        raise RecordError("release-tag-drift", "release tag invalid")
    if _git(repo, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}") != commit:
        raise RecordError("release-tag-drift", "tag must resolve to current commit")
    identity = handoff["buildIdentity"]
    if not isinstance(identity, dict) or identity.get("sourceCommit") != commit:
        raise RecordError("release-build-identity-drift", "source commit mismatch")
    if (
        identity.get("softwareVersion") != tag[1:]
        or identity.get("dirty") is not False
        or identity.get("channel") != "release"
    ):
        raise RecordError(
            "release-build-identity-drift", "tag/version/build identity mismatch"
        )
    for key in ("requestSha256", "runtimeRequestSha256"):
        if not isinstance(handoff[key], str) or not HEX.fullmatch(handoff[key]):
            raise RecordError("release-handoff-invalid", f"{key} invalid")
    if verify_identity and _producer_build_identity(repo, tag) != identity:
        raise RecordError(
            "release-build-identity-drift", "identity differs from current source proof"
        )

    runtime_bytes, runtime_sha = _read_relative(repo, runtime_locator)
    if runtime_sha != handoff["runtimeRequestSha256"]:
        raise RecordError("release-runtime-drift", "runtime request digest mismatch")
    try:
        runtime = parse_json(runtime_bytes, "release runtime request")
    except RecordError:
        raise RecordError(
            "release-runtime-invalid", "invalid runtime request"
        ) from None
    if runtime != {"target": handoff["target"]}:
        raise RecordError("release-runtime-drift", "candidate target mismatch")

    request_path = handoff["requestFile"]
    if not isinstance(request_path, str):
        raise RecordError("release-request-invalid", "request path must be a string")
    request_bytes, request_sha = _read_absolute(request_path)
    if request_sha != handoff["requestSha256"]:
        raise RecordError("release-request-drift", "request digest mismatch")
    try:
        request = parse_json(request_bytes, "release request")
    except RecordError:
        raise RecordError("release-request-invalid", "request JSON invalid") from None
    request_keys = {
        "releaseTag",
        "dataset",
        "noticesRoot",
        "outputParent",
        "endpoint",
    }
    dataset_keys = {
        "path",
        "sha256",
        "releaseId",
        "preparationId",
        "ruleId",
        "sqlVersion",
    }
    candidate_keys = {"candidateDirectory", "candidateSha256"}
    dataset = request.get("dataset") if isinstance(request, dict) else None
    if (
        not isinstance(request, dict)
        or set(request) != request_keys
        or not isinstance(dataset, dict)
        or set(dataset) != dataset_keys
        or request.get("releaseTag") != tag
        or not all(
            isinstance(request.get(field), str)
            for field in ("noticesRoot", "outputParent", "endpoint")
        )
        or not request["endpoint"].startswith("unix:///")
    ):
        raise RecordError("release-request-drift", "request subject mismatch")
    source_bindings = _validate_source_bindings(repo, request)

    candidates: dict[str, dict[str, Any]] = {}
    for label in ("target",):
        candidate = handoff[label]
        if (
            not isinstance(candidate, dict)
            or set(candidate) != candidate_keys
            or not isinstance(candidate.get("candidateDirectory"), str)
            or not isinstance(candidate.get("candidateSha256"), str)
            or not HEX.fullmatch(candidate["candidateSha256"])
        ):
            raise RecordError("release-candidate-invalid", f"{label} candidate invalid")
        marker_path = os.path.join(candidate["candidateDirectory"], "candidate.json")
        marker_bytes, marker_sha = _read_absolute(marker_path)
        if marker_sha != candidate["candidateSha256"]:
            raise RecordError(
                "release-candidate-drift", f"{label} marker digest mismatch"
            )
        try:
            marker = parse_json(marker_bytes, "release candidate marker")
        except RecordError:
            raise RecordError(
                "release-candidate-invalid", "candidate JSON invalid"
            ) from None
        if not isinstance(marker, dict) or not isinstance(
            marker.get("buildIdentity"), dict
        ):
            raise RecordError(
                "release-candidate-invalid", "candidate build identity missing"
            )
        build = marker["buildIdentity"]
        if not isinstance(build.get("buildId"), str) or not isinstance(
            build.get("sourceCommit"), str
        ):
            raise RecordError(
                "release-candidate-invalid", "candidate build identity fields invalid"
            )
        candidates[label] = {
            "candidate_directory": candidate["candidateDirectory"],
            "candidate_sha256": marker_sha,
            "build_identity": build,
        }
    target_build = candidates["target"]["build_identity"]
    if target_build != identity:
        raise RecordError(
            "release-candidate-mismatch",
            "candidate identity is not the expected target",
        )
    return {
        "schema_version": "lexiflow.release-subject.v2",
        "handoff": {"locator": handoff_locator, "sha256": handoff_sha},
        "release_tag": tag,
        "commit_sha": commit,
        "build_identity": identity,
        "request_sha256": request_sha,
        "runtime_request_sha256": runtime_sha,
        "source_bindings": source_bindings,
        **candidates,
    }


def verify_release_subject(repo: Path, subject: dict[str, Any]) -> None:
    """以轻量来源重读核验已绑定 subject；完整身份生产器只在首次建立时运行。"""
    if (
        not isinstance(subject, dict)
        or read_release_subject(repo, verify_identity=False) != subject
    ):
        raise RecordError(
            "release-subject-drift", "current source differs from bound release subject"
        )
