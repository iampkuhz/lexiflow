"""将 Formal 联合证明绑定的候选准备或晋升为不可替换的 GitHub Release。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import tarfile
import tempfile
import time
import threading
import urllib.parse
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from scripts.delivery_gate.candidate import _json, _read_bounded, consume_candidate_pass
from scripts.environment.release_runtime_check import _run_bounded

ROOT = Path(__file__).resolve().parents[2]
LICENSE_TABLE = ROOT / "ops/release/distribution-licenses.json"
MAX_ASSET = 2 * 1024**3
MAX_EXTENSION = 256 * 1024**2
MAX_UNCOMPRESSED = 8 * 1024**3
MAX_JSON = 4 * 1024**2
MAX_LICENSE_TABLE = 256 * 1024
MAX_LICENSES = 64
API_VERSION = "2026-03-10"
SHA = re.compile(r"[0-9a-f]{64}\Z")
ASSET_NAMES = (
    "archive",
    "archive_checksum",
    "candidate",
    "manifest",
    "manifest_checksum",
)


class PromotionError(ValueError):
    """不携带路径、响应体或凭据的受控失败。"""

    def __init__(
        self, reason: str, *, stage: str | None = None, release_id: int | None = None
    ):
        super().__init__(reason)
        self.stage = stage
        self.release_id = release_id


def _fail(reason: str) -> None:
    raise PromotionError(reason)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read(root: Path, relative: str, limit: int) -> bytes:
    try:
        return _read_bounded(root, relative, limit)
    except Exception:
        _fail("candidate-evidence-invalid")


def _json_strict(data: bytes) -> Any:
    try:
        return _json(data)
    except Exception:
        _fail("candidate-metadata-invalid")


def _license_check(manifest: dict[str, Any]) -> None:
    try:
        relative = LICENSE_TABLE.relative_to(ROOT).as_posix()
        table = _json(_read_bounded(ROOT, relative, MAX_LICENSE_TABLE))
    except Exception:
        _fail("distribution-license-table-invalid")
    if (
        not isinstance(table, dict)
        or set(table) != {"schema_version", "licenses"}
        or table["schema_version"] != "lexiflow.distribution-licenses.v1"
        or not isinstance(table["licenses"], list)
        or len(table["licenses"]) > MAX_LICENSES
    ):
        _fail("distribution-license-table-invalid")
    required = manifest.get("licenses")
    if not isinstance(required, list) or not required or len(required) > MAX_LICENSES:
        _fail("distribution-licenses-unapproved")
    fields = {
        "id",
        "component",
        "licenseId",
        "licenseName",
        "sourceUrl",
        "noticePath",
        "noticeBytes",
        "noticeSha256",
    }
    ids = set()
    for item in table["licenses"]:
        if (
            not isinstance(item, dict)
            or set(item) != fields
            or not isinstance(item["id"], str)
            or item["id"] in ids
            or not isinstance(item["licenseId"], str)
            or not isinstance(item["licenseName"], str)
            or "UNVERIFIED" in item["licenseId"].upper()
            or "UNVERIFIED" in item["licenseName"].upper()
        ):
            _fail("distribution-license-table-invalid")
        ids.add(item["id"])
    if (
        len(required) != len(table["licenses"])
        or any(not isinstance(item, dict) or set(item) != fields for item in required)
        or required != table["licenses"]
    ):
        _fail("distribution-licenses-unapproved")


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _stable_fd(root: Path, relative: str) -> tuple[int, os.stat_result]:
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or any(ord(c) < 32 for c in relative)
    ):
        _fail("candidate-path-unsafe")
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.as_posix() != relative
        or any(x in ("", ".", "..") for x in pure.parts)
    ):
        _fail("candidate-path-unsafe")
    dflags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(root, dflags)
        for part in pure.parts[:-1]:
            child = os.open(part, dflags, dir_fd=fd)
            os.close(fd)
            fd = child
        out = os.open(
            pure.parts[-1],
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=fd,
        )
        info = os.fstat(out)
        if not stat.S_ISREG(info.st_mode):
            os.close(out)
            _fail("candidate-path-unsafe")
        return out, info
    except PromotionError:
        raise
    except OSError:
        _fail("candidate-path-unsafe")
    finally:
        if fd is not None:
            os.close(fd)


def _bounded_item(item: Any) -> None:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or type(item.get("bytes")) is not int
        or not 0 < item["bytes"] <= MAX_ASSET
        or not isinstance(item.get("sha256"), str)
        or not SHA.fullmatch(item["sha256"])
        or not isinstance(item.get("role"), str)
    ):
        _fail("candidate-manifest-invalid")
    path = item["path"]
    if path in ("manifest.json", "manifest.json.sha256") or len(path) > 4096:
        _fail("candidate-manifest-invalid")
    # Validate path before tarfile interprets it.
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or pure.as_posix() != path
        or "\\" in path
        or any(x in ("", ".", "..") for x in pure.parts)
    ):
        _fail("candidate-manifest-invalid")


def _read_exact(
    fd: int,
    size: int,
    digest: hashlib._Hash,
    sink: Callable[[bytes], None] | None = None,
) -> None:
    left = size
    deadline = time.monotonic() + 900
    while left:
        if time.monotonic() > deadline:
            _fail("asset-read-deadline")
        chunk = os.read(fd, min(left, 1024 * 1024))
        if not chunk:
            _fail("candidate-artifact-hash-mismatch")
        digest.update(chunk)
        if sink:
            sink(chunk)
        left -= len(chunk)
    if os.read(fd, 1):
        _fail("candidate-artifact-hash-mismatch")


class _VerifiedReader:
    """tarfile 逐块读取同一已打开 FD，并核对实际被归档的字节。"""

    def __init__(self, fd: int, item: dict[str, Any], deadline: float):
        self.fd, self.item = fd, item
        self.deadline = deadline
        self.before = os.fstat(fd)
        if self.before.st_size != item["bytes"]:
            _fail("candidate-artifact-hash-mismatch")
        self.remaining = item["bytes"]
        self.hash = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        """返回下一段已计入摘要的归档字节。"""
        if time.monotonic() > self.deadline:
            _fail("archive-deadline")
        if self.remaining == 0 or size == 0:
            return b""
        block = os.read(
            self.fd, min(self.remaining, size if size >= 0 else 1024 * 1024)
        )
        if not block:
            _fail("candidate-artifact-hash-mismatch")
        self.hash.update(block)
        self.remaining -= len(block)
        return block

    def finish(self) -> None:
        """确认归档已完整消费同一稳定文件。"""
        if (
            self.remaining
            or os.read(self.fd, 1)
            or self.hash.hexdigest() != self.item["sha256"]
            or _stamp(self.before) != _stamp(os.fstat(self.fd))
        ):
            _fail("candidate-artifact-hash-mismatch")


def _archive(
    candidate: Path,
    manifest: dict[str, Any],
    manifest_bytes: bytes,
    sidecar_bytes: bytes,
    out: Path,
) -> tuple[str, int]:
    total = 0
    digest = hashlib.sha256()
    deadline = time.monotonic() + 900
    total_uncompressed = (
        sum(item["bytes"] for item in manifest["artifacts"])
        + len(manifest_bytes)
        + len(sidecar_bytes)
    )
    if total_uncompressed > MAX_UNCOMPRESSED:
        _fail("asset-size-limit")
    with out.open("xb") as raw:

        class Bounded:
            def write(self, data: bytes) -> int:
                """限制并记录实际压缩输出。"""
                nonlocal total
                if time.monotonic() > deadline:
                    _fail("archive-deadline")
                total += len(data)
                if total > MAX_ASSET:
                    _fail("asset-size-limit")
                written = raw.write(data)
                if written != len(data):
                    _fail("archive-write-failed")
                digest.update(data)
                return written

            def flush(self):
                """将压缩输出刷入私有文件。"""
                return raw.flush()

        with gzip.GzipFile(fileobj=Bounded(), mode="wb", filename="", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                entries = [(x["path"], x) for x in manifest["artifacts"]]
                entries += [
                    ("manifest.json", manifest_bytes),
                    ("manifest.json.sha256", sidecar_bytes),
                ]
                for name, value in sorted(entries, key=lambda entry: entry[0]):
                    info = tarfile.TarInfo(name)
                    info.size = (
                        value["bytes"] if isinstance(value, dict) else len(value)
                    )
                    info.mode = (
                        0o755
                        if isinstance(value, dict) and value["role"] == "runtime-entry"
                        else 0o644
                    )
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    if isinstance(value, dict):
                        fd, _ = _stable_fd(candidate, "payload/" + name)
                        try:
                            reader = _VerifiedReader(fd, value, deadline)
                            tar.addfile(info, reader)
                            reader.finish()
                        finally:
                            os.close(fd)
                    else:
                        import io

                        tar.addfile(info, io.BytesIO(value))
        raw.flush()
        os.fsync(raw.fileno())
    return digest.hexdigest(), total


def _repository(root: Path) -> tuple[str, str]:
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if key in os.environ
    }
    try:
        raw = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        ).stdout.strip()
    except Exception:
        _fail("repository-origin-invalid")
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?",
        raw,
    )
    if not match or match.group(1) in (".", "..") or match.group(2) in (".", ".."):
        _fail("repository-origin-invalid")
    return match.group(1), match.group(2)


def _verify_local_tag(tag: str, identity: dict[str, Any]) -> None:
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
        if key in os.environ
    }
    try:
        stdout, _ = _run_bounded(
            ["node", str(ROOT / "ops/release/version.mjs"), "--tag", tag],
            ROOT,
            env,
            30,
            limit=65536,
        )
        parsed = _json(stdout)
    except Exception:
        _fail("formal-release-tag-check-failed")
    if parsed != identity:
        _fail("formal-release-tag-identity-mismatch")


class GitHub:
    """固定目的地与凭据用途的无重定向 HTTP transport。"""

    def __init__(
        self,
        token: str,
        owner: str,
        repo: str,
        host: str = "api.github.com",
        *,
        config: bool = False,
    ):
        if host not in ("api.github.com", "uploads.github.com"):
            _fail("github-host-rejected")
        self.host, self.token, self.owner, self.repo, self.config = (
            host,
            token,
            owner,
            repo,
            config,
        )

    def _allowed(
        self, method: str, path: str, stream: tuple[int, int, str] | None
    ) -> None:
        prefix = f"/repos/{self.owner}/{self.repo}"
        if self.config:
            if (
                self.host != "api.github.com"
                or method != "GET"
                or path != prefix + "/immutable-releases"
            ):
                _fail("github-route-rejected")
        elif self.host == "uploads.github.com":
            if (
                method != "POST"
                or stream is None
                or not re.fullmatch(
                    re.escape(prefix)
                    + r"/releases/[1-9][0-9]*/assets\?name=[A-Za-z0-9._-]+",
                    path,
                )
            ):
                _fail("github-route-rejected")
        else:
            exact = (method, path) in {
                ("GET", prefix + "/releases/latest"),
                ("POST", prefix + "/releases"),
            }
            patterns = (
                method == "GET"
                and re.fullmatch(
                    re.escape(prefix) + r"/git/ref/tags/v[0-9]+\.[0-9]+\.[0-9]+", path
                ),
                method == "GET"
                and re.fullmatch(
                    re.escape(prefix) + r"/git/tags/[0-9a-f]{40,64}", path
                ),
                method == "GET"
                and re.fullmatch(
                    re.escape(prefix)
                    + r"/releases(?:/[1-9][0-9]*/assets)?\?per_page=100&page=(?:[1-9]|10)",
                    path,
                ),
                method == "GET"
                and re.fullmatch(re.escape(prefix) + r"/releases/[1-9][0-9]*", path),
                method == "PATCH"
                and re.fullmatch(re.escape(prefix) + r"/releases/[1-9][0-9]*", path),
            )
            if not exact and not any(patterns):
                _fail("github-route-rejected")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str = "application/json",
        stream: tuple[int, int, str] | None = None,
    ) -> tuple[int, Any]:
        """执行固定路由的有界请求，不重定向或重试。"""
        self._allowed(method, path, stream)
        deadline = time.monotonic() + 60
        conn = http.client.HTTPSConnection(
            self.host, timeout=20, context=ssl.create_default_context()
        )
        active_socket = None

        def expire():
            if active_socket is not None:
                try:
                    active_socket.shutdown(2)
                except OSError:
                    pass
            conn.close()

        timer = threading.Timer(60, expire)
        timer.daemon = True
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "Authorization": "Bearer " + self.token,
            "User-Agent": "LexiFlow-release-promotion",
        }
        if content_type:
            headers["Content-Type"] = content_type
        timer.start()
        try:
            conn.connect()
            active_socket = conn.sock
            if time.monotonic() > deadline:
                _fail("github-request-deadline")
            conn.putrequest(method, path, skip_accept_encoding=True)
            for key, value in headers.items():
                conn.putheader(key, value)
            if stream:
                fd, size, expected_sha = stream
                before = os.fstat(fd)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_size != size
                    or not 0 <= size <= MAX_ASSET
                ):
                    _fail("asset-changed-before-upload")
                conn.putheader("Content-Length", str(size))
                conn.endheaders()
                os.lseek(fd, 0, os.SEEK_SET)
                digest = hashlib.sha256()
                left = size
                while left:
                    if time.monotonic() > deadline:
                        _fail("github-request-deadline")
                    block = os.read(fd, min(left, 1024 * 1024))
                    if not block:
                        _fail("asset-changed-during-upload")
                    digest.update(block)
                    conn.send(block)
                    left -= len(block)
                if (
                    os.read(fd, 1)
                    or digest.hexdigest() != expected_sha
                    or _stamp(before) != _stamp(os.fstat(fd))
                ):
                    _fail("asset-changed-during-upload")
            else:
                body = body or b""
                if len(body) > MAX_JSON:
                    _fail("github-request-too-large")
                conn.putheader("Content-Length", str(len(body)))
                conn.endheaders(body)
            if time.monotonic() > deadline:
                _fail("github-request-deadline")
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                _fail("github-redirect-rejected")
            raw = bytearray()
            while True:
                if time.monotonic() > deadline:
                    _fail("github-request-deadline")
                block = response.read1(min(65536, MAX_JSON + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > MAX_JSON:
                    _fail("github-response-too-large")
            try:
                value = _json(bytes(raw)) if raw else None
            except Exception:
                value = None
            return response.status, value
        except PromotionError:
            raise
        except Exception:
            _fail("github-request-failed")
        finally:
            timer.cancel()
            timer.join()
            conn.close()

    def api(self, method: str, path: str, body: dict[str, Any] | None = None):
        """编码固定 REST 请求的 JSON body。"""
        payload = (
            json.dumps(body, separators=(",", ":")).encode()
            if body is not None
            else None
        )
        return self.request(method, path, body=payload)


def _pages(api: GitHub, path: str) -> list[dict[str, Any]]:
    values = []
    for page in range(1, 11):
        status, part = api.api("GET", f"{path}?per_page=100&page={page}")
        if (
            status != 200
            or not isinstance(part, list)
            or len(part) > 100
            or any(not isinstance(x, dict) for x in part)
        ):
            _fail("github-list-inconclusive")
        values.extend(part)
        if len(part) < 100:
            return values
    _fail("github-list-limit")


def _immutable(config: GitHub, owner: str, repo: str) -> bool:
    status, data = config.api("GET", f"/repos/{owner}/{repo}/immutable-releases")
    return status == 200 and isinstance(data, dict) and data.get("enabled") is True


def _remote_tag(api: GitHub, owner: str, repo: str, tag: str, commit: str) -> bool:
    status, ref = api.api("GET", f"/repos/{owner}/{repo}/git/ref/tags/{tag}")
    if status != 200 or not isinstance(ref, dict):
        return False
    obj = ref.get("object")
    for depth in range(6):
        if not isinstance(obj, dict) or not re.fullmatch(
            r"[0-9a-f]{40,64}", str(obj.get("sha", ""))
        ):
            return False
        if obj.get("type") == "commit":
            return obj["sha"] == commit
        if obj.get("type") != "tag" or depth == 5:
            return False
        status, value = api.api("GET", f"/repos/{owner}/{repo}/git/tags/{obj['sha']}")
        if status != 200 or not isinstance(value, dict):
            return False
        obj = value.get("object")
    return False


def _release_assets(
    api: GitHub, owner: str, repo: str, release_id: int
) -> list[dict[str, Any]]:
    return _pages(api, f"/repos/{owner}/{repo}/releases/{release_id}/assets")


def _asset_digest_matches(
    assets: list[dict[str, Any]], expected: dict[str, tuple[int, str]]
) -> bool:
    names = [x.get("name") for x in assets]
    if any(not isinstance(name, str) for name in names):
        return False
    ids = [x.get("id") for x in assets]
    if (
        len(names) != len(set(names))
        or set(names) != set(expected)
        or any(type(asset_id) is not int or asset_id <= 0 for asset_id in ids)
        or len(ids) != len(set(ids))
    ):
        return False
    return all(
        x.get("state") == "uploaded"
        and type(x.get("size")) is int
        and x["size"] == expected[x["name"]][0]
        and x.get("digest") == "sha256:" + expected[x["name"]][1]
        for x in assets
    )


def _unknown(stage: str, release_id: int | None = None) -> None:
    raise PromotionError(
        "release-write-state-unknown", stage=stage, release_id=release_id
    )


def _publish(
    owner: str,
    repo: str,
    tag: str,
    identity: dict[str, Any],
    assets: dict[str, Path],
    meta: dict[str, tuple[int, str]],
    revalidate: Callable[[], None],
) -> dict[str, Any]:
    token = os.environ.get("GITHUB_TOKEN", "")
    config_token = os.environ.get("LEXIFLOW_RELEASE_CONFIG_TOKEN", "")
    if not token or not config_token:
        _fail("github-token-missing")
    api = GitHub(token, owner, repo)
    config = GitHub(config_token, owner, repo, config=True)
    uploads = GitHub(token, owner, repo, "uploads.github.com")
    prefix = f"/repos/{owner}/{repo}"

    def gate() -> None:
        revalidate()
        if not _immutable(config, owner, repo):
            _fail("immutable-release-required")
        if not _remote_tag(api, owner, repo, tag, identity["sourceCommit"]):
            _fail("remote-tag-mismatch")

    gate()
    releases = _pages(api, prefix + "/releases")
    if any(x.get("tag_name") == tag for x in releases):
        _fail("release-tag-already-exists")
    gate()
    try:
        status, created = api.api(
            "POST",
            prefix + "/releases",
            {
                "tag_name": tag,
                "draft": True,
                "prerelease": False,
                "target_commitish": identity["sourceCommit"],
                "name": tag,
            },
        )
    except Exception:
        _unknown("create")
    release_id = created.get("id") if isinstance(created, dict) else None
    if type(release_id) is not int or release_id <= 0:
        release_id = None
    if (
        status not in (200, 201)
        or release_id is None
        or created.get("tag_name") != tag
        or created.get("draft") is not True
        or created.get("prerelease") is not False
    ):
        _unknown("create", release_id)

    def draft_gate() -> None:
        try:
            gate()
        except PromotionError as exc:
            raise PromotionError(
                str(exc), stage="draft", release_id=release_id
            ) from None

    for name, path in assets.items():
        draft_gate()
        try:
            fd, before = _stable_fd(path.parent, path.name)
        except PromotionError as exc:
            raise PromotionError(
                str(exc), stage="draft", release_id=release_id
            ) from None
        try:
            if before.st_size != meta[name][0]:
                _unknown("upload", release_id)
            try:
                status, item = uploads.request(
                    "POST",
                    f"{prefix}/releases/{release_id}/assets?name={urllib.parse.quote(name)}",
                    content_type="application/octet-stream",
                    stream=(fd, meta[name][0], meta[name][1]),
                )
            except Exception:
                _unknown("upload", release_id)
        finally:
            os.close(fd)
        if (
            status not in (200, 201)
            or not isinstance(item, dict)
            or type(item.get("id")) is not int
            or item["id"] <= 0
            or item.get("state") != "uploaded"
            or item.get("name") != name
            or type(item.get("size")) is not int
            or item["size"] != meta[name][0]
            or item.get("digest") != "sha256:" + meta[name][1]
        ):
            _unknown("upload", release_id)
    draft_gate()
    try:
        releases = _pages(api, prefix + "/releases")
    except PromotionError as exc:
        raise PromotionError(str(exc), stage="draft", release_id=release_id) from None
    if any(x.get("tag_name") == tag and x.get("id") != release_id for x in releases):
        raise PromotionError(
            "release-tag-conflict", stage="draft", release_id=release_id
        )
    draft_gate()
    try:
        status, draft = api.api("GET", f"{prefix}/releases/{release_id}")
        listed = _release_assets(api, owner, repo, release_id)
    except PromotionError as exc:
        raise PromotionError(str(exc), stage="draft", release_id=release_id) from None
    if (
        status != 200
        or not isinstance(draft, dict)
        or draft.get("id") != release_id
        or draft.get("tag_name") != tag
        or draft.get("draft") is not True
        or draft.get("prerelease") is not False
        or not _asset_digest_matches(listed, meta)
    ):
        raise PromotionError(
            "release-draft-incomplete", stage="draft", release_id=release_id
        )
    try:
        status, published = api.api(
            "PATCH",
            f"{prefix}/releases/{release_id}",
            {"draft": False, "make_latest": "true"},
        )
    except Exception:
        _unknown("publish", release_id)
    if (
        status != 200
        or not isinstance(published, dict)
        or published.get("id") != release_id
        or published.get("tag_name") != tag
        or published.get("draft") is not False
    ):
        _unknown("publish", release_id)
    try:
        status, final = api.api("GET", f"{prefix}/releases/{release_id}")
        final_assets = _release_assets(api, owner, repo, release_id)
        immutable = _immutable(config, owner, repo)
    except PromotionError:
        _unknown("confirm", release_id)
    if (
        status != 200
        or not isinstance(final, dict)
        or final.get("id") != release_id
        or final.get("tag_name") != tag
        or final.get("draft") is not False
        or final.get("prerelease") is not False
        or final.get("immutable") is not True
        or not _asset_digest_matches(final_assets, meta)
        or not immutable
    ):
        _unknown("confirm", release_id)
    try:
        status, latest = api.api("GET", prefix + "/releases/latest")
    except PromotionError:
        _unknown("confirm", release_id)
    if status != 200 or not isinstance(latest, dict) or latest.get("id") != release_id:
        _unknown("confirm", release_id)
    return {"published": True, "release_id": release_id, "tag": tag}


def _bound_candidate(
    consumed: Any, candidate: Path
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes, bytes]:
    if (
        not isinstance(consumed, dict)
        or consumed.get("result") != "PASS"
        or not isinstance(consumed.get("proof"), dict)
    ):
        _fail("formal-candidate-proof-required")
    proof = consumed["proof"]
    selected = proof.get("candidate")
    if not isinstance(selected, dict) or set(selected) != {
        "candidateSha256",
        "manifestSha256",
        "buildIdentity",
    }:
        _fail("formal-candidate-proof-required")
    manifest_bytes = _read(candidate, "payload/manifest.json", MAX_JSON)
    candidate_bytes = _read(candidate, "candidate.json", MAX_JSON)
    sidecar_bytes = _read(candidate, "payload/manifest.json.sha256", 256)
    if (
        hashlib.sha256(manifest_bytes).hexdigest() != selected["manifestSha256"]
        or hashlib.sha256(candidate_bytes).hexdigest() != selected["candidateSha256"]
        or sidecar_bytes != f"{selected['manifestSha256']}  manifest.json\n".encode()
    ):
        _fail("candidate-proof-binding-invalid")
    manifest, marker = _json_strict(manifest_bytes), _json_strict(candidate_bytes)
    identity = selected["buildIdentity"]
    if (
        not isinstance(manifest, dict)
        or not isinstance(marker, dict)
        or not isinstance(identity, dict)
        or manifest.get("buildIdentity") != identity
        or marker.get("buildIdentity") != identity
        or manifest.get("softwareVersion") != identity.get("softwareVersion")
        or marker.get("softwareVersion") != identity.get("softwareVersion")
        or manifest.get("sourceCommit") != identity.get("sourceCommit")
        or marker.get("sourceCommit") != identity.get("sourceCommit")
        or identity.get("dirty") is not False
        or identity.get("channel") != "release"
        or identity.get("baseVersion") != identity.get("softwareVersion")
    ):
        _fail("formal-release-identity-required")
    version = identity["softwareVersion"]
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})", version
    ):
        _fail("formal-release-identity-required")
    image_candidates = marker.get("imageCandidates")
    if (
        manifest.get("platforms") != ["linux/arm64"]
        or not isinstance(image_candidates, list)
        or len(image_candidates) != 1
        or not isinstance(image_candidates[0], dict)
        or image_candidates[0].get("platform") != "linux/arm64"
    ):
        _fail("formal-release-platform-required")
    _license_check(manifest)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > 128:
        _fail("candidate-manifest-invalid")
    paths = set()
    extensions = []
    total = len(manifest_bytes) + len(sidecar_bytes)
    for item in artifacts:
        _bounded_item(item)
        if item["path"] in paths:
            _fail("candidate-manifest-invalid")
        paths.add(item["path"])
        if item["role"] == "extension":
            extensions.append(item)
        total += item["bytes"]
        if total > MAX_UNCOMPRESSED:
            _fail("asset-size-limit")
    if (
        len(extensions) != 1
        or extensions[0]["path"] != "extension/package.zip"
        or extensions[0]["bytes"] > MAX_EXTENSION
    ):
        _fail("candidate-extension-invalid")
    return proof, identity, manifest_bytes, candidate_bytes, sidecar_bytes


def promote(
    *, submission_id: str, candidate_directory: str | Path, publish: bool = False
) -> dict[str, Any]:
    """核验既有候选并按显式选择执行不可替换发布。"""
    try:
        submission_id = str(uuid.UUID(submission_id))
    except Exception:
        _fail("submission-id-invalid")
    candidate = Path(candidate_directory)
    if not candidate.is_absolute():
        _fail("candidate-directory-must-be-absolute")
    first = consume_candidate_pass(
        ROOT, submission_id=submission_id, candidate_directory=candidate
    )
    proof, identity, manifest_bytes, candidate_bytes, sidecar_bytes = _bound_candidate(
        first, candidate
    )
    tag = "v" + identity["softwareVersion"]
    _verify_local_tag(tag, identity)
    manifest = _json_strict(manifest_bytes)

    def revalidate() -> None:
        latest = consume_candidate_pass(
            ROOT, submission_id=submission_id, candidate_directory=candidate
        )
        if (
            not isinstance(latest, dict)
            or latest.get("result") != "PASS"
            or latest.get("proof") != proof
        ):
            _fail("formal-candidate-proof-required")
        _bound_candidate(latest, candidate)
        _verify_local_tag(tag, identity)
        _license_check(manifest)

    workspace = Path(tempfile.mkdtemp(prefix="lexiflow-release-promotion-"))
    try:
        version = identity["softwareVersion"]
        archive = workspace / f"lexiflow-{version}-macos-arm64.tar.gz"
        sha, size = _archive(
            candidate, manifest, manifest_bytes, sidecar_bytes, archive
        )

        def write(name: str, data: bytes) -> Path:
            path = workspace / name
            with path.open("xb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            return path

        archive_sidecar = write(
            archive.name + ".sha256", f"{sha}  {archive.name}\n".encode()
        )
        extension_item = next(
            item for item in manifest["artifacts"] if item["role"] == "extension"
        )
        extension_name = f"lexiflow-extension-{version}.zip"
        extension_path = workspace / extension_name
        extension_fd, extension_info = _stable_fd(
            candidate, "payload/" + extension_item["path"]
        )
        try:
            if extension_info.st_size != extension_item["bytes"]:
                _fail("candidate-artifact-hash-mismatch")
            with extension_path.open("xb") as output:
                extension_digest = hashlib.sha256()
                _read_exact(
                    extension_fd,
                    extension_item["bytes"],
                    extension_digest,
                    output.write,
                )
                output.flush()
                os.fsync(output.fileno())
            if extension_digest.hexdigest() != extension_item["sha256"] or _stamp(
                extension_info
            ) != _stamp(os.fstat(extension_fd)):
                _fail("candidate-artifact-hash-mismatch")
        finally:
            os.close(extension_fd)
        extension_digest_hex = extension_item["sha256"]
        extension_sidecar = write(
            extension_name + ".sha256",
            f"{extension_digest_hex}  {extension_name}\n".encode(),
        )
        manifest_path = write("manifest.json", manifest_bytes)
        manifest_sidecar = write("manifest.json.sha256", sidecar_bytes)
        candidate_path = write("candidate.json", candidate_bytes)
        assets = {
            archive.name: archive,
            archive_sidecar.name: archive_sidecar,
            extension_name: extension_path,
            extension_sidecar.name: extension_sidecar,
            candidate_path.name: candidate_path,
            manifest_path.name: manifest_path,
            manifest_sidecar.name: manifest_sidecar,
        }
        expected = {}
        for name, path in assets.items():
            fd, before = _stable_fd(workspace, name)
            try:
                if before.st_size > MAX_ASSET:
                    _fail("asset-size-limit")
                digest = hashlib.sha256()
                _read_exact(fd, before.st_size, digest)
                if _stamp(before) != _stamp(os.fstat(fd)):
                    _fail("asset-changed-during-prepare")
                expected[name] = (before.st_size, digest.hexdigest())
            finally:
                os.close(fd)
        if expected[archive.name] != (size, sha):
            _fail("archive-changed-during-prepare")
        if expected[extension_name] != (
            extension_item["bytes"],
            extension_item["sha256"],
        ):
            _fail("extension-changed-during-prepare")
        revalidate()
        if publish:
            owner, repo = _repository(ROOT)
            declared = os.environ.get("GITHUB_REPOSITORY")
            if declared and declared != f"{owner}/{repo}":
                _fail("repository-origin-mismatch")
            result = _publish(owner, repo, tag, identity, assets, expected, revalidate)
        else:
            result = {"published": False, "release_id": None, "tag": tag}
        result["assets"] = [
            {"name": name, "bytes": expected[name][0], "sha256": expected[name][1]}
            for name in sorted(expected)
        ]
        result["result"] = "PASS"
        return result
    finally:
        try:
            shutil.rmtree(workspace)
        except OSError:
            # 清理失败不抹去已确认发布或原本的未知写入状态。
            if "result" in locals():
                result["cleanup"] = "BLOCKED"
                result["result"] = "BLOCKED"


def main(argv: list[str] | None = None) -> int:
    """解析固定晋升入口并仅输出不含凭据的结果。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--candidate-directory", required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = promote(
            submission_id=args.submission_id,
            candidate_directory=args.candidate_directory,
            publish=args.publish,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["result"] == "PASS" else 2
    except PromotionError as exc:
        result = {"result": "BLOCKED", "reason": str(exc)}
        if exc.stage:
            result.update(
                published="unknown", write_stage=exc.stage, release_id=exc.release_id
            )
        print(json.dumps(result, sort_keys=True))
        return 2
    except Exception:
        print(
            json.dumps(
                {"result": "BLOCKED", "reason": "promotion-failed"}, sort_keys=True
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
