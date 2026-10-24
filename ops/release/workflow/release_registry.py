"""固定 GHCR 镜像提升；不构建、不改变候选、不覆盖现存标签。"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import stat
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any
from urllib.parse import quote

from ops.release.host_resources import release_process_control as release_process

MAX_ARCHIVE = 2 * 1024**3
SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
ROLES = ("runtime-image",)


class RegistryError(RuntimeError):
    """安全的固定原因，不携带命令输出、路径、响应体或凭据。"""

    def __init__(
        self,
        reason: str,
        stage: str | None = None,
        role: str | None = None,
        write_unknown: bool = False,
    ):
        self.reason, self.stage, self.role, self.write_unknown = (
            reason,
            stage,
            role,
            write_unknown,
        )
        super().__init__(reason)


class _PrivateDirectory:
    def __init__(self, role: str | None = None):
        self.role = role
        self.path: str | None = None
        self.write_started = False

    def __enter__(self) -> Path:
        self.path = str(
            Path(tempfile.mkdtemp(prefix="lexiflow-registry-")).resolve(strict=True)
        )
        os.chmod(self.path, 0o700)
        return Path(self.path)

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        try:
            if self.path:
                shutil.rmtree(self.path)
        except OSError:
            _fail("registry-cleanup-failed", "cleanup", self.role, self.write_started)
        return False


def _fail(
    reason: str,
    stage: str | None = None,
    role: str | None = None,
    unknown: bool = False,
) -> None:
    raise RegistryError(reason, stage, role, unknown)


def _read_regular(root: Path, relative: str, limit: int) -> bytes:
    """Read bounded file while refusing symlinks in every path component."""
    fd, parent_fd, before, leaf = _open_candidate_file(root, relative, limit)
    try:
        chunks = bytearray()
        while len(chunks) <= limit:
            part = os.read(fd, min(1024 * 1024, limit + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
        _check_stable_file(fd, parent_fd, leaf, before)
        if len(chunks) > limit:
            _fail("registry-candidate-invalid", "candidate")
        return bytes(chunks)
    except OSError:
        _fail("registry-candidate-invalid", "candidate")
    finally:
        os.close(fd)
        os.close(parent_fd)


def _hash_regular(root: Path, relative: str, limit: int) -> tuple[int, str]:
    """Hash a bounded regular file without loading an image archive into memory."""
    fd, parent_fd, before, leaf = _open_candidate_file(root, relative, limit)
    try:
        digest, size = hashlib.sha256(), 0
        while chunk := os.read(fd, 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
        _check_stable_file(fd, parent_fd, leaf, before)
        return size, digest.hexdigest()
    except OSError:
        _fail("registry-candidate-invalid", "candidate")
    finally:
        os.close(fd)
        os.close(parent_fd)


def _file_stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _open_candidate_file(
    root: Path, relative: str, limit: int
) -> tuple[int, int, os.stat_result, str]:
    parts = Path(relative).parts
    if (
        not parts
        or Path(relative).is_absolute()
        or any(p in ("", ".", "..") for p in parts)
    ):
        _fail("registry-candidate-invalid", "candidate")
    root_absolute = root.absolute()
    if any(component in (".", "..") for component in root_absolute.parts):
        _fail("registry-candidate-invalid", "candidate")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent = os.open("/", flags)
    file_fd = None
    try:
        for component in (*root_absolute.parts[1:], *parts[:-1]):
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        leaf = parts[-1]
        file_fd = os.open(
            leaf,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            _fail("registry-candidate-invalid", "candidate")
        return file_fd, parent, before, leaf
    except RegistryError:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent)
        raise
    except OSError:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent)
        _fail("registry-candidate-invalid", "candidate")


def _check_stable_file(
    fd: int, parent_fd: int, leaf: str, before: os.stat_result
) -> None:
    after = os.fstat(fd)
    named = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    if _file_stamp(before) != _file_stamp(after) or _file_stamp(after) != _file_stamp(
        named
    ):
        _fail("registry-candidate-mutated", "candidate")


def _copy_verified_archive(
    root: Path, relative: str, destination: Path, expected_size: int, expected_sha: str
) -> None:
    fd, parent_fd, before, leaf = _open_candidate_file(root, relative, MAX_ARCHIVE)
    output_fd = None
    try:
        if before.st_size != expected_size:
            _fail("registry-archive-invalid", "candidate")
        output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        digest, size = hashlib.sha256(), 0
        while chunk := os.read(fd, 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                view = view[os.write(output_fd, view) :]
        os.fsync(output_fd)
        _check_stable_file(fd, parent_fd, leaf, before)
        if size != expected_size or digest.hexdigest() != expected_sha:
            _fail("registry-archive-invalid", "candidate")
    except RegistryError:
        if destination.exists():
            destination.unlink()
        raise
    except OSError:
        if destination.exists():
            destination.unlink()
        _fail("registry-archive-invalid", "candidate")
    finally:
        if output_fd is not None:
            os.close(output_fd)
        os.close(fd)
        os.close(parent_fd)


def _https(
    method: str, path: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], bytes]:
    """仅访问固定 ghcr.io，拒绝重定向并限制时间/响应。"""
    deadline = time.monotonic() + 15
    conn = http.client.HTTPSConnection(
        "ghcr.io", timeout=10, context=ssl.create_default_context()
    )
    active_socket = None

    def expire() -> None:
        if active_socket is not None:
            try:
                active_socket.shutdown(2)
            except OSError:
                pass
        conn.close()

    timer = threading.Timer(15, expire)
    timer.daemon = True
    timer.start()
    try:
        conn.connect()
        active_socket = conn.sock
        if time.monotonic() >= deadline:
            _fail("registry-request-deadline", "remote-read")
        conn.putrequest(method, path, skip_accept_encoding=True)
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        conn.endheaders()
        response = conn.getresponse()
        body_bytes = bytearray()
        while True:
            if time.monotonic() >= deadline:
                _fail("registry-request-deadline", "remote-read")
            chunk = response.read1(
                min(64 * 1024, 4 * 1024 * 1024 + 1 - len(body_bytes))
            )
            if not chunk:
                break
            body_bytes.extend(chunk)
            if len(body_bytes) > 4 * 1024 * 1024:
                _fail("registry-response-too-large", "remote-read")
        body = bytes(body_bytes)
        if 300 <= response.status < 400:
            _fail("registry-redirect-rejected", "remote-read")
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    except RegistryError:
        raise
    except (OSError, http.client.HTTPException, ssl.SSLError, TimeoutError):
        _fail("registry-network-unknown", "remote-read")
    finally:
        timer.cancel()
        conn.close()


def _manifest(
    repo: str,
    reference: str,
    *,
    owner: str | None = None,
    token: str | None = None,
    anonymous: bool = True,
) -> tuple[int, dict[str, str], bytes]:
    path = f"/v2/{repo}/manifests/{reference}"
    accept = "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json"
    basic = None
    if not anonymous:
        if not owner or not token:
            _fail("registry-token-missing", "auth")
        basic = base64.b64encode(f"{owner}:{token}".encode()).decode("ascii")
    status, headers, body = _https("GET", path, {"Accept": accept})
    if status != 401:
        return status, headers, body
    challenge = headers.get("www-authenticate", "")
    match = re.fullmatch(
        r'Bearer realm="https://ghcr\.io/token",service="ghcr\.io",scope="repository:([^" ]+):pull(?:,push)?"',
        challenge,
    )
    if not match or match.group(1) != repo:
        _fail("registry-auth-challenge-invalid", "auth")
    token_headers = {"Accept": "application/json"}
    if basic:
        token_headers["Authorization"] = f"Basic {basic}"
    status, th, tb = _https(
        "GET",
        f"/token?service=ghcr.io&scope={quote(f'repository:{repo}:pull', safe=':/')}",
        token_headers,
    )
    if status != 200:
        return status, th, tb
    try:
        parsed = json.loads(tb)
        bearer = parsed.get("token") or parsed.get("access_token")
        if not isinstance(bearer, str) or not bearer:
            _fail("registry-token-invalid", "auth")
    except RegistryError:
        raise
    except (ValueError, TypeError, KeyError):
        _fail("registry-token-invalid", "auth")
    status, headers, body = _https(
        "GET", path, {"Accept": accept, "Authorization": f"Bearer {bearer}"}
    )
    # bearer is never returned, logged or retained.
    return status, headers, body


def _image_manifest(
    repo: str,
    reference: str,
    *,
    owner: str | None = None,
    token: str | None = None,
    anonymous: bool = True,
) -> tuple[str, str]:
    status, headers, body = _manifest(
        repo, reference, owner=owner, token=token, anonymous=anonymous
    )
    if status != 200:
        _fail(
            "registry-not-public"
            if anonymous and status in (401, 403)
            else "registry-tag-unavailable",
            "anonymous-read" if anonymous else "remote-read",
        )
    try:
        obj = json.loads(body)
        config = obj["config"]["digest"]
        digest = headers.get("docker-content-digest")
        actual = "sha256:" + hashlib.sha256(body).hexdigest()
        if not isinstance(config, str) or not SHA.fullmatch(config) or digest != actual:
            _fail("registry-manifest-digest-invalid", "remote-read")
        return digest, config
    except RegistryError:
        raise
    except (ValueError, TypeError, KeyError):
        _fail("registry-manifest-invalid", "remote-read")


def _run(
    argv: list[str], *, endpoint: str, input_stream: Any = None, timeout: int = 300
) -> Any:
    # Never inherit caller secrets, proxy, or credential configuration.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": tempfile.gettempdir(),
        "LANG": "C",
        "LC_ALL": "C",
        "CONTAINER_HOST": endpoint,
    }
    try:
        facts = release_process.run_owned(
            argv, Path.cwd(), env, timeout, 1024 * 1024, input_stream=input_stream
        )
    except release_process.ProcessError:
        _fail("registry-process-failed", "podman")
    if not facts.cleanup_succeeded:
        _fail("registry-process-cleanup-failed", "podman")
    if facts.timed_out or facts.overflow:
        _fail("registry-process-limited", "podman")
    if facts.returncode != 0:
        _fail("registry-process-failed", "podman")
    return facts


def _inspect_image(image_id: str, role: str, identity: dict, endpoint: str) -> None:
    inspected = _run(
        ["podman", "--url", endpoint, "image", "inspect", image_id],
        endpoint=endpoint,
    )
    try:
        image_info = json.loads(inspected.stdout)
        image_info = image_info[0] if isinstance(image_info, list) else image_info
        labels = image_info["Config"]["Labels"]
        inspected_id = image_info["Id"]
        if isinstance(inspected_id, str) and HEX.fullmatch(inspected_id):
            inspected_id = "sha256:" + inspected_id
        if (
            inspected_id != image_id
            or image_info["Architecture"] != "arm64"
            or image_info["Os"] != "linux"
            or not isinstance(labels, dict)
        ):
            _fail("registry-image-inspect-mismatch", "inspect", role)
        expected = {
            "org.opencontainers.image.version": identity["softwareVersion"],
            "org.opencontainers.image.revision": identity["sourceCommit"],
            "io.lexiflow.build-id": identity["buildId"],
            "io.lexiflow.source-sha256": identity["sourceSha256"],
            "io.lexiflow.image-role": "runtime",
            "io.lexiflow.deployment-version": identity["baseVersion"].lower(),
        }
        if any(labels.get(key) != value for key, value in expected.items()):
            _fail("registry-image-label-mismatch", "inspect", role)
    except RegistryError:
        raise
    except (ValueError, TypeError, KeyError, IndexError):
        _fail("registry-image-inspect-invalid", "inspect", role)


def publish_images(
    *,
    candidate: Path,
    manifest: dict,
    identity: dict,
    owner: str,
    repo: str,
    endpoint: str,
    candidate_sha256: str,
    manifest_sha256: str,
    revalidate: Callable[[], None],
    existing_only: bool = False,
) -> dict:
    """发布已验证候选镜像并核对远端摘要，不授予发行批准。"""
    workspace_state = _PrivateDirectory()
    try:
        with workspace_state as workspace:
            return _publish_images(
                candidate=candidate,
                manifest=manifest,
                identity=identity,
                owner=owner,
                repo=repo,
                endpoint=endpoint,
                candidate_sha256=candidate_sha256,
                manifest_sha256=manifest_sha256,
                revalidate=revalidate,
                existing_only=existing_only,
                workspace=workspace,
                workspace_state=workspace_state,
            )
    except RegistryError:
        raise
    except Exception:  # noqa: BLE001 -- sanitize any unexpected post-write exception.
        _fail(
            "registry-operation-failed",
            "publish",
            workspace_state.role,
            workspace_state.write_started,
        )


def _publish_images(
    *,
    candidate: Path,
    manifest: dict,
    identity: dict,
    owner: str,
    repo: str,
    endpoint: str,
    candidate_sha256: str,
    manifest_sha256: str,
    revalidate: Callable[[], None],
    existing_only: bool,
    workspace: Path,
    workspace_state: _PrivateDirectory,
) -> dict:
    """将 Formal 已验候选镜像按固定 GHCR 路径发布并返回 digest 绑定映射。"""
    if (
        not isinstance(owner, str)
        or not re.fullmatch(r"[A-Za-z0-9-]+", owner)
        or not isinstance(repo, str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo)
    ):
        _fail("registry-origin-invalid", "input")
    if (
        not isinstance(endpoint, str)
        or not endpoint.startswith("unix://")
        or not endpoint[7:]
        or not os.path.isabs(endpoint[7:])
    ):
        _fail("registry-endpoint-invalid", "input")
    try:
        st = os.stat(endpoint[7:])
        if not stat.S_ISSOCK(st.st_mode):
            _fail("registry-endpoint-invalid", "input")
    except RegistryError:
        raise
    except OSError:
        _fail("registry-endpoint-invalid", "input")
    try:
        st = os.lstat(endpoint[7:])
        if not stat.S_ISSOCK(st.st_mode):
            _fail("registry-endpoint-invalid", "input")
    except RegistryError:
        raise
    except OSError:
        _fail("registry-endpoint-invalid", "input")
    if (
        not isinstance(identity, dict)
        or set(identity)
        != {
            "schemaVersion",
            "baseVersion",
            "softwareVersion",
            "chromeVersion",
            "sourceCommit",
            "sourceSha256",
            "buildId",
            "dirty",
            "channel",
        }
        or identity.get("schemaVersion") != 1
        or identity.get("channel") != "release"
        or identity.get("dirty") is not False
        or identity.get("baseVersion") != identity.get("softwareVersion")
        or not re.fullmatch(
            r"\d+\.\d+\.\d+\.\d+", str(identity.get("chromeVersion", ""))
        )
        or not re.fullmatch(r"[0-9a-f]{40,64}", str(identity.get("sourceCommit", "")))
        or not re.fullmatch(r"\d+\.\d+\.\d+", str(identity.get("softwareVersion", "")))
        or any(
            not HEX.fullmatch(str(identity.get(k, "")))
            for k in ("sourceSha256", "buildId")
        )
        or manifest.get("buildIdentity") != identity
        or manifest.get("softwareVersion") != identity.get("softwareVersion")
        or manifest.get("sourceCommit") != identity.get("sourceCommit")
    ):
        _fail("registry-identity-invalid", "input")
    for value in (candidate_sha256, manifest_sha256):
        if not isinstance(value, str) or not HEX.fullmatch(value):
            _fail("registry-hash-invalid", "input")
    assets = manifest.get("artifacts")
    if not isinstance(assets, list):
        _fail("registry-candidate-invalid", "candidate")
    images = [x for x in assets if isinstance(x, dict) and x.get("role") in ROLES]
    if len(images) != 1 or {x.get("role") for x in images} != set(ROLES):
        _fail("registry-image-set-invalid", "candidate")
    candidate_bytes = _read_regular(candidate, "candidate.json", 4 * 1024 * 1024)
    manifest_bytes = _read_regular(candidate, "payload/manifest.json", 4 * 1024 * 1024)
    if (
        hashlib.sha256(candidate_bytes).hexdigest() != candidate_sha256
        or hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256
    ):
        _fail("registry-candidate-hash-mismatch", "candidate")
    try:
        if json.loads(manifest_bytes) != manifest:
            _fail("registry-candidate-manifest-mismatch", "candidate")
    except (ValueError, TypeError):
        _fail("registry-candidate-manifest-invalid", "candidate")
    try:
        candidate_meta = json.loads(candidate_bytes)
    except (ValueError, TypeError):
        _fail("registry-candidate-metadata-invalid", "candidate")
    if (
        not isinstance(candidate_meta, dict)
        or candidate_meta.get("buildIdentity") != identity
        or candidate_meta.get("softwareVersion") != identity["softwareVersion"]
        or candidate_meta.get("sourceCommit") != identity["sourceCommit"]
        or not isinstance(candidate_meta.get("imageCandidates"), list)
        or len(candidate_meta["imageCandidates"]) != 1
        or candidate_meta["imageCandidates"][0].get("platform") != "linux/arm64"
    ):
        _fail("registry-identity-invalid", "candidate")
    token = os.environ.get("GITHUB_TOKEN")
    if not isinstance(token, str) or not token:
        _fail("registry-token-missing", "auth")
    # Validate all local bytes before the first Podman or registry write.
    staged_archives: dict[str, Path] = {}
    for item in images:
        path = item.get("path")
        if (
            not isinstance(path, str)
            or Path(path).is_absolute()
            or ".." in Path(path).parts
        ):
            _fail("registry-archive-invalid", "candidate", item.get("role"))
        if (
            type(item.get("bytes")) is not int
            or not 0 < item["bytes"] <= MAX_ARCHIVE
            or not HEX.fullmatch(str(item.get("sha256", "")))
        ):
            _fail("registry-archive-invalid", "candidate", item.get("role"))
        staged_archive = workspace / f"{item['role']}.tar"
        _copy_verified_archive(
            candidate,
            "payload/" + path,
            staged_archive,
            item.get("bytes"),
            item.get("sha256"),
        )
        staged_archives[item["role"]] = staged_archive
        if (
            item.get("platform") != "linux/arm64"
            or not isinstance(item.get("imageDigest"), str)
            or not SHA.fullmatch(item["imageDigest"])
        ):
            _fail("registry-image-identity-invalid", "candidate", item.get("role"))
    tag_states: dict[str, int] = {}
    for item in images:
        role = item["role"]
        registry_repo = f"{owner.lower()}/{repo.lower()}"
        revalidate()
        state, _, _ = _manifest(
            registry_repo,
            identity["softwareVersion"],
            owner=owner.lower(),
            token=token,
            anonymous=False,
        )
        if state not in (200, 404):
            _fail("registry-tag-state-unknown", "preflight", role)
        tag_states[role] = state
    tag_digests: dict[str, str] = {}
    # Verify every occupied tag before the first image can be pushed.
    for item in images:
        role = item["role"]
        if existing_only and tag_states[role] == 404:
            _fail("registry-recovery-target-not-confirmed", "preflight", role)
        if tag_states[role] == 200:
            registry_repo = f"{owner.lower()}/{repo.lower()}"
            digest, config_id = _image_manifest(
                registry_repo,
                identity["softwareVersion"],
                owner=owner.lower(),
                token=token,
                anonymous=False,
            )
            if config_id != item["imageDigest"]:
                _fail("registry-tag-mismatch", "preflight", role)
            tag_digests[role] = digest
    # Load and inspect every missing candidate image before the first remote push.
    for item in sorted(images, key=lambda x: ROLES.index(x["role"])):
        role = item["role"]
        if tag_states[role] == 404:
            workspace_state.role = role
            revalidate()
            _run(
                ["podman", "--url", endpoint, "load", "-i", str(staged_archives[role])],
                endpoint=endpoint,
            )
            _inspect_image(item["imageDigest"], role, identity, endpoint)
    results = []
    for item in sorted(images, key=lambda x: ROLES.index(x["role"])):
        role = item["role"]
        image_id = item.get("imageDigest")
        if (
            not isinstance(image_id, str)
            or not SHA.fullmatch(image_id)
            or item.get("platform") != "linux/arm64"
        ):
            _fail("registry-image-identity-invalid", "candidate", role)
        target_repo = f"{owner.lower()}/{repo.lower()}"
        tag = identity["softwareVersion"]
        reference = f"ghcr.io/{target_repo}:{tag}"
        # The read-only preflight completed for both image tags before any writes.
        status = tag_states[role]
        digest = None
        if status == 200:
            digest = tag_digests[role]
        elif status == 404:
            try:
                workspace_state.role = role
                authfile = workspace / f"{role}-auth.json"
                auth_bytes = json.dumps(
                    {
                        "auths": {
                            "ghcr.io": {
                                "auth": base64.b64encode(
                                    (owner.lower() + ":" + token).encode()
                                ).decode()
                            }
                        }
                    }
                ).encode("utf-8")
                fd = os.open(authfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as auth_stream:
                    auth_stream.write(auth_bytes)
                del auth_bytes
                digestfile = workspace / f"{role}-digest"
                revalidate()
                fresh_status, _, _ = _manifest(
                    target_repo,
                    tag,
                    owner=owner.lower(),
                    token=token,
                    anonymous=False,
                )
                if fresh_status == 200:
                    fresh_digest, fresh_config = _image_manifest(
                        target_repo,
                        tag,
                        owner=owner.lower(),
                        token=token,
                        anonymous=False,
                    )
                    if fresh_config != image_id:
                        _fail("registry-tag-mismatch", "preflight", role)
                    anon_digest, anon_config = _image_manifest(
                        target_repo, fresh_digest, anonymous=True
                    )
                    if anon_digest != fresh_digest or anon_config != image_id:
                        _fail("registry-not-public", "anonymous-read", role)
                    tag_states[role] = 200
                    tag_digests[role] = fresh_digest
                    digest = fresh_digest
                    # A concurrent publisher won the race with identical bytes.
                    # Do not overwrite the tag.
                elif fresh_status != 404:
                    _fail("registry-tag-state-unknown", "preflight", role)
                else:
                    workspace_state.write_started = True
                    try:
                        _run(
                            [
                                "podman",
                                "--url",
                                endpoint,
                                "push",
                                "--retry=0",
                                "--tls-verify=true",
                                f"--authfile={authfile}",
                                f"--digestfile={digestfile}",
                                image_id,
                                f"docker://{reference}",
                            ],
                            endpoint=endpoint,
                        )
                    except RegistryError:
                        _fail("registry-push-unknown", "push", role, True)
                    try:
                        digest, config_id = _image_manifest(
                            target_repo,
                            tag,
                            owner=owner.lower(),
                            token=token,
                            anonymous=False,
                        )
                    except RegistryError:
                        _fail("registry-push-verify-unknown", "verify", role, True)
                    if config_id != image_id:
                        _fail("registry-manifest-config-mismatch", "verify", role, True)
                    try:
                        digest_file_value = (
                            digestfile.read_text(encoding="ascii").strip()
                            if digestfile.is_file() and not digestfile.is_symlink()
                            else ""
                        )
                    except (OSError, UnicodeError):
                        digest_file_value = ""
                    if not digest_file_value or digest_file_value != digest:
                        _fail("registry-push-digest-mismatch", "verify", role, True)
                    try:
                        anon_digest, anon_config = _image_manifest(
                            target_repo, digest, anonymous=True
                        )
                    except RegistryError:
                        _fail(
                            "registry-postwrite-verify-unknown",
                            "anonymous-read",
                            role,
                            True,
                        )
                    if anon_digest != digest or anon_config != image_id:
                        _fail("registry-not-public", "anonymous-read", role, True)
            except RegistryError:  # noqa: TRY203 -- preserve sanitized structured failure.
                raise
        else:
            _fail("registry-tag-state-unknown", "preflight", role)
        # Existing matching tags are read-only; anonymous check still mandatory.
        if status == 200:
            anon_digest, anon_config = _image_manifest(
                target_repo, digest, anonymous=True
            )
            if anon_digest != digest or anon_config != image_id:
                _fail("registry-not-public", "anonymous-read", role)
        results.append(
            {
                "role": role,
                "platform": "linux/arm64",
                "reference": reference,
                "manifestDigest": digest,
                "imageId": image_id,
                "archiveSha256": item["sha256"],
            }
        )
    return {
        "schemaVersion": "lexiflow.registry-images.v1",
        "softwareVersion": identity["softwareVersion"],
        "sourceCommit": identity["sourceCommit"],
        "candidateSha256": candidate_sha256,
        "manifestSha256": manifest_sha256,
        "images": results,
    }


def verify_images(mapping: dict, *, owner: str, repo: str, expected: dict) -> dict:
    """只读匿名复核发布映射中的 digest/config 身份，供 draft 发布前使用。"""
    required = {
        "schemaVersion",
        "softwareVersion",
        "sourceCommit",
        "candidateSha256",
        "manifestSha256",
        "images",
    }
    if (
        not isinstance(mapping, dict)
        or set(mapping) != required
        or mapping.get("schemaVersion") != "lexiflow.registry-images.v1"
    ):
        _fail("registry-mapping-invalid", "mapping")
    if not isinstance(expected, dict) or any(
        mapping.get(key) != expected.get(key)
        for key in (
            "softwareVersion",
            "sourceCommit",
            "candidateSha256",
            "manifestSha256",
        )
    ):
        _fail("registry-mapping-identity-mismatch", "mapping")
    if not re.fullmatch(r"[A-Za-z0-9-]+", owner) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+", repo
    ):
        _fail("registry-origin-invalid", "mapping")
    images = mapping["images"]
    if (
        not isinstance(images, list)
        or len(images) != 1
        or {item.get("role") for item in images if isinstance(item, dict)} != set(ROLES)
    ):
        _fail("registry-mapping-invalid", "mapping")
    for item in images:
        role = item.get("role")
        target_repo = f"{owner.lower()}/{repo.lower()}"
        if (
            item.get("platform") != "linux/arm64"
            or item.get("reference")
            != f"ghcr.io/{target_repo}:{mapping['softwareVersion']}"
            or not SHA.fullmatch(str(item.get("manifestDigest", "")))
            or not SHA.fullmatch(str(item.get("imageId", "")))
            or not HEX.fullmatch(str(item.get("archiveSha256", "")))
        ):
            _fail("registry-mapping-invalid", "mapping", role)
        digest, image_id = _image_manifest(
            target_repo, item["manifestDigest"], anonymous=True
        )
        if digest != item["manifestDigest"] or image_id != item["imageId"]:
            _fail("registry-manifest-config-mismatch", "verify", role)
    return mapping
