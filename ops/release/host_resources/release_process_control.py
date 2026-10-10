"""发行专用 owned-process 监督，保留真实退出与完整清理事实。"""

from __future__ import annotations
import ctypes
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_ACTIVE_PROCESS_GROUPS: set[int] = set()
_SPAWNING_CHILD = False
_TERMINATION_REQUESTED = False
_TERMINATION_HANDLER_INSTALLED = False
_DARWIN_LIBPROC: Any = None


class ProcessError(RuntimeError):
    """进程监督失败，不代表 daemon 不可用。"""

    def __init__(self, status: str, reason: str) -> None:
        self.status, self.reason = status, reason
        super().__init__(f"{status}/{reason}")


@dataclass(frozen=True)
class ProcessFacts:
    """有界输出、真实退出状态及已完成清理的事实。"""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    overflow: bool
    cleanup_succeeded: bool


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
        raise ProcessError("BLOCKED", "release-process-lifetime-unsupported")
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except (OSError, ValueError):
        raise ProcessError("FAIL", "release-process-status-unavailable") from None


def _require_unreaped_wait_support() -> None:
    """在创建外部进程前确认宿主支持安全的未回收状态查询。"""
    required = ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED")
    if any(getattr(os, name, None) is None for name in required):
        raise ProcessError("BLOCKED", "release-process-lifetime-unsupported")
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
                raise ProcessError(
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
            raise ProcessError("FAIL", "release-process-group-signal-denied") from None


def _group_absent_after_reap(pgid: int) -> bool:
    """回收后只读观察组消失；未知/权限错误不能当作清理成功。"""
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            pass
        time.sleep(0.02)
    return False


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
        raise ProcessError("FAIL", "release-process-control-unavailable")
    signal.signal(signal.SIGTERM, _consumer_termination_handler)
    _TERMINATION_HANDLER_INSTALLED = True


def run_owned(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    limit: int = 1_048_576,
    *,
    input_stream: Any = None,
) -> ProcessFacts:
    """有界监督发行专属进程组，回收前完成全部归属信号并返回退出事实。"""
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
        raise ProcessError("BLOCKED", "release-command-unavailable") from None
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
        reader_failed = threading.Event()

        def drain(stream: Any, index: int) -> None:
            try:
                while chunk := stream.read(8192):
                    if len(captures[index]) + len(chunk) > limit:
                        overflow.set()
                    else:
                        captures[index].extend(chunk)
            except (OSError, ValueError):
                reader_failed.set()

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
        if reader_failed.is_set() or any(reader.is_alive() for reader in readers):
            raise ProcessError("FAIL", "release-command-cleanup-failed")
        actual = _wait_status_unreaped(process.pid)
        returncode = (
            None
            if actual is None
            else (
                actual.si_status
                if actual.si_code == os.CLD_EXITED
                else -actual.si_status
            )
        )
        return ProcessFacts(
            returncode,
            bytes(captures[0]),
            bytes(captures[1]),
            timed_out,
            overflow.is_set(),
            True,
        )
    finally:
        # 在 PID 仍由未回收的直接子进程占有期间清理整组，避免复用后的 PGID 误伤。
        _SPAWNING_CHILD = True
        try:
            cleanup_error: ProcessError | None = None
            try:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        _signal_process_group(process.pid, sig)
                    except ProcessError as exc:
                        cleanup_error = exc
                    except OSError:
                        if cleanup_error is None:
                            cleanup_error = ProcessError(
                                "FAIL", "release-command-cleanup-failed"
                            )
            finally:
                _ACTIVE_PROCESS_GROUPS.discard(process.pid)
            try:
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=0.5)
                    cleanup_error = ProcessError(
                        "FAIL", "release-command-cleanup-failed"
                    )
            except (OSError, subprocess.SubprocessError):
                cleanup_error = ProcessError("FAIL", "release-command-cleanup-failed")
            finally:
                for reader in readers:
                    if reader.ident is not None:
                        reader.join(timeout=0.25)
                # 不在活 reader 持有的 IO 锁上无限等待；此种状态始终 FAIL。
                if not any(reader.is_alive() for reader in readers):
                    for stream in (process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
            if any(reader.is_alive() for reader in readers):
                cleanup_error = ProcessError("FAIL", "release-command-cleanup-failed")
            if cleanup_error is None and (
                process.returncode is None or not _group_absent_after_reap(process.pid)
            ):
                cleanup_error = ProcessError("FAIL", "release-command-cleanup-failed")
            if cleanup_error is not None:
                raise cleanup_error
        finally:
            _SPAWNING_CHILD = False
            if _TERMINATION_REQUESTED:
                _TERMINATION_REQUESTED = False
                raise SystemExit(128 + signal.SIGTERM)


def run_bounded(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    limit: int = 1_048_576,
    *,
    input_stream: Any = None,
) -> tuple[bytes, bytes]:
    """消费 owned 进程事实，只有真实零退出且完整清理才返回输出。"""
    facts = run_owned(argv, cwd, env, timeout, limit, input_stream=input_stream)
    if facts.overflow:
        raise ProcessError("FAIL", "release-command-output-limit")
    if facts.timed_out:
        raise ProcessError("FAIL", "release-command-timeout")
    if facts.returncode != 0 or not facts.cleanup_succeeded:
        raise ProcessError("FAIL", "release-command-failed")
    return facts.stdout, facts.stderr
