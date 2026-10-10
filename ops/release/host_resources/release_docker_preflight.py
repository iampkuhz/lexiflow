"""为发行运行器提供只读且有界的本机 Docker 预检。"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from ops.release.host_resources import release_process_control as release_process

_ARCHITECTURES = {"arm64", "aarch64"}

_ENV_KEYS = {"PATH", "DOCKER_HOST"}
_MAX_OUTPUT = 256 * 1024
_TIMEOUT = 30.0
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
_INFO_FORMAT = (
    "{{json .ID}}|{{json .OSType}}|{{json .Architecture}}|{{json .ServerVersion}}"
)


@dataclass(frozen=True, repr=False)
class Binding:
    """只供当前进程传递的已核对引擎身份，不是授权签名。"""

    endpoint: str
    socket_path: str
    socket_device: int
    socket_inode: int
    daemon_id: str
    environment: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class PreflightResult:
    """不含命令输出、路径或环境值的稳定预检结果。"""

    status: str
    reason: str
    binding: Binding | None = None


@dataclass(frozen=True)
class _CommandResult:
    status: str
    returncode: int | None
    stdout: bytes


def _result(
    status: str, reason: str, binding: Binding | None = None
) -> PreflightResult:
    return PreflightResult(status, reason, binding)


def _safe_environment(
    environment: Mapping[str, str],
) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(environment, Mapping):
        return None, "invalid-environment"
    values: dict[str, str] = {}
    for key, value in environment.items():
        if key not in _ENV_KEYS or not isinstance(value, str):
            continue
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            return None, "invalid-environment"
        values[key] = value
    if _socket_endpoint(values.get("DOCKER_HOST", "")) is None:
        return None, "invalid-host"
    return values, None


def _socket_endpoint(endpoint: str) -> tuple[str, str] | None:
    """验证 unix 绝对 endpoint，并返回 endpoint 与其路径。"""
    if not endpoint or any(
        char.isspace() or ord(char) < 32 or ord(char) == 127 for char in endpoint
    ):
        return None
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return None
    if (
        parsed.scheme != "unix"
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or endpoint != f"unix://{parsed.path}"
    ):
        return None
    path = parsed.path
    if not path.startswith("/") or "\x00" in path:
        return None
    return endpoint, path


def _command_environment(values: Mapping[str, str]) -> dict[str, str]:
    return {"PATH": values.get("PATH") or "/usr/bin:/bin"}


def _run_bounded(argv: list[str], env: Mapping[str, str]) -> _CommandResult:
    """复用发行监督，环境不可用与清理失败严格区分。"""
    try:
        facts = release_process.run_owned(argv, Path.cwd(), env, _TIMEOUT, _MAX_OUTPUT)
    except release_process.ProcessError as exc:
        status = (
            "unsupported-platform"
            if exc.reason == "release-process-lifetime-unsupported"
            else "spawn-failed"
            if exc.status == "BLOCKED"
            else "cleanup-failed"
        )
        return _CommandResult(status, None, b"")
    if not facts.cleanup_succeeded:
        return _CommandResult("cleanup-failed", facts.returncode, b"")
    reason = (
        "output-limit" if facts.overflow else "timeout" if facts.timed_out else "exited"
    )
    return _CommandResult(
        reason, facts.returncode, facts.stdout if reason == "exited" else b""
    )


def _run_docker(
    docker: str, endpoint: str, args: list[str], env: Mapping[str, str]
) -> _CommandResult:
    return _run_bounded([docker, "--host", endpoint, *args], env)


def _inspect_socket(endpoint: str) -> tuple[str, int, int] | None:
    parsed = _socket_endpoint(endpoint)
    if parsed is None:
        return None
    path = Path(parsed[1])
    try:
        resolved = str(path.resolve(strict=True))
        info = os.stat(path)
    except (OSError, RuntimeError):
        return None
    if not stat_is_socket(info.st_mode):
        return None
    return resolved, info.st_dev, info.st_ino


def stat_is_socket(mode: int) -> bool:
    """避免暴露平台特定 stat 类型之外的逻辑。"""
    return stat.S_ISSOCK(mode)


def _daemon_id(
    docker: str, endpoint: str, env: Mapping[str, str]
) -> tuple[dict[str, str] | None, str | None]:
    result = _run_docker(docker, endpoint, ["info", "--format", _INFO_FORMAT], env)
    if result.status == "cleanup-failed":
        return None, "docker-process-cleanup-failed"
    if result.status == "missing-cli":
        return None, "docker-cli-missing"
    if result.status in {"timeout", "output-limit", "unsupported-platform"}:
        return None, "docker-info-unavailable"
    if result.status != "exited" or result.returncode != 0:
        return None, "docker-daemon-unavailable"
    try:
        parts = result.stdout.decode("utf-8").rstrip("\n").split("|")
        if len(parts) != 4:
            raise ValueError
        names = ("ID", "OSType", "Architecture", "ServerVersion")
        fields = dict(zip(names, (json.loads(part) for part in parts), strict=True))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, "docker-info-invalid"
    if any(not isinstance(item, str) or not item for item in fields.values()):
        return None, "docker-info-invalid"
    if not _ID.fullmatch(fields["ID"]):
        return None, "docker-info-invalid"
    return fields, None


def preflight(
    environment: Mapping[str, str],
) -> PreflightResult:
    """以显式 Unix endpoint 只读核验并绑定本机 Docker daemon。"""
    values, error = _safe_environment(environment)
    if error:
        return _result("FAIL", error)
    assert values is not None
    command_env = _command_environment(values)
    docker = shutil.which("docker", path=command_env.get("PATH", ""))
    if not docker:
        return _result("BLOCKED", "docker-cli-missing")
    endpoint = values["DOCKER_HOST"]
    socket_identity = _inspect_socket(endpoint)
    if socket_identity is None:
        return _result("BLOCKED", "local-socket-unavailable")
    fields, daemon_error = _daemon_id(docker, endpoint, command_env)
    if _inspect_socket(endpoint) != socket_identity:
        return _result("FAIL", "socket-identity-changed")
    if daemon_error:
        status = (
            "FAIL" if daemon_error == "docker-process-cleanup-failed" else "BLOCKED"
        )
        return _result(status, daemon_error)
    assert fields is not None
    if fields["OSType"] != "linux":
        return _result("BLOCKED", "daemon-os-mismatch")
    if fields["Architecture"] not in _ARCHITECTURES:
        return _result("BLOCKED", "daemon-platform-mismatch")
    compose = _run_docker(docker, endpoint, ["compose", "version"], command_env)
    if _inspect_socket(endpoint) != socket_identity:
        return _result("FAIL", "socket-identity-changed")
    if compose.status != "exited" or compose.returncode != 0:
        if compose.status == "cleanup-failed":
            return _result("FAIL", "docker-process-cleanup-failed")
        reason = (
            "compose-unavailable"
            if compose.status not in {"timeout", "output-limit", "unsupported-platform"}
            else "compose-check-unavailable"
        )
        return _result("BLOCKED", reason)
    return _result(
        "PASS",
        "docker-readonly-preflight-succeeded",
        Binding(
            endpoint=endpoint,
            socket_path=socket_identity[0],
            socket_device=socket_identity[1],
            socket_inode=socket_identity[2],
            daemon_id=fields["ID"],
            environment=tuple(sorted(command_env.items())),
        ),
    )


def recheck(binding: Binding) -> PreflightResult:
    """副作用边界前重验 socket 身份与 daemon ID。"""
    if not isinstance(binding, Binding):
        return _result("FAIL", "invalid-binding")
    identity = _inspect_socket(binding.endpoint)
    if identity is None or identity != (
        binding.socket_path,
        binding.socket_device,
        binding.socket_inode,
    ):
        return _result("FAIL", "socket-identity-changed")
    env = dict(binding.environment)
    docker = shutil.which("docker", path=env.get("PATH", ""))
    if not docker:
        return _result("FAIL", "docker-cli-changed")
    fields, error = _daemon_id(docker, binding.endpoint, env)
    if _inspect_socket(binding.endpoint) != identity:
        return _result("FAIL", "socket-identity-changed")
    if error:
        return _result(
            "FAIL",
            "docker-process-cleanup-failed"
            if error == "docker-process-cleanup-failed"
            else "daemon-identity-unavailable",
        )
    assert fields is not None
    if fields["ID"] != binding.daemon_id:
        return _result("FAIL", "daemon-identity-changed")
    if fields["OSType"] != "linux" or fields["Architecture"] not in _ARCHITECTURES:
        return _result("FAIL", "daemon-platform-changed")
    return _result("PASS", "docker-readonly-recheck-succeeded", binding)
