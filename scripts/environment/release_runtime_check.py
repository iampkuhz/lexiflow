"""消费Verify冻结来源并执行合成发布runtime生命周期验收。"""

from __future__ import annotations

import contextvars
import ctypes
import hashlib
import json
import os
import shutil
import signal
import threading
import time
import subprocess
import sys
import tempfile
import struct
from pathlib import Path
from typing import Any, Callable, Mapping

from scripts.environment import release_docker_preflight
from scripts.verification.kernel import fingerprint_json, snapshot_check_inputs
from scripts.verification.release_source_bridge import (
    BridgeError,
    consume_verify_snapshot,
)

MAX_ENVELOPE = 1_048_576
CHECK_IDS = frozenset(
    {"eng.release.lifecycle-runtime", "eng.release.lifecycle-runtime-on-change"}
)
ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "check_id",
        "check_config_fingerprint",
        "effective_check",
        "snapshot",
    }
)
SNAPSHOT_FIELDS = frozenset({"fingerprint", "files", "missing"})
_PROGRESS: contextvars.ContextVar[list[int] | None] = contextvars.ContextVar(
    "release_runtime_progress", default=None
)
_ACTIVE_PROCESS_GROUPS: set[int] = set()
_SPAWNING_CHILD = False
_TERMINATION_REQUESTED = False
_TERMINATION_HANDLER_INSTALLED = False
_DARWIN_LIBPROC: Any = None
RELEASE_PLATFORMS = ("linux/arm64",)


def _require_supported_host() -> None:
    """只允许当前合同支持的 Apple Silicon macOS 宿主。"""
    if sys.platform != "darwin" or os.uname().machine not in {"arm64", "aarch64"}:
        raise ConsumerError("BLOCKED", "release-host-platform-unsupported")


def _darwin_group_only_zombies(pgid: int) -> bool:
    """仅在 Darwin 的 EPERM 后，核实固定进程组内已无可运行成员。"""
    lib = _DARWIN_LIBPROC
    if lib is None:
        return False
    # XNU may report a member as SRUN while exit is in progress. Only a later
    # complete group enumeration proving all members SZOMB can clear EPERM.
    # The unreaped leader pins PGID throughout this bounded observation.
    deadline = time.monotonic() + 0.05
    for attempt in range(12):
        capacity = lib.proc_listpgrppids(pgid, None, 0)
        if capacity <= 0:
            return False
        # libproc returns a PID count; reserve slack but reject a filled buffer.
        capacity += 8
        pids = (ctypes.c_int * capacity)()
        count = lib.proc_listpgrppids(pgid, pids, ctypes.sizeof(pids))
        if count <= 0 or count >= capacity or pgid not in pids[:count]:
            return False
        for pid in pids[:count]:
            info = ctypes.create_string_buffer(256)
            size = lib.proc_pidinfo(pid, 13, 1, info, ctypes.sizeof(info))
            if size < 16:
                break
            actual_pid, _parent_pid, actual_pgid, state = struct.unpack_from(
                "=IIII", info
            )
            if actual_pid != pid or actual_pgid != pgid or state != 5:
                break
        else:
            return True
        remaining = deadline - time.monotonic()
        if attempt == 11 or remaining <= 0:
            break
        time.sleep(min(0.005, remaining))
    return False


def _wait_status_unreaped(pid: int) -> Any:
    """用 WNOWAIT 查询子进程状态，信号归属结束前不回收其 PID。"""
    required = ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED")
    if any(getattr(os, name, None) is None for name in required):
        raise ConsumerError("BLOCKED", "release-process-lifetime-unsupported")
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except (OSError, ValueError):
        raise ConsumerError("FAIL", "release-process-status-unavailable") from None


def _require_unreaped_wait_support() -> None:
    """在创建外部进程前确认宿主支持安全的未回收状态查询。"""
    required = ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED")
    if any(getattr(os, name, None) is None for name in required):
        raise ConsumerError("BLOCKED", "release-process-lifetime-unsupported")
    if sys.platform == "darwin":
        global _DARWIN_LIBPROC
        if _DARWIN_LIBPROC is None:
            try:
                lib = ctypes.CDLL("/usr/lib/libproc.dylib")
                lib.proc_listpgrppids.argtypes = [
                    ctypes.c_int,
                    ctypes.c_void_p,
                    ctypes.c_int,
                ]
                lib.proc_listpgrppids.restype = ctypes.c_int
                lib.proc_pidinfo.argtypes = [
                    ctypes.c_int,
                    ctypes.c_int,
                    ctypes.c_uint64,
                    ctypes.c_void_p,
                    ctypes.c_int,
                ]
                lib.proc_pidinfo.restype = ctypes.c_int
            except (OSError, AttributeError):
                raise ConsumerError(
                    "BLOCKED", "release-process-lifetime-unsupported"
                ) from None
            _DARWIN_LIBPROC = lib


def _signal_process_group(pgid: int, sig: int) -> None:
    """仅向本进程登记的专属子进程组发送信号。"""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # XNU excludes zombies from killpg's iteration and returns EPERM when
        # no signalable member remains. Never mask a live unsignalable member.
        if not (sys.platform == "darwin" and _darwin_group_only_zombies(pgid)):
            raise ConsumerError("FAIL", "release-process-group-signal-denied") from None


def _terminate_registered_groups() -> None:
    """快速终止所有本次consumer启动并登记的进程组。"""
    for pgid in tuple(_ACTIVE_PROCESS_GROUPS):
        _signal_process_group(pgid, signal.SIGTERM)
        _signal_process_group(pgid, signal.SIGKILL)


def _consumer_termination_handler(signum: int, _frame: Any) -> None:
    """将父级TERM转发到子进程组；spawn窗口内登记后再终止。"""
    global _TERMINATION_REQUESTED
    if _SPAWNING_CHILD:
        _TERMINATION_REQUESTED = True
        return
    _terminate_registered_groups()
    raise SystemExit(128 + signum)


def _install_termination_handler() -> None:
    """在主线程安装进程级TERM转发器。"""
    global _TERMINATION_HANDLER_INSTALLED
    if _TERMINATION_HANDLER_INSTALLED:
        return
    if threading.current_thread() is not threading.main_thread():
        raise ConsumerError("FAIL", "release-process-control-unavailable")
    signal.signal(signal.SIGTERM, _consumer_termination_handler)
    _TERMINATION_HANDLER_INSTALLED = True


def mark_completed() -> None:
    """记录一个真实成功完成的runtime命令阶段。"""
    progress = _PROGRESS.get()
    if progress is not None:
        progress[0] += 1


class ConsumerError(RuntimeError):
    """固定状态和安全错误码。"""

    def __init__(self, status: str, reason: str) -> None:
        self.status, self.reason = status, reason
        progress = _PROGRESS.get()
        self.checks_run = progress[0] if progress is not None else 0
        super().__init__(f"{status}/{reason}")


def _reject_constant(value: str) -> None:
    """拒绝JSON标准之外的NaN与Infinity。"""
    raise ValueError(value)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate-key")
        result[key] = value
    return result


def _read_envelope(
    stream: Any, *, check_ids: frozenset[str] = CHECK_IDS
) -> dict[str, Any]:
    raw = stream.buffer.read(MAX_ENVELOPE + 1)
    if not raw:
        raise ConsumerError("BLOCKED", "verify-context-unavailable")
    if len(raw) > MAX_ENVELOPE:
        raise ConsumerError("FAIL", "verify-context-invalid")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ConsumerError("FAIL", "verify-context-invalid") from None
    if (
        not isinstance(value, dict)
        or set(value) != ENVELOPE_FIELDS
        or value.get("schema_version") != "lexiflow.verify-child-input.v1"
        or type(value.get("run_id")) is not str
        or not value["run_id"].strip()
        or value.get("check_id") not in check_ids
        or type(value.get("check_config_fingerprint")) is not str
        or not isinstance(value.get("effective_check"), dict)
        or not isinstance(value.get("snapshot"), dict)
        or set(value["snapshot"]) != SNAPSHOT_FIELDS
        or value["effective_check"].get("check_id") != value["check_id"]
        or fingerprint_json(value["effective_check"])
        != value["check_config_fingerprint"]
    ):
        raise ConsumerError("FAIL", "verify-context-invalid")
    if value["snapshot"].get("missing"):
        raise ConsumerError("FAIL", "verify-snapshot-mismatch")
    return value


def _check_environment(repo: Path, environ: Mapping[str, str]) -> list[str]:
    missing = []
    for executable in ("git", "node", "npm", "docker"):
        if not shutil.which(executable, path=environ.get("PATH")):
            missing.append(executable)
    java_home = environ.get("JAVA_HOME", "")
    java = (
        Path(java_home) / "bin/java"
        if java_home
        else Path(shutil.which("java", path=environ.get("PATH")) or "/nonexistent")
    )
    if not java.is_file():
        missing.append("java-25")
    else:
        try:
            _stdout, version_bytes = _run_bounded(
                [str(java), "-version"],
                repo,
                {"PATH": environ.get("PATH", "")},
                5,
                limit=4096,
            )
            version = version_bytes.decode("utf-8", "replace")
            if 'version "25' not in version:
                missing.append("java-25")
        except (OSError, subprocess.SubprocessError):
            missing.append("java-25")
    if not (repo / "backend/gradlew").is_file():
        missing.append("gradle-wrapper")
    for name in ("LEXIFLOW_RELEASE_GRADLE_CACHE", "LEXIFLOW_RELEASE_NPM_CACHE"):
        value = environ.get(name, "")
        if not value or not Path(value).is_absolute() or not Path(value).is_dir():
            missing.append(name.lower().replace("_", "-"))
    if not environ.get("LEXIFLOW_POSTGRES_TEST_JDBC_URL", "").strip():
        missing.append("isolated-postgres-test-jdbc-url")
    return missing


def _bindings(repo: Path, env: Mapping[str, str]) -> tuple[Any, ...]:
    result = []
    _require_supported_host()
    for platform in RELEASE_PLATFORMS:
        key = "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST"
        endpoint = env.get(key, "").strip()
        if not endpoint:
            raise ConsumerError("BLOCKED", f"missing-{key.lower().replace('_', '-')}")
        if not endpoint.startswith("unix://"):
            raise ConsumerError("FAIL", "release-docker-endpoint-invalid")
        info = release_docker_preflight.preflight(
            repo, platform, {"DOCKER_HOST": endpoint, "PATH": env.get("PATH", "")}
        )
        if info.status != "PASS" or info.binding is None:
            raise ConsumerError(info.status, info.reason)
        result.append(info.binding)
    if len({binding.target_platform for binding in result}) != len(result):
        raise ConsumerError("FAIL", "release-docker-bindings-not-distinct")
    return tuple(result)


def run_consumer(
    repo_root: str | Path,
    envelope: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    bridge: Callable[..., dict[str, Any]] = consume_verify_snapshot,
    run_pipeline: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """消费原始Verify快照并串联构件、候选与安装生命周期。"""
    _install_termination_handler()
    from scripts.environment.release_runtime_artifacts import produce_candidates
    from scripts.environment.release_runtime_lifecycle import execute_lifecycle

    repo, env = Path(repo_root).resolve(), (os.environ if environ is None else environ)
    check_id = envelope["check_id"]
    context = {
        "run_id": envelope["run_id"],
        "check_id": check_id,
        "check_config_fingerprint": envelope["check_config_fingerprint"],
    }
    progress = [0]
    progress_token = _PROGRESS.set(progress)
    scratch_path = Path(tempfile.mkdtemp(prefix="lexiflow-release-runtime-")).resolve()
    source_parent = scratch_path / "source-parent"
    source_parent.mkdir(mode=0o700)
    try:
        fixture = bridge(
            repo,
            envelope["effective_check"],
            context,
            envelope["snapshot"],
            source_parent,
        )
    except BridgeError as exc:
        try:
            shutil.rmtree(scratch_path)
        except OSError:
            _PROGRESS.reset(progress_token)
            raise ConsumerError("FAIL", "release-scratch-cleanup-failed") from None
        error = ConsumerError(exc.status, exc.code)
        _PROGRESS.reset(progress_token)
        raise error from None
    try:
        fixture_root = Path(fixture["fixture"])
        current = snapshot_check_inputs(repo, envelope["effective_check"])
        if json.dumps(current, sort_keys=True, separators=(",", ":")) != json.dumps(
            envelope["snapshot"], sort_keys=True, separators=(",", ":")
        ):
            raise ConsumerError("FAIL", "verify-snapshot-mismatch")
        missing = _check_environment(fixture_root, env)
        if missing:
            raise ConsumerError("BLOCKED", "missing-environment")
        # Native endpoints are checked before any build or Docker side effect.
        bindings = _bindings(fixture_root, env)
        evidence = (
            run_pipeline(fixture_root, scratch_path, bindings, env)
            if run_pipeline
            else produce_candidates(fixture_root, scratch_path, env, bindings)
        )
        if not isinstance(evidence, dict) or evidence.get("status", "PASS") != "PASS":
            raise ConsumerError("FAIL", "release-runtime-evidence-invalid")
        lifecycle = execute_lifecycle(
            fixture_root, scratch_path, evidence, bindings, env
        )
        result = {
            "status": "PASS",
            "run_id": context["run_id"],
            "check_id": check_id,
            "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
            "evidence": evidence,
            "lifecycle": lifecycle,
        }
        if progress[0] < 1:
            raise ConsumerError("FAIL", "release-runtime-no-checks-executed")
        result["checks_run"] = progress[0]
        result["failures"] = 0
        result["errors"] = 0
        result["skipped"] = 0
        result["reason"] = ""
        shutil.rmtree(scratch_path)
        _PROGRESS.reset(progress_token)
        return result
    except ConsumerError:
        # Leave the task-owned scratch intact for diagnosis; never recursively clean daemon resources here.
        _PROGRESS.reset(progress_token)
        raise
    except Exception:
        error = ConsumerError("FAIL", "release-runtime-consumer-failed")
        _PROGRESS.reset(progress_token)
        raise error from None


def _run_bounded(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    limit: int = 1_048_576,
    *,
    input_stream: Any = None,
) -> tuple[bytes, bytes]:
    global _SPAWNING_CHILD, _TERMINATION_REQUESTED
    _install_termination_handler()
    _require_unreaped_wait_support()
    readers: list[threading.Thread] = []
    _SPAWNING_CHILD = True
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL if input_stream is None else input_stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        _SPAWNING_CHILD = False
        if _TERMINATION_REQUESTED:
            _TERMINATION_REQUESTED = False
            _terminate_registered_groups()
            raise SystemExit(128 + signal.SIGTERM)
        raise ConsumerError("BLOCKED", "release-command-unavailable") from None
    except BaseException:
        _SPAWNING_CHILD = False
        if _TERMINATION_REQUESTED:
            _TERMINATION_REQUESTED = False
            _terminate_registered_groups()
            raise SystemExit(128 + signal.SIGTERM)
        raise
    try:
        _ACTIVE_PROCESS_GROUPS.add(process.pid)
        _SPAWNING_CHILD = False
        if _TERMINATION_REQUESTED:
            _TERMINATION_REQUESTED = False
            _terminate_registered_groups()
            raise SystemExit(128 + signal.SIGTERM)
        captures = [bytearray(), bytearray()]
        overflow = threading.Event()

        def drain(stream: Any, index: int) -> None:
            while chunk := stream.read(8192):
                if len(captures[index]) + len(chunk) > limit:
                    overflow.set()
                else:
                    captures[index].extend(chunk)

        readers = [
            threading.Thread(target=drain, args=(process.stdout, 0), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, 1), daemon=True),
        ]
        for reader in readers:
            reader.start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not overflow.is_set():
            status = _wait_status_unreaped(process.pid)
            if status is not None:
                break
            time.sleep(0.02)
        status = _wait_status_unreaped(process.pid)
        child_exited = status is not None
        timed_out = (
            not child_exited and not overflow.is_set() and time.monotonic() >= deadline
        )
        if not child_exited:
            _signal_process_group(process.pid, signal.SIGTERM)
            term_deadline = time.monotonic() + 0.5
            while time.monotonic() < term_deadline:
                if _wait_status_unreaped(process.pid) is not None:
                    break
                time.sleep(0.01)
            if _wait_status_unreaped(process.pid) is None:
                _signal_process_group(process.pid, signal.SIGKILL)
        for reader in readers:
            reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            raise ConsumerError("FAIL", "release-command-cleanup-failed")
        if overflow.is_set():
            raise ConsumerError("FAIL", "release-command-output-limit")
        if timed_out:
            raise ConsumerError("FAIL", "release-command-timeout")
        if status is None or status.si_code != os.CLD_EXITED or status.si_status != 0:
            raise ConsumerError("FAIL", "release-command-failed")
        mark_completed()
        return bytes(captures[0]), bytes(captures[1])
    finally:
        # 在 PID 仍由未回收的直接子进程占有期间清理整组，避免复用后的 PGID 误伤。
        _SPAWNING_CHILD = True
        try:
            cleanup_error: ConsumerError | None = None
            try:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        _signal_process_group(process.pid, sig)
                    except ConsumerError as exc:
                        cleanup_error = exc
                    except OSError:
                        if cleanup_error is None:
                            cleanup_error = ConsumerError(
                                "FAIL", "release-command-cleanup-failed"
                            )
            finally:
                _ACTIVE_PROCESS_GROUPS.discard(process.pid)
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=0.5)
                if cleanup_error is None:
                    cleanup_error = ConsumerError(
                        "FAIL", "release-command-cleanup-failed"
                    )
            finally:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
                for reader in readers:
                    if reader.ident is not None:
                        reader.join(timeout=0.25)
            if cleanup_error is not None:
                raise cleanup_error
        finally:
            _SPAWNING_CHILD = False
            if _TERMINATION_REQUESTED:
                _TERMINATION_REQUESTED = False
                raise SystemExit(128 + signal.SIGTERM)


def _run_json_command(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    output_parent: Path | None = None,
) -> dict[str, Any]:
    stdout, _stderr = _run_bounded(argv, cwd, env, timeout)
    try:
        lines = stdout.decode("utf-8").splitlines()
        if len(lines) != 1:
            raise ValueError
        result = json.loads(lines[0])
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ConsumerError("FAIL", "release-command-result-invalid") from None
    expected_fields = {"status", "scope", "operation", "candidateDirectory"}
    if (
        not isinstance(result, dict)
        or set(result) != expected_fields
        or result.get("status") != "PASS"
        or result.get("scope") != "candidate-only"
        or result.get("operation") not in {"images", "assemble"}
        or not isinstance(result.get("candidateDirectory"), str)
    ):
        raise ConsumerError("FAIL", "release-command-result-invalid")
    directory = Path(result["candidateDirectory"])
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ConsumerError("FAIL", "release-command-result-invalid")
    if (
        output_parent is not None
        and directory.parent.resolve() != output_parent.resolve()
    ):
        raise ConsumerError("FAIL", "release-command-result-invalid")
    marker = directory / "candidate.json"
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 2_000_000:
        raise ConsumerError("FAIL", "release-command-result-invalid")
    data = marker.read_bytes()
    try:
        candidate = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ConsumerError("FAIL", "release-command-result-invalid") from None
    expected_kind = (
        "lexiflow-image-candidate"
        if result["operation"] == "images"
        else "lexiflow-release-candidate"
    )
    if not isinstance(candidate, dict) or candidate.get("kind") != expected_kind:
        raise ConsumerError("FAIL", "release-command-result-invalid")
    result["candidateSha256"] = hashlib.sha256(data).hexdigest()
    result["candidate"] = candidate
    return result


def main() -> int:
    """读取匿名pipe envelope并输出带correlation的结果。"""
    envelope: dict[str, Any] | None = None
    try:
        _install_termination_handler()
        root = Path(__file__).resolve().parents[2]
        envelope = _read_envelope(sys.stdin)
        result = run_consumer(root, envelope)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except ConsumerError as exc:
        report: dict[str, Any] = {"status": exc.status, "reason": exc.reason}
        if envelope is not None:
            report.update(
                {
                    "run_id": envelope["run_id"],
                    "check_id": envelope["check_id"],
                    "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
                }
            )
        report.update(
            {
                "checks_run": exc.checks_run,
                "failures": 1 if exc.status == "FAIL" else 0,
                "errors": 0,
                "skipped": 0,
            }
        )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        report = {
            "status": "FAIL",
            "reason": "release-runtime-consumer-failed",
            "checks_run": 0,
            "failures": 1,
            "errors": 0,
            "skipped": 0,
        }
        if envelope is not None:
            report.update(
                {
                    "run_id": envelope["run_id"],
                    "check_id": envelope["check_id"],
                    "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
                }
            )
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
