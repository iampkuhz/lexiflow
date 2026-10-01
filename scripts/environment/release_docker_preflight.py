"""为发行运行器提供只读且有界的本机 Docker 预检。"""

from __future__ import annotations

import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

_TARGETS = {"linux/amd64": {"amd64", "x86_64"}, "linux/arm64": {"arm64", "aarch64"}}
_ENV_KEYS = {"PATH", "HOME", "DOCKER_CONFIG", "DOCKER_HOST", "DOCKER_CONTEXT"}
_MAX_OUTPUT = 256 * 1024
_TIMEOUT = 30.0
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
_CONTEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z", re.ASCII)
_INFO_FORMAT = (
    "{{json .ID}}|{{json .OSType}}|{{json .Architecture}}|{{json .ServerVersion}}"
)
_CONTEXT_FORMAT = "{{json .Endpoints.docker.Host}}"


@dataclass(frozen=True, repr=False)
class Binding:
    """只供当前进程传递的已核对引擎身份，不是授权签名。"""

    endpoint: str
    socket_path: str
    socket_device: int
    socket_inode: int
    daemon_id: str
    target_platform: str
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
    host, context = values.get("DOCKER_HOST", ""), values.get("DOCKER_CONTEXT", "")
    if host and context:
        return None, "host-context-conflict"
    if host:
        endpoint = _socket_endpoint(host)
        if endpoint is None:
            return None, "invalid-host"
    elif context and not _CONTEXT.fullmatch(context):
        return None, "invalid-context"
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
    env = {"PATH": values.get("PATH") or "/usr/bin:/bin"}
    for key in ("HOME", "DOCKER_CONFIG"):
        if values.get(key):
            env[key] = values[key]
    return env


def _group_state(pid: int) -> bool | None:
    """观察组存在性；权限错误或未知错误均不算清理成功。"""
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None


def _wait_child_exit(process: subprocess.Popen[bytes], deadline: float) -> bool | None:
    """用 waitid(WNOWAIT)观察 child 退出但不 reap，保留 PGID。"""
    waitid_flags = ("waitid", "WNOWAIT", "WEXITED", "WNOHANG")
    if any(not hasattr(os, name) for name in waitid_flags):
        return None
    while time.monotonic() < deadline:
        try:
            state = os.waitid(
                os.P_PID,
                process.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except ChildProcessError:
            return True
        except OSError:
            return None
        if state is not None and state.si_pid:
            return True
        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
    return False


def _reap_direct_child(process: subprocess.Popen[bytes]) -> bool:
    """有界等待当前 Popen child；超时后只向其 own PID 发 kill。"""
    waited = False
    cleanup_ok = True
    interruption: KeyboardInterrupt | None = None
    try:
        process.wait(timeout=2)
        waited = True
    except subprocess.TimeoutExpired:
        pass
    except KeyboardInterrupt as exc:
        interruption = exc
    except (PermissionError, OSError, subprocess.SubprocessError):
        cleanup_ok = False
    if not waited:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        except (PermissionError, OSError):
            cleanup_ok = False
        try:
            process.wait(timeout=2)
            waited = True
        except KeyboardInterrupt as exc:
            interruption = interruption or exc
        except (PermissionError, OSError, subprocess.SubprocessError):
            cleanup_ok = False
        if not waited:
            # Last bounded wait is still required after the exact-child kill attempt.
            try:
                process.wait(timeout=2)
                waited = True
            except KeyboardInterrupt as exc:
                interruption = interruption or exc
            except (PermissionError, OSError, subprocess.SubprocessError):
                cleanup_ok = False
    if interruption is not None:
        raise interruption
    return cleanup_ok and waited


def _kill_group(process: subprocess.Popen[bytes]) -> bool:
    """终止并确认回收本次独立进程组，不吞掉权限失败。"""
    process_reaped = False
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except (PermissionError, OSError):
            pass
        deadline = time.monotonic() + 0.25
        group_state = _group_state(process.pid)
        while group_state is True and time.monotonic() < deadline:
            time.sleep(0.02)
            group_state = _group_state(process.pid)
        if group_state is not False:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except (PermissionError, OSError):
                pass
    except BaseException:
        # Interruptions/errors still attempt exact owned-group termination before reap.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        raise
    finally:
        # waitid(WNOWAIT) 保留可识别 child；killpg 信号都先于 wait/reap。
        # Popen.wait 只 reap direct child；孤儿由系统接管。
        # 当前进程不能代 reap 非子进程。
        # reap 后绝不再 signal 数字 PGID，避免 PID/PGID 复用导致误杀。
        process_reaped = _reap_direct_child(process)
    if not process_reaped:
        return False
    # Give the OS reaper a brief window for orphan zombies; only observe after reap.
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        final_group_state = _group_state(process.pid)
        if final_group_state is False:
            # Darwin may return EPERM for a group containing only an unreaped
            # zombie. Successful direct-child reap plus confirmed group absence
            # is the cleanup postcondition, regardless of earlier signal errors.
            return True
        if final_group_state is None:
            return False
        time.sleep(0.02)
    return False


def _run_bounded(argv: list[str], env: Mapping[str, str]) -> _CommandResult:
    """并行排空两条管道，在超时或超量时先杀组再回收。"""
    if (
        os.name != "posix"
        or not hasattr(os, "killpg")
        or not hasattr(os, "waitid")
        or not hasattr(os, "WNOWAIT")
        or not hasattr(os, "WEXITED")
        or not hasattr(os, "WNOHANG")
    ):
        return _CommandResult("unsupported-platform", None, b"")
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            shell=False,
            start_new_session=True,
        )
    except FileNotFoundError:
        return _CommandResult("missing-cli", None, b"")
    except OSError:
        return _CommandResult("spawn-failed", None, b"")
    assert process.stdout is not None and process.stderr is not None
    try:
        selector = selectors.DefaultSelector()
    except (OSError, ValueError):
        reaped = _kill_group(process)
        process.stdout.close()
        process.stderr.close()
        return _CommandResult(
            "unsupported-platform" if reaped else "cleanup-failed", None, b""
        )
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    try:
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
    except (OSError, ValueError):
        selector.close()
        reaped = _kill_group(process)
        process.stdout.close()
        process.stderr.close()
        return _CommandResult(
            "unsupported-platform" if reaped else "cleanup-failed", None, b""
        )
    deadline = time.monotonic() + _TIMEOUT
    reason = "exited"
    cleanup_attempted = False
    cleanup_succeeded = False
    result: _CommandResult | None = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = "timeout"
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = streams[key.fileobj]
                if len(target) + len(chunk) > _MAX_OUTPUT:
                    reason = "output-limit"
                    break
                target.extend(chunk)
            if reason != "exited":
                break
        if reason == "exited":
            child_exited = _wait_child_exit(process, deadline)
            if child_exited is False:
                reason = "timeout"
            elif child_exited is None:
                reason = "unsupported-platform"
        # 无论 CLI 结果如何都清理本组，防止遗留关闭管道的子孙。
        cleanup_attempted = True
        cleanup_succeeded = _kill_group(process)
        # 终止后短暂排空并关闭读取端；不等待未受控的继承管道。
        drain_deadline = time.monotonic() + 0.5
        while selector.get_map() and time.monotonic() < drain_deadline:
            for key, _ in selector.select(0.05):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                except (BlockingIOError, OSError):
                    chunk = b""
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                else:
                    target = streams[key.fileobj]
                    if len(target) + len(chunk) <= _MAX_OUTPUT:
                        target.extend(chunk)
        for stream in list(selector.get_map().values()):
            selector.unregister(stream.fileobj)
            stream.fileobj.close()
        if reason != "exited":
            result = _CommandResult(reason, process.returncode, b"")
        else:
            result = _CommandResult(
                "exited", process.returncode, bytes(streams[process.stdout])
            )
    finally:
        try:
            if not cleanup_attempted:
                cleanup_succeeded = _kill_group(process)
        finally:
            selector.close()
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()
    if not cleanup_succeeded:
        return _CommandResult("cleanup-failed", process.returncode, b"")
    assert result is not None
    return result


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


def _context_endpoint(
    docker: str, context: str | None, env: Mapping[str, str]
) -> tuple[str | None, str | None]:
    argv = [docker, "context", "inspect"]
    if context is not None:
        argv.append(context)
    argv.extend(("--format", _CONTEXT_FORMAT))
    result = _run_bounded(argv, env)
    if result.status == "cleanup-failed":
        return None, "docker-process-cleanup-failed"
    if result.status == "missing-cli":
        return None, "docker-cli-missing"
    if result.status in {"timeout", "output-limit", "unsupported-platform"}:
        return None, "docker-context-unavailable"
    if result.status != "exited" or result.returncode != 0:
        return None, "docker-context-unavailable"
    try:
        endpoint = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "docker-context-invalid"
    parsed = _socket_endpoint(endpoint) if isinstance(endpoint, str) else None
    return (parsed[0], None) if parsed else (None, "docker-context-remote")


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
    repo_root: str | Path,
    target_platform: str,
    environment: Mapping[str, str],
) -> PreflightResult:
    """发现并绑定本机 Docker daemon。

    仅执行只读 context、info 与 compose version。
    """
    del repo_root  # 此窄只读预检不需要访问仓库或用户配置文件。
    if not isinstance(target_platform, str) or target_platform not in _TARGETS:
        return _result("FAIL", "invalid-target-platform")
    values, error = _safe_environment(environment)
    if error:
        return _result("FAIL", error)
    assert values is not None
    command_env = _command_environment(values)
    docker = shutil.which("docker", path=command_env.get("PATH", ""))
    if not docker:
        return _result("BLOCKED", "docker-cli-missing")
    if values.get("DOCKER_HOST"):
        endpoint = values["DOCKER_HOST"]
    else:
        context = values.get("DOCKER_CONTEXT") or None
        if context is not None and not _CONTEXT.fullmatch(context):
            return _result("FAIL", "invalid-context")
        endpoint, context_error = _context_endpoint(docker, context, command_env)
        if context_error:
            status = (
                "FAIL"
                if context_error
                in {
                    "docker-context-invalid",
                    "docker-context-remote",
                    "docker-process-cleanup-failed",
                }
                else "BLOCKED"
            )
            return _result(status, context_error)
        assert endpoint is not None
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
    if fields["Architecture"] not in _TARGETS[target_platform]:
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
            target_platform=target_platform,
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
    if fields["OSType"] != "linux" or fields["Architecture"] not in _TARGETS.get(
        binding.target_platform, set()
    ):
        return _result("FAIL", "daemon-platform-changed")
    return _result("PASS", "docker-readonly-recheck-succeeded", binding)
