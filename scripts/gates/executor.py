"""Frozen-plan Gate checker executor with typed outcome adapters.

Consumes a validated ``lexiflow.gate-plan.v1`` plan and returns an in-memory
``lexiflow.gate-check-outcome.v1`` result. Never discovers checks, commands,
cwd, timeout, subjects, owners, or inputs; never writes receipts or creates
run identity. Lifecycle and persistence belong to LF-TSK-QLT-0010.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Callable

OUTCOME_SCHEMA = "lexiflow.gate-check-outcome.v1"
CHECK_OUTCOME_SCHEMA = "lexiflow.check-outcome.v1"
PLAN_SCHEMA = "lexiflow.gate-plan.v1"

DEFAULT_STDOUT_LIMIT = 2 * 1024 * 1024
DEFAULT_STDERR_LIMIT = 1 * 1024 * 1024
PROCESS_TERM_GRACE = 0.4

_SECRET_PREFIXES = (
    "CODEX", "QODER", "CLAUDE", "ANTHROPIC", "OPENAI",
    "LEXIFLOW_GATE_",
)
_SECRET_SUBSTRINGS = (
    "API_KEY", "API_TOKEN", "API_SECRET", "SECRET_KEY",
    "CREDENTIAL", "PRIVATE_KEY", "ACCESS_TOKEN", "AUTH_TOKEN",
)

_ALLOWED_ENV_KEYS = frozenset({
    "USER", "LOGNAME",
    "LANG", "LC_ALL", "LANGUAGE",
    "TERM", "TZ",
    "PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
    "TMPDIR", "TEMP", "TMP",
})

_PLANNING_COMMAND_ID = "qlt.planning.validate.v1"
_UNITTEST_COMMAND_IDS = frozenset({
    "qlt.evidence.validate.v1",
    "qlt.evidence.validate.v3",
    "qlt.issuer.validate.v1",
    "qlt.issuer.validate.v3",
    "qlt.planner.validate.v1",
    "qlt.executor.validate.v1",
    "qlt.lifecycle.validate.v1",
    "qlt.independent-review.validate.v1",
    "qlt.hash-dag.validate.v1",
    "qlt.catalog-decision.validate.v1",
    "qlt.dispatch-preflight.validate.v1",
    "qlt.runner.validate.v1",
})
_TASK_CONTRACT_COMMAND_RE = re.compile(r"^g1\.[a-z]+\.\d{4}\.contract\.v1$")

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_TASK_ID_RE = re.compile(r"^LF-TSK-[A-Z]+-\d{4}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

RAN_TESTS_RE = re.compile(
    r"^Ran\s+(\d+)\s+tests?(?:\s+in\s+\d+(?:\.\d+)?s)?\s*$",
    re.MULTILINE,
)
OK_LINE_RE = re.compile(r"^OK\s*(\(.*\))?\s*$", re.MULTILINE)
FAILED_LINE_RE = re.compile(
    r"^FAILED\s*\(([^)]*)\)\s*$", re.MULTILINE
)

PLANNING_STATUS_RE = re.compile(
    r"^Planning validator:\s*(PASS|BLOCKED|FAIL)\s*$", re.MULTILINE
)
PLANNING_TASKS_RE = re.compile(
    r"^Tasks validated:\s*(\d+)\s*$", re.MULTILINE
)
PLANNING_CHECKS_RE = re.compile(
    r"^Checks executed:\s*(\d+)\s+\([^\r\n)]*\)\s*$", re.MULTILINE
)


class ExecutorError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class ExternalInterruption(BaseException):
    def __init__(self, signal_number: int | None = None) -> None:
        super().__init__(f"interrupted (signal {signal_number})" if signal_number else "interrupted")
        self.signal_number = signal_number


@dataclass(frozen=True)
class ExecutableBinding:
    locator: str
    sha256: str
    device: int
    inode: int
    size: int


@dataclass(frozen=True)
class ProcessRequest:
    argv: tuple[str, ...]
    cwd: str
    env: tuple[tuple[str, str], ...]
    timeout_seconds: int
    stdout_limit: int
    stderr_limit: int
    executable: ExecutableBinding


@dataclass(frozen=True)
class ProcessResult:
    return_code: int | None
    signal_number: int | None
    exit_reason: str
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    started_at: str
    finished_at: str
    duration_seconds: float
    child_pid: int | None
    timeout_seconds: int
    argv: tuple[str, ...]
    cwd: str
    capture_error: bool = False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_locator(locator: str) -> str:
    if not isinstance(locator, str) or not locator or _CONTROL_RE.search(locator):
        raise ExecutorError("invalid-consumed-input", f"unsafe locator: {locator!r}")
    if "\\" in locator or os.path.isabs(locator):
        raise ExecutorError("invalid-consumed-input", f"unsafe locator: {locator!r}")
    path = PurePosixPath(locator)
    if path.is_absolute() or path.as_posix() != locator:
        raise ExecutorError("invalid-consumed-input", f"unnormalized locator: {locator!r}")
    for part in path.parts:
        if part in {"", ".", ".."} or part.lower() == "latest":
            raise ExecutorError("invalid-consumed-input", f"unsafe segment: {locator!r}")
    return locator


def _safe_read_file(repo_root: str, locator: str) -> bytes:
    locator = _safe_locator(locator)
    root = os.path.abspath(repo_root)
    dflags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fflags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    dirs: list[int] = []
    file_fd: int | None = None
    try:
        current = os.open(os.path.sep, dflags)
        dirs.append(current)
        for part in (p for p in os.path.abspath(root).split(os.path.sep) if p):
            before = os.stat(part, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise ExecutorError("input-symlink", f"symlink root ancestor: {root}")
            if not stat.S_ISDIR(before.st_mode):
                raise ExecutorError("input-not-regular", f"non-directory root: {root}")
            child = os.open(part, dflags, dir_fd=current)
            after = os.fstat(child)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                os.close(child)
                raise ExecutorError("input-drift", "root ancestor changed while opening")
            os.close(current)
            current = child
        for part in locator.split("/")[:-1]:
            before = os.stat(part, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode):
                raise ExecutorError("input-symlink", f"symlink ancestor: {locator}")
            if not stat.S_ISDIR(before.st_mode):
                raise ExecutorError("input-not-regular", f"non-directory ancestor: {locator}")
            child = os.open(part, dflags, dir_fd=current)
            after = os.fstat(child)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                os.close(child)
                raise ExecutorError("input-drift", f"ancestor changed: {locator}")
            dirs.append(child)
            current = child
        leaf = locator.split("/")[-1]
        before = os.stat(leaf, dir_fd=current, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode):
            raise ExecutorError("input-symlink", f"symlink leaf: {locator}")
        if not stat.S_ISREG(before.st_mode):
            raise ExecutorError("input-not-regular", f"non-regular input: {locator}")
        file_fd = os.open(leaf, fflags, dir_fd=current)
        after = os.fstat(file_fd)
        if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ExecutorError("input-drift", f"input changed while opening: {locator}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except ExecutorError:
        raise
    except OSError as exc:
        raise ExecutorError("input-missing", f"cannot read {locator}: {exc}") from None
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError:
                pass
        for fd in reversed(dirs):
            try:
                os.close(fd)
            except OSError:
                pass


def _verify_consumed_inputs(repo_root: str, descriptors: list[dict]) -> list[dict]:
    results = []
    for desc in descriptors:
        locator = desc["locator"]
        expected = desc["sha256"]
        try:
            content = _safe_read_file(repo_root, locator)
            actual = sha256_bytes(content)
            if not hmac.compare_digest(actual, expected):
                results.append({"locator": locator, "expected_sha256": expected, "actual_sha256": actual, "status": "drift"})
            else:
                results.append({"locator": locator, "expected_sha256": expected, "actual_sha256": actual, "status": "verified"})
        except ExecutorError as exc:
            results.append({"locator": locator, "expected_sha256": expected, "actual_sha256": "", "status": exc.code})
    return results


def _all_verified(results: list[dict]) -> bool:
    return all(r["status"] == "verified" for r in results)


def _build_child_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key not in _ALLOWED_ENV_KEYS:
            continue
        if any(key.startswith(p) for p in _SECRET_PREFIXES):
            continue
        if any(s in key for s in _SECRET_SUBSTRINGS):
            continue
        env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("PYTHONHASHSEED", "0")
    return env


def _environment_fingerprint(env: dict[str, str]) -> str:
    return sha256_bytes(json.dumps(sorted(env.items()), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _read_executable_binding(locator: str) -> ExecutableBinding:
    if not isinstance(locator, str) or not os.path.isabs(locator) or os.path.realpath(locator) != locator:
        raise ExecutorError("invalid-executable", "executable locator must be an absolute canonical path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        before = os.stat(locator, follow_symlinks=False)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ExecutorError("invalid-executable", "executable must be a regular non-symlink file")
        if before.st_mode & 0o111 == 0:
            raise ExecutorError("invalid-executable", "executable has no execute bit")
        descriptor = os.open(locator, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ExecutorError("executable-drift", "executable changed while opening")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise ExecutorError("executable-drift", "executable changed while hashing")
        return ExecutableBinding(
            locator=locator,
            sha256=digest.hexdigest(),
            device=after.st_dev,
            inode=after.st_ino,
            size=after.st_size,
        )
    except ExecutorError:
        raise
    except OSError as exc:
        raise ExecutorError("invalid-executable", f"cannot bind executable: {exc}") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _resolve_registered_executable(declared: str) -> ExecutableBinding:
    if declared != "python3":
        raise ExecutorError("invalid-executable", f"unsupported registered executable: {declared!r}")
    return _read_executable_binding(os.path.realpath(sys.executable))


def _verify_executable_binding(binding: ExecutableBinding) -> bool:
    try:
        current = _read_executable_binding(binding.locator)
        return hmac.compare_digest(current.sha256, binding.sha256) and (
            current.device, current.inode, current.size
        ) == (binding.device, binding.inode, binding.size)
    except ExecutorError:
        return False


def _resolve_cwd(repo_root: str, cwd: str) -> str:
    if not isinstance(cwd, str) or not cwd or _CONTROL_RE.search(cwd):
        raise ExecutorError("invalid-cwd", "cwd must be a non-empty string")
    root = os.path.abspath(repo_root)
    if "\\" in cwd or os.path.isabs(cwd) or cwd.endswith("/") or "//" in cwd:
        raise ExecutorError("invalid-cwd", f"cwd is not normalized repo-relative POSIX: {cwd}")
    if cwd == ".":
        relative_parts: list[str] = []
    else:
        path = PurePosixPath(cwd)
        if path.as_posix() != cwd or any(part in {"", ".", ".."} for part in path.parts):
            raise ExecutorError("invalid-cwd", f"cwd escapes repo root: {cwd}")
        relative_parts = list(path.parts)

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptors: list[int] = []
    try:
        current = os.open(os.path.sep, flags)
        descriptors.append(current)
        for part in (piece for piece in root.split(os.path.sep) if piece):
            before = os.stat(part, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ExecutorError("invalid-cwd", f"unsafe repository root ancestor: {repo_root}")
            child = os.open(part, flags, dir_fd=current)
            after = os.fstat(child)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                os.close(child)
                raise ExecutorError("invalid-cwd", "repository root changed while resolving cwd")
            descriptors.append(child)
            current = child
        for part in relative_parts:
            before = os.stat(part, dir_fd=current, follow_symlinks=False)
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
                raise ExecutorError("invalid-cwd", f"cwd contains a symlink or non-directory: {cwd}")
            child = os.open(part, flags, dir_fd=current)
            after = os.fstat(child)
            if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                os.close(child)
                raise ExecutorError("invalid-cwd", f"cwd changed while resolving: {cwd}")
            descriptors.append(child)
            current = child
    except ExecutorError:
        raise
    except OSError as exc:
        raise ExecutorError("invalid-cwd", f"cannot safely resolve cwd: {exc}") from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return root if not relative_parts else os.path.join(root, *relative_parts)


def _terminate_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.monotonic() + PROCESS_TERM_GRACE
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        time.sleep(0.02)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def default_process_runner(request: ProcessRequest) -> ProcessResult:
    argv = list(request.argv)
    executed_argv = [request.executable.locator, *argv[1:]]
    cwd = request.cwd
    env = dict(request.env)
    timeout = request.timeout_seconds
    stdout_limit = request.stdout_limit
    stderr_limit = request.stderr_limit

    started_at = _utc_now()
    started = time.monotonic()
    child_pid: int | None = None
    return_code: int | None = None
    signal_number: int | None = None
    exit_reason = "SPAWN_ERROR"
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    stdout_truncated = False
    stderr_truncated = False
    stdout_eof = threading.Event()
    stderr_eof = threading.Event()
    overflow_event = threading.Event()
    stdout_overflow = threading.Event()
    stderr_overflow = threading.Event()
    capture_error = threading.Event()

    if (
        not argv
        or any(not v or "\x00" in v for v in argv)
        or not _verify_executable_binding(request.executable)
    ):
        finished_at = _utc_now()
        return ProcessResult(
            return_code=None, signal_number=None, exit_reason="SPAWN_ERROR",
            stdout=b"", stderr=b"", stdout_truncated=False, stderr_truncated=False,
            started_at=started_at, finished_at=finished_at,
            duration_seconds=round(time.monotonic() - started, 6),
            child_pid=None, timeout_seconds=timeout,
            argv=tuple(argv), cwd=cwd,
        )

    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            executed_argv, cwd=cwd, env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, shell=False,
        )
        child_pid = process.pid

        def _read_stream(stream, buf, limit, eof_event, stream_overflow):
            try:
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    if len(buf) + len(chunk) > limit:
                        remaining = limit - len(buf)
                        if remaining > 0:
                            buf.extend(chunk[:remaining])
                        stream_overflow.set()
                        overflow_event.set()
                        return
                    buf.extend(chunk)
            except (OSError, ValueError):
                capture_error.set()
            finally:
                eof_event.set()

        stdout_thread = threading.Thread(
            target=_read_stream,
            args=(process.stdout, stdout_buf, stdout_limit, stdout_eof, stdout_overflow),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_read_stream,
            args=(process.stderr, stderr_buf, stderr_limit, stderr_eof, stderr_overflow),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        try:
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, timeout)
                try:
                    return_code = process.wait(timeout=min(remaining, 0.1))
                    break
                except subprocess.TimeoutExpired:
                    if overflow_event.is_set() or capture_error.is_set():
                        break
                    continue
        except subprocess.TimeoutExpired:
            _terminate_process_group(process.pid)
            process.wait()
            return_code = process.returncode
            signal_number = -return_code if return_code is not None and return_code < 0 else None
            exit_reason = "TIMEOUT"
            stdout_thread.join(timeout=2.0)
            stderr_thread.join(timeout=2.0)
            finished_at = _utc_now()
            return ProcessResult(
                return_code=return_code, signal_number=signal_number, exit_reason="TIMEOUT",
                stdout=bytes(stdout_buf), stderr=bytes(stderr_buf),
                stdout_truncated=stdout_overflow.is_set(),
                stderr_truncated=stderr_overflow.is_set(),
                started_at=started_at, finished_at=finished_at,
                duration_seconds=round(time.monotonic() - started, 6),
                child_pid=child_pid, timeout_seconds=timeout,
                argv=tuple(argv), cwd=cwd,
            )

        if overflow_event.is_set():
            _terminate_process_group(process.pid)
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return_code = process.returncode
            signal_number = -return_code if return_code is not None and return_code < 0 else None
            stdout_thread.join(timeout=2.0)
            stderr_thread.join(timeout=2.0)
            exit_reason = "OUTPUT_LIMIT"
            finished_at = _utc_now()
            return ProcessResult(
                return_code=return_code, signal_number=signal_number, exit_reason="OUTPUT_LIMIT",
                stdout=bytes(stdout_buf), stderr=bytes(stderr_buf),
                stdout_truncated=stdout_overflow.is_set(),
                stderr_truncated=stderr_overflow.is_set(),
                started_at=started_at, finished_at=finished_at,
                duration_seconds=round(time.monotonic() - started, 6),
                child_pid=child_pid, timeout_seconds=timeout,
                argv=tuple(argv), cwd=cwd,
            )

        if capture_error.is_set():
            _terminate_process_group(process.pid)
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return_code = process.returncode
            signal_number = -return_code if return_code is not None and return_code < 0 else None
            stdout_thread.join(timeout=2.0)
            stderr_thread.join(timeout=2.0)
            finished_at = _utc_now()
            return ProcessResult(
                return_code=return_code, signal_number=signal_number, exit_reason="CAPTURE_ERROR",
                stdout=bytes(stdout_buf), stderr=bytes(stderr_buf),
                stdout_truncated=stdout_overflow.is_set(),
                stderr_truncated=stderr_overflow.is_set(),
                started_at=started_at, finished_at=finished_at,
                duration_seconds=round(time.monotonic() - started, 6),
                child_pid=child_pid, timeout_seconds=timeout,
                argv=tuple(argv), cwd=cwd, capture_error=True,
            )

        stdout_thread.join(timeout=5.0)
        stderr_thread.join(timeout=5.0)

        if stdout_thread.is_alive() or stderr_thread.is_alive() or not stdout_eof.is_set() or not stderr_eof.is_set():
            capture_error.set()
            exit_reason = "CAPTURE_ERROR"

        if return_code is not None and return_code < 0:
            signal_number = -return_code
            exit_reason = "SIGNAL"
        else:
            exit_reason = "EXITED"
    except OSError:
        exit_reason = "SPAWN_ERROR"
    except KeyboardInterrupt:
        if process is not None:
            _terminate_process_group(process.pid)
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        stdout_thread.join(timeout=2.0) if 'stdout_thread' in dir() else None
        stderr_thread.join(timeout=2.0) if 'stderr_thread' in dir() else None
        raise ExternalInterruption()
    finally:
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        pass

    finished_at = _utc_now()
    stdout_truncated = stdout_overflow.is_set()
    stderr_truncated = stderr_overflow.is_set()
    return ProcessResult(
        return_code=return_code, signal_number=signal_number,
        exit_reason=exit_reason,
        stdout=bytes(stdout_buf), stderr=bytes(stderr_buf),
        stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated,
        started_at=started_at, finished_at=finished_at,
        duration_seconds=round(time.monotonic() - started, 6),
        child_pid=child_pid, timeout_seconds=timeout,
        argv=tuple(argv), cwd=cwd, capture_error=capture_error.is_set(),
    )


def _process_result_problem(result: object, request: ProcessRequest) -> str | None:
    if not isinstance(result, ProcessResult):
        return "result-type"
    if (
        not isinstance(result.stdout, bytes)
        or not isinstance(result.stderr, bytes)
        or not isinstance(result.exit_reason, str)
        or not result.exit_reason
        or not isinstance(result.started_at, str)
        or not result.started_at
        or not isinstance(result.finished_at, str)
        or not result.finished_at
        or not isinstance(result.duration_seconds, (int, float))
        or isinstance(result.duration_seconds, bool)
        or result.duration_seconds < 0
        or not isinstance(result.stdout_truncated, bool)
        or not isinstance(result.stderr_truncated, bool)
        or not isinstance(result.capture_error, bool)
        or (result.return_code is not None and (
            not isinstance(result.return_code, int) or isinstance(result.return_code, bool)
        ))
        or (result.signal_number is not None and (
            not isinstance(result.signal_number, int)
            or isinstance(result.signal_number, bool)
            or result.signal_number <= 0
        ))
        or (result.child_pid is not None and (
            not isinstance(result.child_pid, int)
            or isinstance(result.child_pid, bool)
            or result.child_pid <= 0
        ))
    ):
        return "field-shape"
    if (
        result.argv != request.argv
        or result.cwd != request.cwd
        or result.timeout_seconds != request.timeout_seconds
    ):
        return "request-binding"
    if result.exit_reason == "EXITED" and (
        result.return_code is None
        or result.return_code < 0
        or result.signal_number is not None
        or result.child_pid is None
    ):
        return "exit-facts"
    return None


def _adapt_unittest(result: ProcessResult, check: dict) -> dict:
    try:
        stdout_text = result.stdout.decode("utf-8", errors="strict")
        stderr_text = result.stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _fail_outcome("malformed-output", None, None, None, None, None, {"encoding": "invalid-utf8"})

    stdout_ran = RAN_TESTS_RE.findall(stdout_text)
    stderr_ran = RAN_TESTS_RE.findall(stderr_text)
    total_ran = len(stdout_ran) + len(stderr_ran)

    if total_ran == 0:
        if result.exit_reason in ("SPAWN_ERROR", "TIMEOUT", "SIGNAL", "OUTPUT_LIMIT"):
            return _fail_outcome("missing-proof", None, None, None, None, None, {"streams": {"stdout_ran": 0, "stderr_ran": 0}})
        if result.return_code == 0:
            return _fail_outcome("unproved-exit-zero", None, None, None, None, None, {"streams": {"stdout_ran": 0, "stderr_ran": 0}})
        return _fail_outcome("missing-proof", None, None, None, None, None, {"streams": {"stdout_ran": 0, "stderr_ran": 0}})

    if total_ran > 1:
        return _fail_outcome("duplicate-marker", None, None, None, None, None, {"streams": {"stdout_ran": len(stdout_ran), "stderr_ran": len(stderr_ran)}})

    ran_str = (stdout_ran or stderr_ran)[0]
    test_count = int(ran_str)

    if test_count == 0:
        return _fail_outcome("zero-suite", 0, None, None, None, None, {"exit_code": result.return_code})

    stdout_ok = OK_LINE_RE.findall(stdout_text)
    stderr_ok = OK_LINE_RE.findall(stderr_text)
    stdout_failed = FAILED_LINE_RE.findall(stdout_text)
    stderr_failed = FAILED_LINE_RE.findall(stderr_text)

    total_ok = len(stdout_ok) + len(stderr_ok)
    total_failed = len(stdout_failed) + len(stderr_failed)

    if total_ok > 0 and total_failed > 0:
        return _fail_outcome("conflicting-marker", test_count, None, None, None, None, {"ok": total_ok, "failed": total_failed})

    if total_ok > 1 or total_failed > 1:
        return _fail_outcome("duplicate-marker", test_count, None, None, None, None, {"ok": total_ok, "failed": total_failed})

    if total_ok == 0 and total_failed == 0:
        if result.return_code == 0:
            return _fail_outcome("unproved-exit-zero", test_count, None, None, None, None, {})
        return _fail_outcome("missing-proof", test_count, None, None, None, None, {})

    if total_ok == 1:
        parenthetical = (stdout_ok or stderr_ok)[0]
        if parenthetical:
            return _fail_outcome("malformed-output", test_count, None, None, None, None, {"parenthetical": parenthetical})
        if result.return_code != 0:
            return _fail_outcome("status-exit-mismatch", test_count, None, None, None, None, {"expected_exit": 0, "actual_exit": result.return_code})
        return _pass_outcome(test_count, None, 0, 0, 0)

    failed_str = (stdout_failed or stderr_failed)[0]
    fields: dict[str, int] = {}
    for raw_field in failed_str.split(","):
        match = re.fullmatch(r"\s*(failures|errors|skipped)\s*=\s*(\d+)\s*", raw_field)
        if match is None or match.group(1) in fields:
            return _fail_outcome("malformed-output", test_count, None, None, None, None, {"failed_summary": failed_str})
        fields[match.group(1)] = int(match.group(2))
    failures = fields.get("failures", 0)
    errors = fields.get("errors", 0)
    skipped = fields.get("skipped", 0)

    if failures + errors + skipped > test_count:
        return _fail_outcome("malformed-output", test_count, None, failures, errors, skipped, {})

    if result.return_code != 1:
        return _fail_outcome("status-exit-mismatch", test_count, None, failures, errors, skipped, {"expected_exit": 1, "actual_exit": result.return_code})

    if skipped > 0:
        return _fail_outcome("skipped-tests", test_count, None, failures, errors, skipped, {})

    if errors > 0:
        return _fail_outcome("invocation-error", test_count, None, failures, errors, skipped, {})

    if failures > 0 and errors == 0:
        return _blocked_outcome(test_count, None, failures, errors, skipped)

    return _fail_outcome("malformed-output", test_count, None, failures, errors, skipped, {})


def _adapt_planning_validator(result: ProcessResult, check: dict) -> dict:
    try:
        combined = result.stdout.decode("utf-8", errors="strict") + "\n" + result.stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _fail_outcome("malformed-output", None, None, None, None, None, {"encoding": "invalid-utf8"})

    status_matches = PLANNING_STATUS_RE.findall(combined)
    tasks_matches = PLANNING_TASKS_RE.findall(combined)
    checks_matches = PLANNING_CHECKS_RE.findall(combined)

    if len(status_matches) != 1:
        return _fail_outcome("duplicate-marker" if len(status_matches) > 1 else "missing-proof", None, None, None, None, None, {"status_markers": len(status_matches)})
    if len(tasks_matches) != 1:
        return _fail_outcome("duplicate-marker" if len(tasks_matches) > 1 else "missing-proof", None, None, None, None, None, {"tasks_markers": len(tasks_matches)})
    if len(checks_matches) != 1:
        return _fail_outcome("duplicate-marker" if len(checks_matches) > 1 else "missing-proof", None, None, None, None, None, {"checks_markers": len(checks_matches)})

    status = status_matches[0]
    task_count = int(tasks_matches[0])
    check_count = int(checks_matches[0])

    if status == "PASS":
        if task_count <= 0 or check_count <= 0:
            return _fail_outcome("count-exit-mismatch", None, None, None, None, None, {"status": status, "tasks": task_count, "checks": check_count, "expected_exit": 0, "actual_exit": result.return_code})
        if result.return_code != 0:
            return _fail_outcome("status-exit-mismatch", None, None, None, None, None, {"status": status, "expected_exit": 0, "actual_exit": result.return_code})
        return _pass_outcome(None, None, 0, 0, 0, extra_evidence={"tasks_validated": task_count, "checks_executed": check_count})

    if status == "BLOCKED":
        if task_count <= 0 or check_count <= 0:
            return _fail_outcome("count-exit-mismatch", None, None, None, None, None, {"status": status, "tasks": task_count, "checks": check_count})
        if result.return_code != 1:
            return _fail_outcome("status-exit-mismatch", None, None, None, None, None, {"status": status, "expected_exit": 1, "actual_exit": result.return_code})
        return _blocked_outcome(None, None, 0, 0, 0, extra_evidence={"tasks_validated": task_count, "checks_executed": check_count})

    if status == "FAIL":
        if result.return_code != 1:
            return _fail_outcome("status-exit-mismatch", None, None, None, None, None, {"status": status, "expected_exit": 1, "actual_exit": result.return_code})
        return _fail_outcome("verification-failed", None, None, None, None, None, {"tasks_validated": task_count, "checks_executed": check_count})

    return _fail_outcome("unknown-state", None, None, None, None, None, {"status": status})


def _pass_outcome(assertions, suites, failures, errors, skipped, extra_evidence=None):
    return {
        "schema": CHECK_OUTCOME_SCHEMA,
        "status": "PASS",
        "reason": "",
        "assertions": assertions,
        "suites": suites,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "evidence": extra_evidence or {},
    }


def _blocked_outcome(assertions, suites, failures, errors, skipped, extra_evidence=None):
    return {
        "schema": CHECK_OUTCOME_SCHEMA,
        "status": "BLOCKED",
        "reason": "assertion-failure",
        "assertions": assertions,
        "suites": suites,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "evidence": extra_evidence or {},
    }


def _fail_outcome(reason, assertions, suites, failures, errors, skipped, extra_evidence=None):
    return {
        "schema": CHECK_OUTCOME_SCHEMA,
        "status": "FAIL",
        "reason": reason,
        "assertions": assertions,
        "suites": suites,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "evidence": extra_evidence or {},
    }


def _adapt_task_contract(result: ProcessResult, check: dict) -> dict:
    try:
        stdout_text = result.stdout.decode("utf-8", errors="strict")
        stderr_text = result.stderr.decode("utf-8", errors="strict")
        value = json.loads(stdout_text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail_outcome("malformed-output", None, None, None, None, None, {})
    if stderr_text or not isinstance(value, dict):
        return _fail_outcome("malformed-output", None, None, None, None, None, {})
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if result.stdout.rstrip(b"\n") != canonical:
        return _fail_outcome("malformed-output", None, None, None, None, None, {})
    if value.get("schema_version") != "lexiflow.task-contract-check.v1":
        return _fail_outcome("schema-mismatch", None, None, None, None, None, {})
    subject = check.get("subject_task", {}).get("task_id")
    if value.get("task_id") != subject:
        return _fail_outcome("subject-identity-mismatch", None, None, None, None, None, {})
    status = value.get("status")
    assertions = value.get("assertions")
    if status == "FAIL":
        if result.return_code != 2 or assertions != [] or not isinstance(value.get("reason"), str):
            return _fail_outcome("malformed-output", None, None, None, None, None, {})
        return _fail_outcome(value["reason"], 0, 1, None, None, None, {})
    if not isinstance(assertions, list) or not assertions:
        return _fail_outcome("malformed-output", None, None, None, None, None, {})
    ids: set[str] = set()
    statuses: list[str] = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            return _fail_outcome("malformed-output", None, None, None, None, None, {})
        assertion_id = assertion.get("id")
        assertion_status = assertion.get("status")
        if (
            not isinstance(assertion_id, str)
            or assertion_id in ids
            or assertion_status not in {"PASS", "BLOCKED"}
        ):
            return _fail_outcome("malformed-output", None, None, None, None, None, {})
        ids.add(assertion_id)
        statuses.append(assertion_status)
    evidence = {
        "task_id": subject,
        "input_count": len(value.get("inputs", [])) if isinstance(value.get("inputs"), list) else None,
        "assertion_ids": sorted(ids),
    }
    if status == "PASS" and result.return_code == 0 and all(item == "PASS" for item in statuses):
        return _pass_outcome(len(assertions), 1, 0, 0, 0, extra_evidence=evidence)
    if status == "BLOCKED" and result.return_code == 1 and "BLOCKED" in statuses:
        return _blocked_outcome(
            len(assertions), 1, statuses.count("BLOCKED"), 0, 0, extra_evidence=evidence,
        )
    return _fail_outcome("malformed-output", None, None, None, None, None, evidence)


def _select_adapter(argv: list[str], command_id: str) -> Callable | None:
    if not argv:
        return None
    if len(argv) >= 3 and argv[1] == "-m":
        executable = argv[0]
        if executable == "python3":
            module = argv[2]
            if module == "scripts.gates.planning" and command_id == _PLANNING_COMMAND_ID:
                return _adapt_planning_validator
            if module == "unittest" and command_id in _UNITTEST_COMMAND_IDS:
                return _adapt_unittest
            if (
                module == "scripts.gates.task_contracts"
                and _TASK_CONTRACT_COMMAND_RE.fullmatch(command_id)
                and len(argv) == 5
                and argv[3] == "--task-id"
                and _TASK_ID_RE.fullmatch(argv[4])
            ):
                return _adapt_task_contract
    return None


def _validate_plan_projection(plan: dict) -> None:
    if not isinstance(plan, dict):
        raise ExecutorError("invalid-plan", "plan must be a dict")
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise ExecutorError("schema-mismatch", f"expected {PLAN_SCHEMA}")
    checks = plan.get("checks")
    if not isinstance(checks, list):
        raise ExecutorError("invalid-plan", "checks must be a list")
    if len(checks) == 0:
        raise ExecutorError("empty-checks", "no checks to execute")

    seen_check_ids: set[str] = set()
    seen_subjects: set[str] = set()
    for i, check in enumerate(checks):
        if not isinstance(check, dict):
            raise ExecutorError("invalid-check-projection", f"checks[{i}] must be a dict")
        required_keys = {
            "check_id", "check_version", "owner", "subject_task",
            "command_id", "fixed_argv", "cwd", "timeout_seconds",
            "consumed_inputs", "outcome_contract", "required",
        }
        missing = required_keys - set(check.keys())
        if missing:
            raise ExecutorError("invalid-check-projection", f"checks[{i}] missing: {sorted(missing)}")

        check_id = check["check_id"]
        if not isinstance(check_id, str) or not _SAFE_ID_RE.fullmatch(check_id):
            raise ExecutorError("invalid-check-projection", f"checks[{i}].check_id invalid")
        if check_id in seen_check_ids:
            raise ExecutorError("duplicate-check-id", f"duplicate check_id: {check_id}")
        seen_check_ids.add(check_id)

        if not isinstance(check["check_version"], int) or check["check_version"] <= 0:
            raise ExecutorError("invalid-check-projection", f"checks[{i}].check_version invalid")
        if not isinstance(check["owner"], str) or not _SAFE_ID_RE.fullmatch(check["owner"]):
            raise ExecutorError("owner-mismatch", f"checks[{i}].owner invalid")

        subject = check["subject_task"]
        if not isinstance(subject, dict):
            raise ExecutorError("invalid-check-projection", f"checks[{i}].subject_task must be a dict")
        for field in ("task_id", "task_version", "change_version", "owner"):
            if field not in subject:
                raise ExecutorError("subject-identity-mismatch", f"checks[{i}].subject_task missing {field}")
        if not isinstance(subject["task_id"], str) or not _TASK_ID_RE.fullmatch(subject["task_id"]):
            raise ExecutorError("subject-identity-mismatch", f"checks[{i}].subject_task.task_id invalid")
        if not isinstance(subject["task_version"], int) or subject["task_version"] <= 0:
            raise ExecutorError("subject-identity-mismatch", f"checks[{i}].subject_task.task_version invalid")
        if not isinstance(subject["change_version"], str) or not _SEMVER_RE.fullmatch(subject["change_version"]):
            raise ExecutorError("subject-identity-mismatch", f"checks[{i}].subject_task.change_version invalid")
        if not isinstance(subject["owner"], str) or not _SAFE_ID_RE.fullmatch(subject["owner"]):
            raise ExecutorError("owner-mismatch", f"checks[{i}].subject_task.owner invalid")

        if subject["task_id"] in seen_subjects:
            raise ExecutorError("duplicate-subject-task", f"duplicate subject: {subject['task_id']}")
        seen_subjects.add(subject["task_id"])

        command_id = check["command_id"]
        if not isinstance(command_id, str) or not _SAFE_ID_RE.fullmatch(command_id):
            raise ExecutorError("invalid-check-projection", f"checks[{i}].command_id invalid")

        argv = check["fixed_argv"]
        if not isinstance(argv, list) or not argv:
            raise ExecutorError("invalid-argv", f"checks[{i}].fixed_argv must be non-empty list")
        if any(not isinstance(a, str) or not a or "\x00" in a for a in argv):
            raise ExecutorError("invalid-argv", f"checks[{i}].fixed_argv has invalid entries")

        cwd = check["cwd"]
        if not isinstance(cwd, str) or not cwd:
            raise ExecutorError("invalid-cwd", f"checks[{i}].cwd invalid")

        timeout = check["timeout_seconds"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise ExecutorError("invalid-timeout", f"checks[{i}].timeout_seconds invalid")

        consumed = check["consumed_inputs"]
        if not isinstance(consumed, list):
            raise ExecutorError("invalid-consumed-input", f"checks[{i}].consumed_inputs must be list")
        for j, desc in enumerate(consumed):
            if not isinstance(desc, dict):
                raise ExecutorError("invalid-consumed-input", f"checks[{i}].consumed_inputs[{j}] must be dict")
            if set(desc.keys()) != {"locator", "state", "sha256"}:
                raise ExecutorError("invalid-consumed-input", f"checks[{i}].consumed_inputs[{j}] keys invalid")
            if not isinstance(desc["locator"], str) or not desc["locator"]:
                raise ExecutorError("invalid-consumed-input", f"checks[{i}].consumed_inputs[{j}].locator invalid")
            if desc["state"] != "present":
                raise ExecutorError("invalid-consumed-input", f"checks[{i}].consumed_inputs[{j}].state must be 'present'")
            if not isinstance(desc["sha256"], str) or not _HEX_RE.fullmatch(desc["sha256"]):
                raise ExecutorError("invalid-consumed-input", f"checks[{i}].consumed_inputs[{j}].sha256 invalid")

        outcome = check["outcome_contract"]
        if not isinstance(outcome, dict):
            raise ExecutorError("invalid-check-projection", f"checks[{i}].outcome_contract must be dict")
        if outcome.get("schema") != CHECK_OUTCOME_SCHEMA:
            raise ExecutorError("schema-mismatch", f"checks[{i}].outcome_contract.schema mismatch")
        if outcome.get("required_fields") != ["exit_code", "stdout_locator", "stderr_locator", "typed_result"]:
            raise ExecutorError("invalid-check-projection", f"checks[{i}].outcome_contract.required_fields mismatch")

        if not isinstance(check["required"], bool):
            raise ExecutorError("invalid-check-projection", f"checks[{i}].required must be bool")


def _executable_facts(binding: ExecutableBinding | None, *, pre: str, post: str) -> dict:
    if binding is None:
        return {
            "executed_argv": [],
            "executable_locator": "",
            "executable_sha256": "",
            "executable_device": None,
            "executable_inode": None,
            "executable_size": None,
            "executable_verification": {"pre_execution": pre, "post_execution": post},
        }
    return {
        "executable_locator": binding.locator,
        "executable_sha256": binding.sha256,
        "executable_device": binding.device,
        "executable_inode": binding.inode,
        "executable_size": binding.size,
        "executable_verification": {"pre_execution": pre, "post_execution": post},
    }


def _build_process_facts(
    result: ProcessResult,
    env_fingerprint: str,
    binding: ExecutableBinding,
    *,
    post_verification: str,
) -> dict:
    return {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.duration_seconds,
        "argv": list(result.argv),
        "argv_fingerprint": _fingerprint(list(result.argv)),
        "executed_argv": [binding.locator, *result.argv[1:]],
        "cwd": result.cwd,
        "cwd_fingerprint": sha256_bytes(result.cwd.encode("utf-8")),
        "return_code": result.return_code,
        "signal": result.signal_number,
        "exit_reason": result.exit_reason,
        "stdout_bytes": len(result.stdout),
        "stderr_bytes": len(result.stderr),
        "stdout_sha256": sha256_bytes(result.stdout),
        "stderr_sha256": sha256_bytes(result.stderr),
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "capture_error": result.capture_error,
        "environment_fingerprint": env_fingerprint,
        "child_pid": result.child_pid,
        "timeout_seconds": result.timeout_seconds,
        **_executable_facts(binding, pre="verified", post=post_verification),
    }


def _empty_process_facts(
    argv: list[str], cwd: str, env_fingerprint: str, timeout: int, reason: str,
    binding: ExecutableBinding | None = None,
) -> dict:
    now = _utc_now()
    return {
        "started_at": now,
        "finished_at": now,
        "duration_seconds": 0.0,
        "argv": list(argv),
        "argv_fingerprint": _fingerprint(list(argv)),
        "executed_argv": [binding.locator, *argv[1:]] if binding else [],
        "cwd": cwd,
        "cwd_fingerprint": sha256_bytes(cwd.encode("utf-8")),
        "return_code": None,
        "signal": None,
        "exit_reason": reason,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "stdout_sha256": sha256_bytes(b""),
        "stderr_sha256": sha256_bytes(b""),
        "stdout_truncated": False,
        "stderr_truncated": False,
        "capture_error": False,
        "environment_fingerprint": env_fingerprint,
        "child_pid": None,
        "timeout_seconds": timeout,
        **_executable_facts(binding, pre="verified" if binding else "not-run", post="not-run"),
    }


def _execute_single_check(
    check: dict,
    repo_root: str,
    runner: Callable[[ProcessRequest], ProcessResult],
    env: dict[str, str],
    env_fingerprint: str,
    stdout_limit: int,
    stderr_limit: int,
) -> dict:
    check_id = check["check_id"]
    argv = check["fixed_argv"]
    cwd_rel = check["cwd"]
    timeout = check["timeout_seconds"]
    consumed = check["consumed_inputs"]

    pre_verification = _verify_consumed_inputs(repo_root, consumed)
    if not _all_verified(pre_verification):
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _empty_process_facts(argv, cwd_rel, env_fingerprint, timeout, "INPUT_DRIFT"),
            "outcome": _fail_outcome("input-drift", None, None, None, None, None, {"pre_verification": pre_verification}),
            "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": []},
        }

    try:
        resolved_cwd = _resolve_cwd(repo_root, cwd_rel)
    except ExecutorError as exc:
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _empty_process_facts(argv, cwd_rel, env_fingerprint, timeout, exc.code),
            "outcome": _fail_outcome(exc.code, None, None, None, None, None, {}),
            "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": []},
        }

    adapter = _select_adapter(argv, check["command_id"])
    if adapter is None:
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _empty_process_facts(argv, cwd_rel, env_fingerprint, timeout, "unknown-adapter"),
            "outcome": _fail_outcome("unknown-adapter", None, None, None, None, None, {}),
            "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": []},
        }

    try:
        executable = _resolve_registered_executable(argv[0])
    except ExecutorError as exc:
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _empty_process_facts(argv, cwd_rel, env_fingerprint, timeout, exc.code),
            "outcome": _fail_outcome(exc.code, None, None, None, None, None, {}),
            "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": []},
        }

    request = ProcessRequest(
        argv=tuple(argv),
        cwd=resolved_cwd,
        env=tuple(sorted(env.items())),
        timeout_seconds=timeout,
        stdout_limit=stdout_limit,
        stderr_limit=stderr_limit,
        executable=executable,
    )

    try:
        result = runner(request)
    except ExternalInterruption:
        raise
    except Exception as exc:
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _empty_process_facts(argv, cwd_rel, env_fingerprint, timeout, "spawn-error", executable),
            "outcome": _fail_outcome("spawn-error", None, None, None, None, None, {"error_type": type(exc).__name__}),
            "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": []},
        }

    process_problem = _process_result_problem(result, request)
    if process_problem is not None:
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _empty_process_facts(
                argv, resolved_cwd, env_fingerprint, timeout,
                "MALFORMED_PROCESS_RESULT", executable,
            ),
            "outcome": _fail_outcome(
                "malformed-process-result", None, None, None, None, None,
                {"problem": process_problem, "result_type": type(result).__name__},
            ),
            "consumed_input_verification": {
                "pre_execution": pre_verification,
                "post_execution": [],
            },
        }

    executable_post = "verified" if _verify_executable_binding(executable) else "drift"
    post_verification = _verify_consumed_inputs(repo_root, consumed)
    if executable_post != "verified":
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _build_process_facts(result, env_fingerprint, executable, post_verification=executable_post),
            "outcome": _fail_outcome("executable-drift", None, None, None, None, None, {}),
            "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": post_verification},
        }
    if not _all_verified(post_verification):
        return {
            "check_id": check_id,
            "check_version": check["check_version"],
            "owner": check["owner"],
            "subject_task": dict(check["subject_task"]),
            "required": check["required"],
            "command_id": check["command_id"],
            "selection_reasons": list(check.get("selection_reasons", [])),
            "process": _build_process_facts(result, env_fingerprint, executable, post_verification=executable_post),
            "outcome": _fail_outcome("input-drift", None, None, None, None, None, {"post_verification": post_verification}),
            "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": post_verification},
        }

    if result.exit_reason == "TIMEOUT":
        outcome = _fail_outcome("timeout", None, None, None, None, None, {})
    elif result.exit_reason == "SIGNAL":
        outcome = _fail_outcome("signal-death", None, None, None, None, None, {"signal": result.signal_number})
    elif result.exit_reason == "SPAWN_ERROR":
        outcome = _fail_outcome("spawn-error", None, None, None, None, None, {})
    elif result.exit_reason == "OUTPUT_LIMIT":
        outcome = _fail_outcome("output-limit-exceeded", None, None, None, None, None, {})
    elif result.exit_reason == "CAPTURE_ERROR" or result.capture_error:
        outcome = _fail_outcome("capture-failure", None, None, None, None, None, {})
    elif result.stdout_truncated or result.stderr_truncated:
        outcome = _fail_outcome("output-limit-exceeded", None, None, None, None, None, {})
    elif result.exit_reason != "EXITED":
        outcome = _fail_outcome(
            "unknown-process-state", None, None, None, None, None,
            {"exit_reason": result.exit_reason},
        )
    else:
        try:
            outcome = adapter(result, check)
        except Exception as exc:
            outcome = _fail_outcome("adapter-failure", None, None, None, None, None, {"error_type": type(exc).__name__})

    return {
        "check_id": check_id,
        "check_version": check["check_version"],
        "owner": check["owner"],
        "subject_task": dict(check["subject_task"]),
        "required": check["required"],
        "command_id": check["command_id"],
        "selection_reasons": list(check.get("selection_reasons", [])),
        "process": _build_process_facts(result, env_fingerprint, executable, post_verification=executable_post),
        "outcome": outcome,
        "consumed_input_verification": {"pre_execution": pre_verification, "post_execution": post_verification},
    }


def _not_run_check(check: dict, env_fingerprint: str) -> dict:
    argv = check["fixed_argv"]
    cwd = check["cwd"]
    timeout = check["timeout_seconds"]
    return {
        "check_id": check["check_id"],
        "check_version": check["check_version"],
        "owner": check["owner"],
        "subject_task": dict(check["subject_task"]),
        "required": check["required"],
        "command_id": check["command_id"],
        "selection_reasons": list(check.get("selection_reasons", [])),
        "process": _empty_process_facts(argv, cwd, env_fingerprint, timeout, "NOT_RUN"),
        "outcome": _fail_outcome("not-run", None, None, None, None, None, {}),
        "consumed_input_verification": {"pre_execution": [], "post_execution": []},
    }


def _aggregate(checks: list[dict]) -> tuple[str, str]:
    if not checks:
        return "FAIL", "empty-checks"

    has_fail = False
    has_blocked = False
    fail_reason = ""
    blocked_reason = ""

    for check in checks:
        status = check["outcome"]["status"]
        if status == "FAIL":
            has_fail = True
            if not fail_reason:
                fail_reason = check["outcome"]["reason"] or "outcome-unknown"
        elif status == "BLOCKED":
            has_blocked = True
            if not blocked_reason:
                blocked_reason = check["outcome"]["reason"] or "verification-failed"
        elif status != "PASS":
            has_fail = True
            if not fail_reason:
                fail_reason = "unknown-state"

    if has_fail:
        return "FAIL", fail_reason or "outcome-unknown"
    if has_blocked:
        return "BLOCKED", blocked_reason or "verification-failed"
    if all(c["outcome"]["status"] == "PASS" for c in checks):
        return "PASS", ""
    return "FAIL", "unknown-state"


def execute_checks(
    plan: dict,
    *,
    repo_root: str,
    process_runner: Callable[[ProcessRequest], ProcessResult] | None = None,
    clock: Callable[[], float] | None = None,
    now: Callable[[], str] | None = None,
    stdout_limit: int = DEFAULT_STDOUT_LIMIT,
    stderr_limit: int = DEFAULT_STDERR_LIMIT,
) -> dict:
    if process_runner is None:
        process_runner = default_process_runner
    if clock is None:
        clock = time.monotonic
    if now is None:
        now = _utc_now

    started = clock()
    started_at = now()

    try:
        _validate_plan_projection(plan)
    except ExecutorError as exc:
        finished_at = now()
        return {
            "schema_version": OUTCOME_SCHEMA,
            "plan_content_fingerprint": plan.get("content_fingerprint", "") if isinstance(plan, dict) else "",
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_seconds": round(clock() - started, 6),
            "checks": [],
            "run_status": "FAIL",
            "run_reason": exc.code,
            "aggregation": {"total": 0, "passed": 0, "blocked": 0, "failed": 0, "required_total": 0, "required_passed": 0},
        }

    checks = plan["checks"]
    env = _build_child_environment()
    env_fingerprint = _environment_fingerprint(env)

    results: list[dict] = []
    interrupted = False

    for check in checks:
        if interrupted:
            results.append(_not_run_check(check, env_fingerprint))
            continue
        try:
            result = _execute_single_check(check, repo_root, process_runner, env, env_fingerprint, stdout_limit, stderr_limit)
            results.append(result)
        except ExternalInterruption:
            interrupted = True
            results.append(_not_run_check(check, env_fingerprint))

    if interrupted:
        for check in checks[len(results):]:
            results.append(_not_run_check(check, env_fingerprint))

    run_status, run_reason = _aggregate(results)
    finished_at = now()

    passed = sum(1 for r in results if r["outcome"]["status"] == "PASS")
    blocked = sum(1 for r in results if r["outcome"]["status"] == "BLOCKED")
    failed = sum(1 for r in results if r["outcome"]["status"] == "FAIL")
    required_total = sum(1 for r in results if r["required"])
    required_passed = sum(1 for r in results if r["required"] and r["outcome"]["status"] == "PASS")

    return {
        "schema_version": OUTCOME_SCHEMA,
        "plan_content_fingerprint": plan.get("content_fingerprint", ""),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(clock() - started, 6),
        "checks": results,
        "run_status": run_status,
        "run_reason": run_reason,
        "aggregation": {
            "total": len(results),
            "passed": passed,
            "blocked": blocked,
            "failed": failed,
            "required_total": required_total,
            "required_passed": required_passed,
        },
    }
