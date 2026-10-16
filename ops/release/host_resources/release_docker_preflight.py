"""为发行运行器提供只读且有界的本机 Docker 预检。"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
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
_MAX_API_RESPONSE = 256 * 1024


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


def _unix_api_json(endpoint: str, path: str) -> dict[str, object] | None:
    """Read one bounded JSON response from the already-bound Unix endpoint."""
    parsed = _socket_endpoint(endpoint)
    if parsed is None:
        return None

    class UnixConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(_TIMEOUT)
            self.sock.connect(parsed[1])

    connection = UnixConnection("localhost", timeout=_TIMEOUT)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            return None
        payload = response.read(_MAX_API_RESPONSE + 1)
        if len(payload) > _MAX_API_RESPONSE:
            return None
        value = json.loads(payload.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except (
        OSError,
        http.client.HTTPException,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None
    finally:
        connection.close()


def _valid_id_mappings(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    for key in ("uidmap", "gidmap"):
        mappings = value.get(key)
        if not isinstance(mappings, list):
            return False
        for mapping in mappings:
            if not isinstance(mapping, dict):
                return False
            values = tuple(
                mapping.get(field) for field in ("container_id", "host_id", "size")
            )
            if any(type(item) is not int for item in values):
                return False
            container_id, host_id, size = values
            if min(container_id, host_id) < 0 or size <= 0:
                return False
            if max(container_id + size, host_id + size) > 2**32:
                return False
    return True


def _engine_identity(endpoint: str) -> tuple[str | None, str | None]:
    """Classify via the local API; Podman identity uses stable host/store facts."""
    version = _unix_api_json(endpoint, "/version")
    if version is None:
        return None, "engine-api-unavailable"
    components = version.get("Components")
    if not isinstance(components, list):
        return None, "engine-unrecognized"
    podman_components = [
        item
        for item in components
        if isinstance(item, dict) and item.get("Name") == "Podman Engine"
    ]
    docker_components = [
        item
        for item in components
        if isinstance(item, dict) and item.get("Name") == "Engine"
    ]
    if len(podman_components) > 1 or len(docker_components) > 1:
        return None, "engine-component-invalid"
    if bool(podman_components) == bool(docker_components):
        return None, "engine-unrecognized"
    is_podman = bool(podman_components)
    podman_component = podman_components[0] if is_podman else None
    details = (
        podman_component.get("Details") if isinstance(podman_component, dict) else None
    )
    api_version = details.get("APIVersion") if isinstance(details, dict) else None
    if is_podman:
        if not isinstance(api_version, str) or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+", api_version
        ):
            return None, "podman-api-invalid"
        api_major = api_version.split(".", maxsplit=1)[0]
        info = _unix_api_json(endpoint, f"/v{api_major}.0.0/libpod/info")
        if info is None:
            return None, "podman-info-unavailable"
        host, store, ver = info.get("host"), info.get("store"), info.get("version")
        if not all(isinstance(item, dict) for item in (host, store, ver)):
            return None, "podman-info-invalid"
        if host.get("os") != "linux" or host.get("arch") not in _ARCHITECTURES:
            return None, "podman-platform-mismatch"
        hostname = host.get("hostname")
        if not isinstance(hostname, str) or not hostname:
            return None, "podman-info-invalid"
        security = host.get("security")
        if not isinstance(security, dict) or not isinstance(
            security.get("rootless"), bool
        ):
            return None, "podman-info-invalid"
        mappings = host.get("idMappings")
        if not _valid_id_mappings(mappings):
            return None, "podman-info-invalid"
        driver = store.get("graphDriverName")
        store_paths = {
            key: store.get(key) for key in ("graphRoot", "runRoot", "volumePath")
        }
        if (
            not isinstance(driver, str)
            or not driver
            or any(
                not isinstance(value, str) or not value.startswith("/")
                for value in store_paths.values()
            )
            or not isinstance(store.get("transientStore"), bool)
        ):
            return None, "podman-info-invalid"
        version_string = ver.get("Version")
        if not isinstance(version_string, str) or not re.fullmatch(
            r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version_string
        ):
            return None, "podman-info-invalid"
        # The product POSIX entry persists this exact tuple, including its LF.
        # Equal facts must not acquire a different JSON-encoded binding here.
        if any(
            not re.fullmatch(r"[A-Za-z0-9._/+:-]+", value, re.ASCII)
            for value in (hostname, driver, *store_paths.values(), version_string)
        ) or (
            security["rootless"]
            and any(not mappings[key] for key in ("uidmap", "gidmap"))
        ):
            return None, "podman-info-invalid"
        map_fields = [
            "".join(
                f"{item['container_id']},{item['host_id']},{item['size']};"
                for item in mappings[key]
            )
            or "-"
            for key in ("uidmap", "gidmap")
        ]
        canonical = (
            "|".join(
                (
                    host["os"],
                    host["arch"],
                    hostname,
                    str(security["rootless"]).lower(),
                    "present",
                    *map_fields,
                    driver,
                    *store_paths.values(),
                    str(store["transientStore"]).lower(),
                    version_string,
                )
            )
            + "\n"
        )
        return "podman:" + hashlib.sha256(canonical.encode("ascii")).hexdigest(), None
    is_docker = isinstance(components, list) and any(
        isinstance(item, dict) and item.get("Name") == "Engine" for item in components
    )
    if not is_docker:
        return None, "engine-unrecognized"
    # Docker's API version response has the Engine component. Its CLI ID remains
    # the engine identity and is still compared strictly on every recheck.
    return "docker", None


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
    engine_id, engine_error = _engine_identity(endpoint)
    fields, daemon_error = _daemon_id(docker, endpoint, command_env)
    if _inspect_socket(endpoint) != socket_identity:
        return _result("FAIL", "socket-identity-changed")
    if engine_error or daemon_error:
        daemon_error = engine_error or daemon_error
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
            daemon_id=engine_id
            if engine_id and engine_id.startswith("podman:")
            else fields["ID"],
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
    engine_id, engine_error = _engine_identity(binding.endpoint)
    fields, error = _daemon_id(docker, binding.endpoint, env)
    if _inspect_socket(binding.endpoint) != identity:
        return _result("FAIL", "socket-identity-changed")
    if engine_error or error:
        return _result(
            "FAIL",
            "docker-process-cleanup-failed"
            if error == "docker-process-cleanup-failed"
            else "daemon-identity-unavailable",
        )
    assert fields is not None
    actual_id = (
        engine_id if engine_id and engine_id.startswith("podman:") else fields["ID"]
    )
    if actual_id != binding.daemon_id:
        return _result("FAIL", "daemon-identity-changed")
    if fields["OSType"] != "linux" or fields["Architecture"] not in _ARCHITECTURES:
        return _result("FAIL", "daemon-platform-changed")
    return _result("PASS", "docker-readonly-recheck-succeeded", binding)
