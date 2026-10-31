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
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from ops.release.host_resources.release_process_control import (
    run_bounded as _run_bounded,
)
from ops.release.workflow import release_registry
from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import (
    _json,
    _read_bounded,
    consume_candidate_pass,
)

ROOT = Path(__file__).resolve().parents[3]
LICENSE_TABLE = ROOT / "ops/release/distribution-licenses.json"
MAX_ASSET = 2 * 1024**3
MAX_EXTENSION = 256 * 1024**2
MAX_UNCOMPRESSED = 8 * 1024**3
MAX_JSON = 4 * 1024**2
MAX_LICENSE_TABLE = 256 * 1024
MAX_LICENSES = 64
NOTES_ROOT = ROOT / "ops/release/notes"
API_VERSION = "2026-03-10"
SHA = re.compile(r"[0-9a-f]{64}\Z")


class PromotionError(ValueError):
    """不携带路径、响应体或凭据的受控失败。"""

    def __init__(
        self,
        reason: str,
        *,
        stage: str | None = None,
        release_id: int | None = None,
        registry_role: str | None = None,
    ):
        super().__init__(reason)
        self.stage = stage
        self.release_id = release_id
        self.registry_role = registry_role


def _fail(reason: str) -> None:
    raise PromotionError(reason)


def _release_notes(version: str, source_commit: str) -> str:
    """读取精确版本正文；校验必须早于所有远端写入。"""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        _fail("release-notes-version-invalid")
    try:
        note_bytes = (NOTES_ROOT / f"{version}.md").read_bytes()
        text = note_bytes.decode("utf-8")
    except (OSError, UnicodeError):
        _fail("release-notes-missing")
    if (ROOT / ".git").exists():
        try:
            checked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(ROOT),
                    "show",
                    f"{source_commit}:ops/release/notes/{version}.md",
                ],
                check=True,
                capture_output=True,
                timeout=5,
                env={
                    key: os.environ[key]
                    for key in ("PATH", "HOME", "LANG", "LC_ALL", "TZ")
                    if key in os.environ
                },
            ).stdout
        except Exception:
            _fail("release-notes-commit-unavailable")
        if checked != note_bytes:
            _fail("release-notes-not-in-release-commit")
    lines = text.splitlines()
    if not text.strip() or not lines or lines[0].strip() != f"# 1. {version}":
        _fail("release-notes-version-mismatch")
    if not all(term in text.lower() for term in ("前置", "两步", "限制")):
        _fail("release-notes-incomplete")
    return text.rstrip()


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
    project_licenses = [
        item
        for item in table["licenses"]
        if item["component"] == "LexiFlow" and item["noticePath"] == "LICENSE"
    ]
    if len(project_licenses) != 1 or project_licenses[0]["licenseId"] != "MIT":
        _fail("project-license-invalid")
    try:
        own_license = _read_bounded(ROOT, "LICENSE", MAX_LICENSE_TABLE)
    except Exception:
        _fail("project-license-invalid")
    project_license = project_licenses[0]
    if (
        len(own_license) != project_license["noticeBytes"]
        or hashlib.sha256(own_license).hexdigest() != project_license["noticeSha256"]
    ):
        _fail("project-license-notice-mismatch")


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
            [
                "node",
                str(ROOT / "ops/release/integrity/source-build-identity.mjs"),
                "--tag",
                tag,
            ],
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
                or path
                not in {
                    prefix + "/immutable-releases",
                    prefix + "/environments/lexiflow-release-promotion",
                    prefix
                    + "/environments/lexiflow-release-promotion/deployment-branch-policies",
                }
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
                (
                    "GET",
                    prefix
                    + f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}/approvals",
                ),
                (
                    "GET",
                    prefix + f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}",
                ),
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
        request_budget = 900 if self.host == "uploads.github.com" and stream else 60
        deadline = time.monotonic() + request_budget
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

        timer = threading.Timer(request_budget, expire)
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


def _assert_live_promotion_authorization(
    api: GitHub, config: GitHub, owner: str, repo: str, identity: dict[str, Any]
) -> None:
    """重核真实单次 dispatch、审批历史及当前环境保护；任一未知均失败关闭。"""
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    expected = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/tags/v" + identity["softwareVersion"],
        "GITHUB_SHA": identity["sourceCommit"],
        "GITHUB_REPOSITORY": f"{owner}/{repo}",
        "GITHUB_RUN_ATTEMPT": "1",
    }
    if (
        not re.fullmatch(r"[1-9][0-9]*", run_id)
        or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
        or any(os.environ.get(k) != v for k, v in expected.items())
    ):
        _fail("promotion-run-context-invalid")
    status, run = api.api("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}")
    if (
        status != 200
        or not isinstance(run, dict)
        or not isinstance(run.get("repository"), dict)
        or run.get("id") != int(run_id)
        or run.get("run_attempt") != 1
        or run.get("head_sha") != identity["sourceCommit"]
        or run.get("event") != "workflow_dispatch"
        or run.get("path") != ".github/workflows/release.yml"
        or run.get("head_branch") != "v" + identity["softwareVersion"]
        or run.get("status") != "in_progress"
        or run.get("repository", {}).get("full_name") != f"{owner}/{repo}"
    ):
        _fail("promotion-run-platform-check-failed")
    status, approvals = api.api(
        "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/approvals"
    )
    if (
        status != 200
        or not isinstance(approvals, list)
        or any(not isinstance(a, dict) for a in approvals)
    ):
        _fail("promotion-approval-history-unavailable")
    status, env = config.api(
        "GET", f"/repos/{owner}/{repo}/environments/lexiflow-release-promotion"
    )
    if (
        status != 200
        or not isinstance(env, dict)
        or not isinstance(env.get("protection_rules"), list)
        or type(env.get("id")) is not int
        or env["id"] <= 0
    ):
        _fail("promotion-environment-unavailable")
    rules = env.get("protection_rules")
    reviewers = (
        [
            r
            for r in rules
            if isinstance(r, dict) and r.get("type") == "required_reviewers"
        ]
        if isinstance(rules, list)
        else []
    )
    configured = reviewers[0].get("reviewers") if len(reviewers) == 1 else None
    if (
        any(not isinstance(rule, dict) for rule in rules)
        or not isinstance(configured, list)
        or len(configured) != 1
        or not isinstance(configured[0], dict)
    ):
        _fail("promotion-environment-reviewer-mismatch")
    reviewer = configured[0].get("reviewer")
    if (
        configured[0].get("type") != "User"
        or not isinstance(reviewer, dict)
        or reviewer.get("login") != owner
        or type(reviewer.get("id")) is not int
        or reviewer["id"] <= 0
    ):
        _fail("promotion-environment-reviewer-mismatch")
    decisions = []
    for approval in approvals:
        user = approval.get("user")
        environments = approval.get("environments")
        if (
            not isinstance(user, dict)
            or type(user.get("id")) is not int
            or user["id"] <= 0
            or not isinstance(user.get("login"), str)
            or not isinstance(environments, list)
            or approval.get("state") not in {"approved", "rejected"}
        ):
            _fail("promotion-approval-history-unavailable")
        if any(
            not isinstance(e, dict)
            or type(e.get("id")) is not int
            or e["id"] <= 0
            or not isinstance(e.get("name"), str)
            for e in environments
        ):
            _fail("promotion-approval-history-unavailable")
        associated = [
            e
            for e in environments
            if e["id"] == env["id"] or e["name"] == "lexiflow-release-promotion"
        ]
        if associated:
            if (
                len(associated) != 1
                or associated[0]["id"] != env["id"]
                or associated[0]["name"] != "lexiflow-release-promotion"
            ):
                _fail("promotion-environment-id-mismatch")
            decisions.append(approval)
    if len(decisions) > 1:
        _fail("promotion-approval-history-ambiguous")
    if not decisions:
        _fail("promotion-human-approval-missing")
    decision = decisions[0]
    if decision.get("state") != "approved":
        _fail("promotion-human-approval-missing")
    if (
        decision["user"].get("login") != owner
        or decision["user"].get("id") != reviewer["id"]
    ):
        _fail("promotion-human-approval-reviewer-mismatch")
    deployment_policy = env.get("deployment_branch_policy")
    if (
        len(reviewers) != 1
        or env.get("can_admins_bypass") is not False
        or not isinstance(deployment_policy, dict)
        or deployment_policy.get("protected_branches") is not False
        or deployment_policy.get("custom_branch_policies") is not True
    ):
        _fail("promotion-environment-protection-mismatch")
    status, policies = config.api(
        "GET",
        f"/repos/{owner}/{repo}/environments/lexiflow-release-promotion/deployment-branch-policies",
    )
    if (
        status != 200
        or not isinstance(policies, dict)
        or not isinstance(policies.get("branch_policies"), list)
        or any(not isinstance(p, dict) for p in policies["branch_policies"])
        or policies.get("total_count") != 1
        or len(policies["branch_policies"]) != 1
        or policies["branch_policies"][0].get("type") != "tag"
        or policies["branch_policies"][0].get("name") != "v*"
    ):
        _fail("promotion-environment-tag-policy-mismatch")


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


def _assets_are_exact_subset(
    assets: list[dict[str, Any]], expected: dict[str, tuple[int, str]]
) -> bool:
    """恢复 draft 时仅允许期望附件的已验证子集；冲突或额外内容失败关闭。"""
    names = [x.get("name") for x in assets]
    ids = [x.get("id") for x in assets]
    if len(names) != len(set(names)) or len(ids) != len(set(ids)):
        return False
    for item in assets:
        name = item.get("name")
        if (
            name not in expected
            or item.get("state") != "uploaded"
            or type(item.get("id")) is not int
            or item["id"] <= 0
            or type(item.get("size")) is not int
            or item["size"] != expected[name][0]
            or item.get("digest") != "sha256:" + expected[name][1]
        ):
            return False
    return True


def _reconcile_uploaded_asset(
    api: GitHub,
    prefix: str,
    release_id: int,
    tag: str,
    source_commit: str,
    notes: str,
    name: str,
    expected_assets: dict[str, tuple[int, str]],
    response_id: int | None,
) -> bool:
    """有限次只读观察同一 draft；绝不重放附件 POST。"""
    for attempt in range(3):
        if attempt:
            time.sleep(0.1 * attempt)
        try:
            status, release = api.api("GET", f"{prefix}/releases/{release_id}")
            assets = _release_assets(api, *prefix.split("/")[2:4], release_id)
        except (PromotionError, OSError, TimeoutError, http.client.HTTPException):
            if attempt < 2:
                continue
            return False
        if status != 200 or not isinstance(release, dict):
            continue
        if (
            release.get("id") != release_id
            or release.get("tag_name") != tag
            or release.get("name") != tag
            or release.get("draft") is not True
            or release.get("prerelease") is not False
            or release.get("target_commitish") != source_commit
            or release.get("body") != notes
        ):
            return False
        if not _assets_are_exact_subset(assets, expected_assets):
            return False
        matching = [asset for asset in assets if asset.get("name") == name]
        if not matching:
            continue
        return response_id is None or matching[0].get("id") == response_id
    return False


def _unknown(stage: str, release_id: int | None = None) -> None:
    raise PromotionError(
        "release-write-state-unknown", stage=stage, release_id=release_id
    )


def _event_directory(version: str, candidate_sha256: str) -> Path:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or not SHA.fullmatch(
        candidate_sha256
    ):
        _fail("promotion-event-identity-invalid")
    base = (
        ROOT / "tmp/quality/release-workflow" / version / "promotion" / candidate_sha256
    )
    current = ROOT
    for part in (
        "tmp",
        "quality",
        "release-workflow",
        version,
        "promotion",
        candidate_sha256,
        "events",
    ):
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            _fail("promotion-event-storage-unavailable")
        try:
            info = current.lstat()
        except OSError:
            _fail("promotion-event-storage-unavailable")
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            _fail("promotion-event-storage-unavailable")
    return base / "events"


def _record_event(
    version: str,
    candidate_sha256: str,
    stage: str,
    state: str,
    *,
    role: str | None = None,
    release_id: int | None = None,
    image_digest: str | None = None,
    asset: str | None = None,
    asset_sha256: str | None = None,
    asset_bytes: int | None = None,
    config_id: str | None = None,
) -> str:
    """以 exclusive append-only 事件保留远端写尝试及结果，不含凭据/响应体。"""
    if state not in ("attempt", "confirmed", "unknown", "blocked"):
        _fail("promotion-event-state-invalid")
    value = {
        "schemaVersion": "lexiflow.release-promotion-event.v1",
        "eventId": str(uuid.uuid4()),
        "createdAtEpochNs": time.time_ns(),
        "softwareVersion": version,
        "candidateSha256": candidate_sha256,
        "stage": stage,
        "state": state,
    }
    if role is not None and role in ("runtime-image",):
        value["role"] = role
    if release_id is not None and type(release_id) is int and release_id > 0:
        value["releaseId"] = release_id
    if image_digest is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest):
        value["imageDigest"] = image_digest
    if config_id is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", config_id):
        value["configId"] = config_id
    if asset is not None and re.fullmatch(r"[A-Za-z0-9._-]{1,255}", asset):
        value["asset"] = asset
    if asset_sha256 is not None and SHA.fullmatch(asset_sha256):
        value["assetSha256"] = asset_sha256
    if type(asset_bytes) is int and asset_bytes > 0:
        value["assetBytes"] = asset_bytes
    directory = _event_directory(version, candidate_sha256)
    path = directory / f"{value['createdAtEpochNs']}-{value['eventId']}.json"
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    try:
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
    except OSError:
        _fail("promotion-event-write-failed")
    return path.relative_to(ROOT).as_posix()


def _has_unknown_write(version: str, candidate_sha256: str) -> bool:
    directory = _event_directory(version, candidate_sha256)
    latest: dict[tuple[Any, ...], tuple[int, str]] = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            _fail("promotion-event-log-invalid")
        try:
            event = _json(path.read_bytes())
        except Exception:
            _fail("promotion-event-log-invalid")
        if (
            not isinstance(event, dict)
            or event.get("schemaVersion") != "lexiflow.release-promotion-event.v1"
            or event.get("softwareVersion") != version
            or event.get("candidateSha256") != candidate_sha256
            or event.get("state") not in ("attempt", "confirmed", "unknown", "blocked")
            or not isinstance(event.get("stage"), str)
            or type(event.get("createdAtEpochNs")) is not int
        ):
            _fail("promotion-event-log-invalid")
        key = (
            (event["stage"],)
            if event["stage"] == "github-release-create"
            else (
                event["stage"],
                event.get("role"),
                event.get("releaseId"),
                event.get("asset"),
            )
        )
        previous = latest.get(key)
        if previous is None or event["createdAtEpochNs"] >= previous[0]:
            latest[key] = (event["createdAtEpochNs"], event["state"])
    # A crash after a write attempt is itself an unresolved outcome even when no
    # response handler got to append an explicit unknown event.
    return any(state in ("attempt", "unknown") for _, state in latest.values())


def _confirmed_registry_digest(version: str, candidate_sha256: str) -> str | None:
    directory = _event_directory(version, candidate_sha256)
    confirmed = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            _fail("promotion-event-log-invalid")
        event = _json(path.read_bytes())
        if (
            isinstance(event, dict)
            and event.get("schemaVersion") == "lexiflow.release-promotion-event.v1"
            and event.get("softwareVersion") == version
            and event.get("candidateSha256") == candidate_sha256
            and event.get("stage") == "ghcr-image"
            and event.get("state") == "confirmed"
            and event.get("role") == "runtime-image"
        ):
            digest = event.get("imageDigest")
            if not isinstance(digest, str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", digest
            ):
                _fail("promotion-event-log-invalid")
            confirmed.append((event.get("createdAtEpochNs", 0), digest))
    return max(confirmed)[1] if confirmed else None


def _publish(
    owner: str,
    repo: str,
    tag: str,
    identity: dict[str, Any],
    assets: dict[str, Path],
    meta: dict[str, tuple[int, str]],
    revalidate: Callable[[], None],
    registry_context: dict[str, Any],
    release_notes: str,
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
        _assert_live_promotion_authorization(api, config, owner, repo, identity)
        if not _immutable(config, owner, repo):
            _fail("immutable-release-required")
        if not _remote_tag(api, owner, repo, tag, identity["sourceCommit"]):
            _fail("remote-tag-mismatch")

    gate()
    try:
        event_args = (identity["softwareVersion"], registry_context["candidate_sha256"])
        _record_event(*event_args, "ghcr-image", "attempt", role="runtime-image")
        try:
            registry = release_registry.publish_images(
                candidate=registry_context["candidate"],
                manifest=registry_context["manifest"],
                identity=registry_context["identity"],
                owner=owner,
                repo=repo,
                endpoint=registry_context["endpoint"],
                candidate_sha256=registry_context["candidate_sha256"],
                manifest_sha256=registry_context["manifest_sha256"],
                revalidate=gate,
                existing_only=registry_context["existing_only"],
            )
        except release_registry.RegistryError as exc:
            _record_event(
                *event_args,
                "ghcr-image",
                "unknown" if exc.write_unknown else "blocked",
                role="runtime-image",
            )
            raise
        image_digest = (
            registry["images"][0].get("manifestDigest")
            if isinstance(registry, dict) and registry.get("images")
            else None
        )
        previous_digest = _confirmed_registry_digest(*event_args)
        if previous_digest is not None and image_digest != previous_digest:
            _record_event(
                *event_args,
                "ghcr-image",
                "blocked",
                role="runtime-image",
                image_digest=image_digest,
                config_id=registry["images"][0].get("imageId"),
            )
            raise PromotionError(
                "registry-manifest-digest-conflict", stage="registry-verify"
            )
        _record_event(
            *event_args,
            "ghcr-image",
            "confirmed",
            role="runtime-image",
            image_digest=image_digest,
            config_id=registry["images"][0].get("imageId"),
        )
        _registry_assets(registry, owner, repo, registry_context, assets, meta)
    except release_registry.RegistryError as exc:
        raise PromotionError(
            exc.reason,
            stage="registry-" + (exc.stage or "unknown"),
            registry_role=exc.role,
        ) from None
    gate()
    image = registry["images"][0]
    image_ref = f"ghcr.io/{owner.lower()}/{repo.lower()}@{image['manifestDigest']}"
    run_command = (
        "podman run --image-volume=ignore --pull=always -d --name lexiflow-release --publish 127.0.0.1:18080:8080 "
        "--volume lexiflow-release-data:/var/lib/lexiflow --memory=2g --cpus=2 "
        f"--stop-timeout=45 {image_ref}"
    )
    automatic = ["", "## 下载与校验", ""]
    for name in sorted(assets):
        url = f"https://github.com/{owner}/{repo}/releases/download/{tag}/{name}"
        automatic.append(
            f"- [{name}]({url}) — SHA-256 `{meta[name][1]}`，{meta[name][0]} bytes"
        )
    automatic.extend(
        ("", "## 固定 digest 安装命令", "", "```sh", run_command, "```", "")
    )
    final_notes = release_notes.rstrip() + "\n" + "\n".join(automatic)
    releases = _pages(api, prefix + "/releases")
    existing = [x for x in releases if x.get("tag_name") == tag]
    if len(existing) > 1:
        raise PromotionError("release-tag-conflict", stage="draft")
    release_id = None
    resumed = False
    if existing:
        release_id = existing[0].get("id")
        if type(release_id) is not int or release_id <= 0:
            raise PromotionError("release-tag-conflict", stage="draft")
        try:
            status, found = api.api("GET", f"{prefix}/releases/{release_id}")
            found_assets = _release_assets(api, owner, repo, release_id)
        except PromotionError:
            _unknown("resume-inspect", release_id)
        if (
            status != 200
            or not isinstance(found, dict)
            or found.get("tag_name") != tag
            or found.get("target_commitish") != identity["sourceCommit"]
            or found.get("name") != tag
            or found.get("body") != final_notes
            or found.get("prerelease") is not False
            or not _assets_are_exact_subset(found_assets, meta)
        ):
            raise PromotionError(
                "release-existing-content-conflict",
                stage="draft",
                release_id=release_id,
            )
        if found.get("draft") is False:
            if found.get("immutable") is not True or not _asset_digest_matches(
                found_assets, meta
            ):
                raise PromotionError(
                    "release-existing-content-conflict",
                    stage="confirm",
                    release_id=release_id,
                )
            try:
                release_registry.verify_images(
                    registry,
                    owner=owner,
                    repo=repo,
                    expected={
                        "softwareVersion": identity["softwareVersion"],
                        "sourceCommit": identity["sourceCommit"],
                        "candidateSha256": registry_context["candidate_sha256"],
                        "manifestSha256": registry_context["manifest_sha256"],
                    },
                )
                latest_status, latest = api.api("GET", prefix + "/releases/latest")
            except Exception:
                _unknown("resume-confirm", release_id)
            if (
                latest_status != 200
                or not isinstance(latest, dict)
                or latest.get("id") != release_id
            ):
                raise PromotionError(
                    "release-existing-content-conflict",
                    stage="confirm",
                    release_id=release_id,
                )
            _record_event(
                *event_args,
                "github-release-publish",
                "confirmed",
                release_id=release_id,
            )
            return {
                "published": True,
                "release_id": release_id,
                "tag": tag,
                "registry_images": registry,
                "recovered": True,
            }
        if found.get("draft") is not True:
            raise PromotionError(
                "release-existing-content-conflict",
                stage="draft",
                release_id=release_id,
            )
        resumed = True
    else:
        _record_event(*event_args, "github-release-create", "attempt")
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
                    "body": final_notes,
                },
            )
        except Exception:
            _record_event(*event_args, "github-release-create", "unknown")
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
            _record_event(
                *event_args, "github-release-create", "unknown", release_id=release_id
            )
            _unknown("create", release_id)
        _record_event(
            *event_args, "github-release-create", "confirmed", release_id=release_id
        )

    def draft_gate() -> None:
        try:
            gate()
        except PromotionError as exc:
            raise PromotionError(
                str(exc), stage="draft", release_id=release_id
            ) from None

    # Inspect the newly-created draft before the first attachment write. A concurrent
    # writer or malformed POST result must never receive files from this candidate.
    try:
        draft_status, draft_before = api.api("GET", f"{prefix}/releases/{release_id}")
        assets_before = _release_assets(api, owner, repo, release_id)
    except PromotionError:
        _unknown("draft-preflight", release_id)
    if (
        draft_status != 200
        or not isinstance(draft_before, dict)
        or type(draft_before.get("id")) is not int
        or draft_before.get("id") != release_id
        or draft_before.get("tag_name") != tag
        or draft_before.get("name") != tag
        or draft_before.get("draft") is not True
        or draft_before.get("prerelease") is not False
        or draft_before.get("target_commitish") != identity["sourceCommit"]
        or draft_before.get("body") != final_notes
        or (resumed and not _assets_are_exact_subset(assets_before, meta))
        or (not resumed and assets_before)
    ):
        raise PromotionError(
            "release-draft-preflight-conflict", stage="draft", release_id=release_id
        )

    already_uploaded: set[str] = set()
    if resumed:
        try:
            existing_assets = _release_assets(api, owner, repo, release_id)
        except PromotionError:
            _unknown("resume-assets", release_id)
        if not _assets_are_exact_subset(existing_assets, meta):
            raise PromotionError(
                "release-existing-content-conflict",
                stage="draft",
                release_id=release_id,
            )
        already_uploaded = {x["name"] for x in existing_assets}
    for name, path in assets.items():
        if name in already_uploaded:
            continue
        draft_gate()
        _record_event(
            *event_args,
            "github-asset-upload",
            "attempt",
            release_id=release_id,
            asset=name,
            asset_sha256=meta[name][1],
            asset_bytes=meta[name][0],
        )
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
                status, item = None, None
        finally:
            os.close(fd)
        response_valid = not (
            status not in (200, 201)
            or not isinstance(item, dict)
            or type(item.get("id")) is not int
            or item["id"] <= 0
            or item.get("state") != "uploaded"
            or item.get("name") != name
            or type(item.get("size")) is not int
            or item["size"] != meta[name][0]
            or item.get("digest") != "sha256:" + meta[name][1]
        )
        if not response_valid:
            _record_event(
                *event_args,
                "github-asset-upload",
                "unknown",
                release_id=release_id,
                asset=name,
                asset_sha256=meta[name][1],
                asset_bytes=meta[name][0],
            )
            response_id = (
                item.get("id")
                if isinstance(item, dict)
                and type(item.get("id")) is int
                and item["id"] > 0
                else None
            )
            if not _reconcile_uploaded_asset(
                api,
                prefix,
                release_id,
                tag,
                identity["sourceCommit"],
                final_notes,
                name,
                meta,
                response_id,
            ):
                _unknown("upload", release_id)
        _record_event(
            *event_args,
            "github-asset-upload",
            "confirmed",
            release_id=release_id,
            asset=name,
            asset_sha256=meta[name][1],
            asset_bytes=meta[name][0],
        )
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
        or draft.get("name") != tag
        or draft.get("draft") is not True
        or draft.get("prerelease") is not False
        or draft.get("target_commitish") != identity["sourceCommit"]
        or draft.get("body") != final_notes
        or not _asset_digest_matches(listed, meta)
    ):
        raise PromotionError(
            "release-draft-incomplete", stage="draft", release_id=release_id
        )
    try:
        release_registry.verify_images(
            registry,
            owner=owner,
            repo=repo,
            expected={
                "softwareVersion": identity["softwareVersion"],
                "sourceCommit": identity["sourceCommit"],
                "candidateSha256": registry_context["candidate_sha256"],
                "manifestSha256": registry_context["manifest_sha256"],
            },
        )
    except release_registry.RegistryError as exc:
        raise PromotionError(
            exc.reason,
            stage="registry-verify",
            release_id=release_id,
            registry_role=exc.role,
        ) from None
    draft_gate()
    _record_event(
        *event_args, "github-release-publish", "attempt", release_id=release_id
    )
    try:
        status, published = api.api(
            "PATCH",
            f"{prefix}/releases/{release_id}",
            {"draft": False, "make_latest": "true"},
        )
    except Exception:
        _record_event(
            *event_args, "github-release-publish", "unknown", release_id=release_id
        )
        _unknown("publish", release_id)
    if (
        status != 200
        or not isinstance(published, dict)
        or published.get("id") != release_id
        or published.get("tag_name") != tag
        or published.get("draft") is not False
    ):
        _record_event(
            *event_args, "github-release-publish", "unknown", release_id=release_id
        )
        _unknown("publish", release_id)
    _record_event(
        *event_args, "github-release-publish", "confirmed", release_id=release_id
    )
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
        or final.get("name") != tag
        or final.get("draft") is not False
        or final.get("prerelease") is not False
        or final.get("target_commitish") != identity["sourceCommit"]
        or final.get("body") != final_notes
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
    return {
        "published": True,
        "release_id": release_id,
        "tag": tag,
        "registry_images": registry,
    }


def _stage_release_assets(
    candidate: Path, manifest: dict[str, Any], version: str, workspace: Path
) -> tuple[dict[str, Path], dict[str, tuple[int, str]]]:
    """在首个远端写入前验证并固定公开 ZIP 与对应源码原始字节。"""
    extensions = [x for x in manifest["artifacts"] if x["role"] == "extension"]
    sources = [x for x in manifest["artifacts"] if x["role"] == "corresponding-sources"]
    if len(extensions) != 1 or len(sources) != 1:
        raise PromotionError("release-artifacts-invalid", stage="assets")
    assets: dict[str, Path] = {}
    meta: dict[str, tuple[int, str]] = {}
    for item, name in (
        (extensions[0], f"lexiflow-extension-{version}.zip"),
        (sources[0], sources[0]["path"].rsplit("/", 1)[-1]),
    ):
        if name.endswith(".zip") and item["bytes"] > MAX_EXTENSION:
            _fail("candidate-extension-invalid")
        if item["bytes"] > MAX_ASSET:
            _fail("asset-size-limit")
        fd, before = _stable_fd(candidate, "payload/" + item["path"])
        target = workspace / name
        try:
            if before.st_size != item["bytes"]:
                _fail("candidate-artifact-hash-mismatch")
            digest = hashlib.sha256()
            with target.open("xb") as out:
                _read_exact(fd, item["bytes"], digest, out.write)
                out.flush()
                os.fsync(out.fileno())
            if digest.hexdigest() != item["sha256"] or _stamp(before) != _stamp(
                os.fstat(fd)
            ):
                _fail("candidate-artifact-hash-mismatch")
        finally:
            os.close(fd)
        assets[name] = target
        meta[name] = (item["bytes"], item["sha256"])
    return assets, meta


def _registry_assets(
    registry: Any,
    owner: str,
    repo: str,
    context: dict[str, Any],
    assets: dict[str, Path],
    meta: dict[str, tuple[int, str]],
) -> None:
    """生成唯一公开 release.json；不公开候选归档或 sidecar。"""
    identity, manifest = context["identity"], context["manifest"]
    image_candidates = [
        x for x in manifest["artifacts"] if x["role"] == "runtime-image"
    ]
    if (
        not isinstance(registry, dict)
        or len(registry.get("images", [])) != 1
        or len(image_candidates) != 1
        or registry.get("softwareVersion") != identity["softwareVersion"]
        or registry.get("sourceCommit") != identity["sourceCommit"]
        or registry.get("candidateSha256") != context["candidate_sha256"]
        or registry.get("manifestSha256") != context["manifest_sha256"]
    ):
        raise PromotionError("registry-mapping-invalid", stage="registry-mapping")
    remote = registry["images"][0]
    item = image_candidates[0]
    if (
        remote.get("role") != "runtime-image"
        or remote.get("platform") != "linux/arm64"
        or remote.get("imageId") != item.get("imageDigest")
        or remote.get("archiveSha256") != item.get("sha256")
        or not isinstance(remote.get("manifestDigest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", remote["manifestDigest"])
        or remote.get("reference")
        != f"ghcr.io/{owner.lower()}/{repo.lower()}:{identity['softwareVersion']}"
    ):
        raise PromotionError("registry-mapping-invalid", stage="registry-mapping")
    ext = next((x for x in manifest["artifacts"] if x["role"] == "extension"), None)
    sources = [x for x in manifest["artifacts"] if x["role"] == "corresponding-sources"]
    datasets = [x for x in manifest["artifacts"] if x["role"] == "dataset"]
    if ext is None or len(sources) != 1 or len(datasets) != 1:
        raise PromotionError("release-artifacts-invalid", stage="assets")
    version = identity["softwareVersion"]
    extension_name = f"lexiflow-extension-{version}.zip"
    source_name = sources[0]["path"].rsplit("/", 1)[-1]
    release_name = "release.json"
    workspace = Path(context["workspace"])
    source_path = workspace / source_name
    src = sources[0]
    source_meta = src.get("metadata")
    source_items = source_meta.get("items") if isinstance(source_meta, dict) else None
    if (
        not isinstance(source_meta, dict)
        or not isinstance(source_meta.get("indexSha256"), str)
        or not SHA.fullmatch(source_meta["indexSha256"])
        or not isinstance(source_items, list)
        or not source_items
        or len(source_items) > MAX_LICENSES
    ):
        raise PromotionError("corresponding-sources-index-invalid", stage="assets")
    source_ids = set()
    licenses_by_id = {
        x.get("id"): x for x in manifest.get("licenses", []) if isinstance(x, dict)
    }
    for source in source_items:
        expected_source_fields = {
            "id",
            "component",
            "licenseId",
            "licenseName",
            "sourceUrl",
            "noticePath",
            "archivePath",
            "bytes",
            "sha256",
        }
        if (
            not isinstance(source, dict)
            or set(source) != expected_source_fields
            or not isinstance(source.get("id"), str)
            or source["id"] in source_ids
            or not isinstance(source.get("component"), str)
            or not isinstance(source.get("sourceUrl"), str)
            or not source["sourceUrl"].startswith("https://")
            or not isinstance(source.get("noticePath"), str)
            or not isinstance(source.get("archivePath"), str)
            or type(source.get("bytes")) is not int
            or source["bytes"] < 1
            or not isinstance(source.get("sha256"), str)
            or not SHA.fullmatch(source["sha256"])
        ):
            raise PromotionError("corresponding-sources-index-invalid", stage="assets")
        license = licenses_by_id.get(source["id"])
        source_artifact = next(
            (
                x
                for x in manifest["artifacts"]
                if x.get("role") == "license"
                and x.get("path") == source["noticePath"]
                and source["id"] in x.get("licenseIds", [])
            ),
            None,
        )
        if (
            license is None
            or source_artifact is None
            or source["component"] != license.get("component")
            or source["licenseId"] != license.get("licenseId")
            or source["licenseName"] != license.get("licenseName")
            or source["sourceUrl"] != license.get("sourceUrl")
            or source["noticePath"] != license.get("noticePath")
            or source["archivePath"] != "materials/" + license.get("noticePath", "")
            or source["bytes"] != source_artifact.get("bytes")
            or source["sha256"] != source_artifact.get("sha256")
        ):
            raise PromotionError(
                "corresponding-sources-license-binding-invalid", stage="assets"
            )
        source_ids.add(source["id"])
    if source_ids != set(src.get("licenseIds", [])):
        raise PromotionError(
            "corresponding-sources-license-binding-invalid", stage="assets"
        )
    dataset = manifest["dataset"]
    data_item = datasets[0]
    if (
        data_item.get("metadata", {}).get("releaseId") != dataset.get("releaseId")
        or data_item.get("metadata", {}).get("preparationId")
        != dataset.get("preparationId")
        or data_item.get("metadata", {}).get("ruleId") != dataset.get("ruleId")
        or data_item.get("metadata", {}).get("sqlVersion") != manifest.get("sqlVersion")
    ):
        raise PromotionError("published-dataset-binding-invalid", stage="assets")
    expected_dataset = {
        "releaseId": dataset["releaseId"],
        "preparationId": dataset["preparationId"],
        "ruleId": dataset["ruleId"],
        "sqlVersion": manifest["sqlVersion"],
        "sha256": data_item["sha256"],
    }
    if ext.get("metadata", {}).get("expectedDataset") != expected_dataset:
        raise PromotionError(
            "extension-published-dataset-binding-invalid", stage="assets"
        )
    if source_path.name != src["path"].rsplit("/", 1)[-1]:
        raise PromotionError("corresponding-sources-name-invalid", stage="assets")
    if source_name not in assets or meta.get(source_name) != (
        src["bytes"],
        src["sha256"],
    ):
        raise PromotionError("release-source-not-prepared", stage="assets")
    # Extension is copied from a stable candidate FD; candidate tar remains private.
    if extension_name not in assets or meta.get(extension_name) != (
        ext["bytes"],
        ext["sha256"],
    ):
        raise PromotionError("release-extension-not-prepared", stage="assets")
    source_size, source_sha = src["bytes"], src["sha256"]

    def asset(name: str, size: int, sha: str) -> dict[str, Any]:
        return {
            "name": name,
            "bytes": size,
            "sha256": sha,
            "url": f"https://github.com/{owner}/{repo}/releases/download/v{version}/{name}",
        }

    ext_record = asset(extension_name, ext["bytes"], ext["sha256"])
    src_record = asset(source_name, source_size, source_sha)
    release = {
        "schemaVersion": "lexiflow.release.v1",
        "softwareVersion": version,
        "sourceCommit": identity["sourceCommit"],
        "buildIdentity": identity,
        "apiContract": manifest["apiContract"],
        "sqlVersion": manifest["sqlVersion"],
        "dataset": manifest["dataset"],
        "publishedDataset": expected_dataset,
        "image": {
            "reference": f"ghcr.io/{owner.lower()}/{repo.lower()}@{remote['manifestDigest']}",
            "digest": remote["manifestDigest"],
            "configId": remote["imageId"],
            "platform": remote["platform"],
            "bytes": item["bytes"],
        },
        "assets": {
            "extension": ext_record,
            "thirdPartySources": {
                **src_record,
                "inventory": src.get("metadata", {}).get("items"),
                "indexSha256": src.get("metadata", {}).get("indexSha256"),
            },
        },
    }
    release_path = workspace / release_name
    release_path.write_text(
        json.dumps(release, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    for name, path in ((release_name, release_path),):
        data = path.read_bytes()
        assets[name] = path
        meta[name] = (len(data), hashlib.sha256(data).hexdigest())


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
    images = []
    source_archives = []
    total = len(manifest_bytes) + len(sidecar_bytes)
    for item in artifacts:
        _bounded_item(item)
        if item["path"] in paths:
            _fail("candidate-manifest-invalid")
        paths.add(item["path"])
        if item["role"] == "extension":
            extensions.append(item)
        if item["role"] == "runtime-image":
            images.append(item)
        if item["role"] == "corresponding-sources":
            source_archives.append(item)
        total += item["bytes"]
        if total > MAX_UNCOMPRESSED:
            _fail("asset-size-limit")
    if len(extensions) != 1 or extensions[0]["bytes"] > MAX_EXTENSION:
        _fail("candidate-extension-invalid")
    if (
        len(images) != 1
        or {item["role"] for item in images} != {"runtime-image"}
        or any(
            item.get("platform") != "linux/arm64"
            or not isinstance(item.get("imageDigest"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["imageDigest"])
            for item in images
        )
    ):
        _fail("candidate-image-set-invalid")
    if len(source_archives) != 1 or not source_archives[0]["path"].endswith(".tar.gz"):
        _fail("candidate-corresponding-sources-invalid")
    try:
        release_registry.verify_candidate_images(candidate, manifest, identity)
    except release_registry.RegistryError as exc:
        raise PromotionError(exc.reason, stage="candidate") from exc
    return proof, identity, manifest_bytes, candidate_bytes, sidecar_bytes


def promote(
    *,
    submission_id: str,
    candidate_directory: str | Path,
    publish: bool = False,
    confirmed_recovery: bool = False,
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

    release_notes = _release_notes(
        identity["softwareVersion"], identity["sourceCommit"]
    )
    if not publish:
        revalidate()
        return {
            "result": "PASS",
            "scope": "promotion-precheck",
            "published": False,
            "release_id": None,
            "tag": tag,
        }

    candidate_sha256 = proof["candidate"]["candidateSha256"]
    has_unknown = _has_unknown_write(identity["softwareVersion"], candidate_sha256)
    if confirmed_recovery and not has_unknown:
        _fail("promotion-recovery-not-required")
    if has_unknown and not confirmed_recovery:
        raise PromotionError(
            "promotion-recovery-confirmation-required", stage="recovery"
        )

    workspace = Path(tempfile.mkdtemp(prefix="lexiflow-release-promotion-"))
    try:
        version = identity["softwareVersion"]
        assets, expected = _stage_release_assets(
            candidate, manifest, version, workspace
        )
        context = {
            "candidate": candidate,
            "manifest": manifest,
            "identity": identity,
            "endpoint": os.environ.get("LEXIFLOW_RELEASE_PODMAN_ENDPOINT", ""),
            "candidate_sha256": proof["candidate"]["candidateSha256"],
            "manifest_sha256": proof["candidate"]["manifestSha256"],
            "workspace": str(workspace),
            "existing_only": bool(has_unknown and confirmed_recovery),
        }
        revalidate()
        owner, repo = _repository(ROOT)
        declared = os.environ.get("GITHUB_REPOSITORY")
        if declared and declared != f"{owner}/{repo}":
            _fail("repository-origin-mismatch")
        result = _publish(
            owner,
            repo,
            tag,
            identity,
            assets,
            expected,
            revalidate,
            {
                "candidate": candidate,
                "manifest": manifest,
                "identity": identity,
                **context,
            },
            release_notes,
        )
        result["scope"] = "promotion-publish"
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
    parser.add_argument("--confirmed-recovery", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = promote(
            submission_id=args.submission_id,
            candidate_directory=args.candidate_directory,
            publish=args.publish,
            confirmed_recovery=args.confirmed_recovery,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["result"] == "PASS" else 2
    except PromotionError as exc:
        result = {"result": "BLOCKED", "reason": str(exc)}
        if exc.stage:
            result.update(
                published="unknown", write_stage=exc.stage, release_id=exc.release_id
            )
        if exc.registry_role == "runtime-image":
            result["registry_role"] = exc.registry_role
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
