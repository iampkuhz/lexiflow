"""Qoder 任务生命周期管理。

本模块负责每 run 独立 watchdog 调度、显式幂等 ack、resume 替代链及共享 callback claim；
不负责 LLM 调用、全局 daemon、自动重试或取消 Qoder；
由 agents/qoder_task.py 调用；不反向导入该入口。
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable  # noqa: TC003
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# 支持不设置 PYTHONPATH 直接运行本脚本；把仓库根加入搜索路径
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# --- 常量 ---

WATCHDOG_INITIAL_DELAY = 300  # 首次检查延迟，5 分钟
WATCHDOG_INTERVAL = 600  # 后续检查间隔，10 分钟
WATCHDOG_MAX_LIFETIME = 86400  # 最大生命周期，24 小时

WATCHDOG_CLAIM = "watchdog.claim"
LIFECYCLE_FILE = "lifecycle.json"
ACK_FILE = "ack.json"
STARTED_FILE = "started.json"
WATCHDOG_STATUS_FILE = "watchdog.status.json"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")

# 回调认领文件名，与 qoder_task._callback_claim_path 共享
CALLBACK_CLAIM = "callback.claim"
CALLBACK_FILE = "callback.json"
LOCK_FILE = "lifecycle.lock"


@contextmanager
def _run_lock(run_dir: Path):
    """per-run 文件锁上下文管理器，防止并发修改生命周期状态。"""
    lock_path = run_dir / LOCK_FILE
    lock_fd = _open_private_file(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _open_private_file(path: Path, flags: int) -> int:
    """仅打开本 run 的普通文件，拒绝软链和特殊设备。"""
    _check_no_symlink_ancestors(path)
    fd = os.open(str(path), flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"not a regular file: {path}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _is_valid_uuid(value: str) -> bool:
    """校验值是否为合法 UUID 格式。"""
    return isinstance(value, str) and bool(_UUID_RE.fullmatch(value))


def _validate_run_id(run_id: str) -> str:
    """校验 run_id 格式，防路径穿越。"""
    if not _ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid run id: {run_id}")
    return run_id


def _check_no_symlink_ancestors(path: Path) -> None:
    """拒绝路径或其任何祖先为符号链接。"""
    abs_path = path.absolute()
    for component in [abs_path, *abs_path.parents]:
        if component.is_symlink():
            if component == Path("/var") and component.resolve() == Path(
                "/private/var"
            ):
                continue
            raise ValueError(f"symlink in path: {component}")


def _atomic_write_json(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    """原子写入 JSON，设置文件权限。"""
    _check_no_symlink_ancestors(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.chmod(tmp, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_read_json(path: Path) -> dict[str, Any]:
    """安全读取 JSON，拒绝符号链接。"""
    if path.is_symlink():
        raise ValueError(f"refusing to read symlink: {path}")
    _check_no_symlink_ancestors(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _is_pid_alive(pid: int) -> bool:
    """检查指定 PID 是否存活。PermissionError 视为存活（无法确认死亡）。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但无权限检查，视为存活
        return True


def _safe_read_pid(pid_file: Path) -> int | None:
    """安全读取 PID 文件，拒绝符号链接、非法值、负数、0。"""
    if pid_file.is_symlink():
        return None
    _check_no_symlink_ancestors(pid_file)
    try:
        content = pid_file.read_text(encoding="utf-8").strip()
        pid = int(content)
        if pid <= 0:
            return None
        return pid
    except (ValueError, OSError):
        return None


# --- 路径辅助 ---


def watchdog_claim_path(run_dir: Path) -> Path:
    """返回 watchdog 认领文件路径。"""
    return run_dir / WATCHDOG_CLAIM


def lifecycle_path(run_dir: Path) -> Path:
    """返回生命周期文件路径。"""
    return run_dir / LIFECYCLE_FILE


def ack_path(run_dir: Path) -> Path:
    """返回消费确认文件路径。"""
    return run_dir / ACK_FILE


def started_path(run_dir: Path) -> Path:
    """返回 worker 启动确认文件路径。"""
    return run_dir / STARTED_FILE


def callback_claim_path(run_dir: Path) -> Path:
    """返回共享回调认领文件路径。"""
    return run_dir / CALLBACK_CLAIM


def callback_file_path(run_dir: Path) -> Path:
    """返回回调结果文件路径。"""
    return run_dir / CALLBACK_FILE


def watchdog_status_path(run_dir: Path) -> Path:
    """返回 watchdog 状态文件路径。"""
    return run_dir / WATCHDOG_STATUS_FILE


# --- 生命周期状态 ---


def read_lifecycle(run_dir: Path) -> dict[str, Any]:
    """读取生命周期状态，不存在返回空字典。"""
    path = lifecycle_path(run_dir)
    if not path.exists():
        return {}
    return _safe_read_json(path)


def write_lifecycle(run_dir: Path, data: dict[str, Any]) -> None:
    """写入生命周期状态。"""
    _atomic_write_json(lifecycle_path(run_dir), data)


def read_watchdog_status(run_dir: Path) -> dict[str, Any]:
    """读取 watchdog 状态，不存在返回空字典。"""
    path = watchdog_status_path(run_dir)
    if not path.exists():
        return {}
    return _safe_read_json(path)


def write_watchdog_status(run_dir: Path, data: dict[str, Any]) -> None:
    """写入 watchdog 状态。"""
    _atomic_write_json(watchdog_status_path(run_dir), data)


# --- 启动标记 ---


def record_started(run_dir: Path, pid: int, session_id: str = "") -> None:
    """记录 worker 已启动执行并确认身份。幂等。"""
    path = started_path(run_dir)
    if path.exists():
        return
    _atomic_write_json(
        path,
        {
            "run_id": run_dir.name,
            "started_at": time.time(),
            "pid": pid,
            "session_id": session_id,
        },
    )


# --- 看门狗 ---


def start_watchdog(run_dir: Path, repo_root: Path) -> subprocess.Popen:
    """启动 watchdog 后台进程。stderr 写入 watchdog.log 供诊断。"""
    watchdog_cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "_watchdog",
        str(run_dir.parent),  # 任务目录
        run_dir.name,  # 运行标识
        str(repo_root),
    ]
    # stderr 写入日志文件供诊断，stdout 仍丢弃
    log_path = run_dir / "watchdog.log"
    log_fd = _open_private_file(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY)
    try:
        return subprocess.Popen(
            watchdog_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_fd,
            start_new_session=True,
            cwd=str(repo_root),
        )
    finally:
        os.close(log_fd)


def watchdog_entry(task_dir_str: str, run_id: str, repo_root_str: str) -> int:
    """Watchdog 入口，由 _watchdog 子命令调用。初始化在顶层异常保护内。"""
    task_dir = Path(task_dir_str)
    repo_root = Path(repo_root_str)
    validated_run_dir: Path | None = None

    try:
        _validate_run_id(run_id)
        _check_no_symlink_ancestors(task_dir)
        run_dir = task_dir / run_id
        _check_no_symlink_ancestors(run_dir)

        try:
            task_dir.resolve().relative_to(repo_root.resolve())
            run_dir.resolve().relative_to(repo_root.resolve())
        except ValueError:
            print("error: task_dir/run_dir must be inside repo_root", file=sys.stderr)
            return 1
        validated_run_dir = run_dir

        # 创建 watchdog 唯一 claim
        claim = watchdog_claim_path(run_dir)
        try:
            fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                return 0  # 已有 watchdog 运行
            _write_watchdog_error(run_dir, f"claim failed: {exc}")
            print(f"error: watchdog claim failed: {exc}", file=sys.stderr)
            return 1

        # 记录 watchdog 启动
        write_watchdog_status(
            run_dir,
            {"status": "running", "pid": os.getpid(), "started_at": time.time()},
        )

        from scripts.agents.qoder.callback import attempt_codex_callback

        send_callback = attempt_codex_callback

        result = watchdog_loop(run_dir, repo_root, send_callback=send_callback)
        status = read_watchdog_status(run_dir)
        status.update(status="exited", result=result, exited_at=time.time())
        write_watchdog_status(run_dir, status)
        return 0

    except Exception as exc:
        # 顶层异常保护：确保诊断可见
        try:
            if validated_run_dir is not None:
                status = read_watchdog_status(validated_run_dir)
                status.update(
                    status="error",
                    error=type(exc).__name__,
                    message=str(exc),
                    exited_at=time.time(),
                )
                write_watchdog_status(
                    validated_run_dir,
                    status,
                )
        except (OSError, ValueError):
            pass
        print(f"error: watchdog failed: {exc}", file=sys.stderr)
        return 1


def _write_watchdog_error(run_dir: Path, error: str) -> None:
    """记录 watchdog 启动错误。"""
    try:
        write_watchdog_status(run_dir, {"status": "unavailable", "error": error})
    except OSError as exc:
        print(f"warning: watchdog diagnostic unavailable: {exc}", file=sys.stderr)


def watchdog_loop(
    run_dir: Path,
    repo_root: Path,
    *,
    send_callback: Callable | None = None,
    sleep_fn: Callable = time.sleep,
    clock_fn: Callable = time.monotonic,
) -> str:
    """Watchdog 主循环。返回最终状态。

    精确在 WATCHDOG_MAX_LIFETIME 秒后退出（使用 min(delay, remaining)）。
    检查 ack 权威（ack.json 存在）即退出，不覆盖已消费/替代状态。

    参数：
        run_dir: run 目录
        repo_root: 仓库根目录
        send_callback: 回调发送函数，默认使用 qoder callback transport
        sleep_fn: 可注入的 sleep 函数（测试用）
        clock_fn: 可注入的时钟函数（测试用）
    """
    if send_callback is None:
        from scripts.agents.qoder.callback import attempt_codex_callback

        send_callback = attempt_codex_callback

    start_time = clock_fn()
    delay = WATCHDOG_INITIAL_DELAY

    while True:
        elapsed = clock_fn() - start_time
        remaining = WATCHDOG_MAX_LIFETIME - elapsed

        # 已达最大生命周期
        if remaining <= 0:
            _record_exhausted(run_dir)
            return "exhausted"

        # 等待不超过剩余寿命，避免越过截止时间
        actual_sleep = min(delay, remaining)
        sleep_fn(actual_sleep)

        # 检查 ack 权威（ack.json 存在即退出）
        ack_file = ack_path(run_dir)
        if ack_file.exists():
            return "acknowledged"
        if clock_fn() - start_time >= WATCHDOG_MAX_LIFETIME:
            _record_exhausted(run_dir)
            return "exhausted"

        # 执行常规检查
        action = watchdog_check(run_dir, repo_root, send_callback)

        if action in ("acknowledged", "superseded", "exhausted"):
            return action

        delay = WATCHDOG_INTERVAL


def watchdog_check(
    run_dir: Path,
    repo_root: Path,
    send_callback: Callable,
) -> str:
    """单次 watchdog 检查。返回执行的动作。

    返回：
        "continue" - 继续监控
        "acknowledged" - run 已被消费，停止
        "superseded" - run 已被替代，停止
        "sent_first" - 发送了首次通知
        "recorded_unknown" - 记录了 worker 异常
    """
    if ack_path(run_dir).exists():
        return "acknowledged"
    # 读取生命周期
    lifecycle = read_lifecycle(run_dir)
    status = lifecycle.get("status", "")
    if status in ("acknowledged", "superseded"):
        return status

    # 读取任务
    task_file = run_dir / "task.json"
    if not task_file.exists():
        return "continue"
    task = _safe_read_json(task_file)

    # 只监控 codex 父任务
    if task.get("parent_client") != "codex":
        return "continue"

    parent_sid = task.get("parent_session_id", "")
    if not parent_sid or not _is_valid_uuid(parent_sid):
        return "continue"

    run_id = run_dir.name
    completion_path = run_dir / "completion.json"
    cb_path = callback_file_path(run_dir)
    claim_path = callback_claim_path(run_dir)

    if completion_path.exists():
        # Worker 已完成
        completion = _safe_read_json(completion_path)

        # 检查是否已有回调尝试
        cb_exists = cb_path.exists()
        claim_exists = claim_path.exists()

        if not cb_exists and not claim_exists:
            # 从未尝试发送 - 补发首次通知
            send_callback(run_dir, run_id, completion, task, repo_root)
            return "sent_first"

        return "continue"

    # 无 completion - 检查 worker 状态
    pid_file = run_dir / "worker.pid"
    if pid_file.exists():
        pid = _safe_read_pid(pid_file)
        if pid is not None and not _is_pid_alive(pid):
            # Worker 确认死亡且无 completion
            _record_anomaly(run_dir, "worker died without completion")

            # 尝试一次异常通知（使用相同 claim 闸门）
            if not claim_path.exists() and not cb_path.exists():
                anomaly_completion = {
                    "status": "unknown",
                    "exit_code": "",
                    "task_id": task.get("task_id", ""),
                    "title": task.get("title", task.get("task_id", "")),
                    "session_id": task.get("session_id", ""),
                    "agent_id": task.get("agent_id", ""),
                    "client": task.get("client", "qoder"),
                    "parent_client": task.get("parent_client", ""),
                    "parent_session_id": parent_sid,
                    "error": "worker died without completion",
                }
                send_callback(run_dir, run_id, anomaly_completion, task, repo_root)

            return "recorded_unknown"
        # pid 无效（符号链接或非法值）或权限不足（进程存活）
        # 记录诊断但不伪造 worker 死亡
        if pid is None:
            _record_watchdog_diagnostic(
                run_dir, "pid_file_invalid", "worker.pid unreadable or invalid"
            )
    else:
        # PID 文件不存在 - 可能 worker 尚未启动或已清理
        _record_watchdog_diagnostic(run_dir, "pid_file_missing", "worker.pid not found")

    return "continue"


def _record_watchdog_diagnostic(run_dir: Path, key: str, message: str) -> None:
    """记录 watchdog 诊断信息（不伪造 worker 死亡）。"""
    status_file = watchdog_status_path(run_dir)
    try:
        status = read_watchdog_status(run_dir) if status_file.exists() else {}
        diagnostics = status.get("diagnostics", {})
        # 只记录首次或变化的诊断
        if diagnostics.get(key) != message:
            diagnostics[key] = message
            diagnostics["updated_at"] = time.time()
            status["diagnostics"] = diagnostics
            write_watchdog_status(run_dir, status)
    except OSError as exc:
        print(f"warning: watchdog diagnostic unavailable: {exc}", file=sys.stderr)


def _record_exhausted(run_dir: Path) -> None:
    """记录 watchdog 已达最大生命周期。使用 per-run 锁，检查 ack 权威。"""
    with _run_lock(run_dir):
        # 检查 ack 权威：ack.json 存在则不覆盖
        ack_file = ack_path(run_dir)
        if ack_file.exists():
            return
        lifecycle = read_lifecycle(run_dir)
        if lifecycle.get("status") in ("acknowledged", "superseded"):
            return
        watchdog = read_watchdog_status(run_dir)
        watchdog.update(status="exhausted", exhausted_at=time.time())
        write_watchdog_status(run_dir, watchdog)
        if lifecycle.get("status") != "unknown":
            lifecycle["status"] = "exhausted"
            lifecycle["exhausted_at"] = time.time()
            write_lifecycle(run_dir, lifecycle)


def _record_anomaly(run_dir: Path, reason: str) -> None:
    """记录 worker 死亡且无 completion。使用 per-run 锁。"""
    with _run_lock(run_dir):
        if ack_path(run_dir).exists():
            return
        lifecycle = read_lifecycle(run_dir)
        if lifecycle.get("status") in ("acknowledged", "superseded", "exhausted"):
            return
        if "anomaly" in lifecycle:
            return  # 已记录
        lifecycle["status"] = "unknown"
        lifecycle["anomaly"] = reason
        lifecycle["anomaly_at"] = time.time()
        write_lifecycle(run_dir, lifecycle)


# --- 消费确认 ---


def ack_run(
    task_dir: Path,
    run_id: str,
    parent_session_id: str,
    codex_thread_id: str | None = None,
) -> dict[str, Any]:
    """消费确认 run 完成。

    精确核对父身份、终态和 task 归属。幂等。
    返回 ack 记录，失败抛出 ValueError。
    """
    _validate_run_id(run_id)
    run_dir = task_dir / run_id
    _check_no_symlink_ancestors(run_dir)

    if not run_dir.exists():
        raise ValueError(f"unknown run id: {run_id}")

    # 读取任务
    task_file = run_dir / "task.json"
    if not task_file.exists():
        raise ValueError("task.json not found")
    task = _safe_read_json(task_file)

    # 校验 parent_client
    if task.get("parent_client") != "codex":
        raise ValueError("ack only supported for codex parent tasks")

    # 校验 parent_session_id 匹配保存的任务
    saved_parent_sid = task.get("parent_session_id", "")
    if not saved_parent_sid or not _is_valid_uuid(saved_parent_sid):
        raise ValueError("saved task missing valid parent_session_id")

    if not _is_valid_uuid(parent_session_id):
        raise ValueError(f"invalid parent_session_id: {parent_session_id}")

    if _uuid_lower(parent_session_id) != _uuid_lower(saved_parent_sid):
        raise ValueError("parent_session_id does not match saved task")

    # 校验 CODEX_THREAD_ID
    env_thread_id = codex_thread_id or os.environ.get("CODEX_THREAD_ID", "")
    if not env_thread_id or not _is_valid_uuid(env_thread_id):
        raise ValueError("CODEX_THREAD_ID missing or invalid")

    if _uuid_lower(env_thread_id) != _uuid_lower(parent_session_id):
        raise ValueError("CODEX_THREAD_ID does not match parent_session_id")

    # 校验 task 归属
    if task.get("client") != "qoder":
        raise ValueError("task client is not qoder")

    # 校验 task 必填身份字段
    task_session = task.get("session_id", "")
    task_id = task.get("task_id", "")
    if not task_session or not _is_valid_uuid(task_session):
        raise ValueError("task missing valid session_id")
    if not task_id:
        raise ValueError("task missing task_id")

    # 已消费的异常任务也须幂等，不重新依赖已经变化的进程/终态证据。
    ack_file = ack_path(run_dir)
    if ack_file.exists():
        return _verified_ack(ack_file, task, run_id, parent_session_id)

    # 检查终态
    completion_path = run_dir / "completion.json"
    lifecycle = read_lifecycle(run_dir)
    completion_status = ""

    if completion_path.exists():
        completion = _safe_read_json(completion_path)
        completion_status = completion.get("status", "")

        # 严格校验 completion 身份与 task 一致
        _verify_completion_identity(completion, task, run_id)

        # unknown 状态的 completion 不接受直接 ack - 只接受 worker 死亡异常
        if completion_status == "unknown":
            # 必须有可信的 worker 死亡证据
            pid_file = run_dir / "worker.pid"
            if not pid_file.exists():
                raise ValueError("unknown completion requires worker.pid")
            pid = _safe_read_pid(pid_file)
            if pid is None:
                raise ValueError("cannot verify worker death: invalid pid file")
            if _is_pid_alive(pid):
                raise ValueError("cannot verify worker death: pid still alive")
            # 还需要 anomaly 记录
            if not lifecycle.get("anomaly"):
                raise ValueError("unknown completion requires anomaly record")
    elif lifecycle.get("status") == "unknown" and lifecycle.get("anomaly"):
        # Worker 死亡无 completion - 异常是合法终态
        # 验证 anomaly 记录可信：PID 文件存在且进程已死
        pid_file = run_dir / "worker.pid"
        if not pid_file.exists():
            raise ValueError("anomaly requires worker.pid")
        pid = _safe_read_pid(pid_file)
        if pid is None:
            raise ValueError("cannot verify worker death: invalid pid file")
        if _is_pid_alive(pid):
            raise ValueError("cannot verify worker death: pid still alive")
        completion_status = "unknown"
    else:
        raise ValueError("task not in terminal state")

    if completion_status not in ("finished", "failed", "unknown"):
        raise ValueError(f"task status {completion_status!r} is not terminal")

    # 记录 ack - ack.json 是权威 marker，使用 per-run 锁
    with _run_lock(run_dir):
        if ack_file.exists():
            return _verified_ack(ack_file, task, run_id, parent_session_id)

        ack_data: dict[str, Any] = {
            "run_id": run_id,
            "parent_session_id": parent_session_id,
            "qoder_session_id": task_session,
            "task_id": task_id,
            "completion_status": completion_status,
            "acknowledged_at": time.time(),
        }
        _atomic_write_json(ack_file, ack_data)

        # 更新生命周期 - 如果失败，ack.json 仍是权威 marker
        try:
            lifecycle = read_lifecycle(run_dir)
            # 不覆盖 superseded 状态，但记录 acknowledged
            if lifecycle.get("status") != "superseded":
                lifecycle["status"] = "acknowledged"
            lifecycle["acknowledged_at"] = time.time()
            write_lifecycle(run_dir, lifecycle)
        except OSError:
            # lifecycle 写失败不影响 ack 权威
            pass

    return ack_data


def _verified_ack(
    path: Path, task: dict[str, Any], run_id: str, parent: str
) -> dict[str, Any]:
    """锁内外共用同一完整身份检查，不接受其他 run 的消费记录。"""
    existing = _safe_read_json(path)
    if _uuid_lower(existing.get("parent_session_id", "")) != _uuid_lower(parent):
        raise ValueError("ack already recorded for different parent")
    for field, expected in (
        ("run_id", run_id),
        ("qoder_session_id", task["session_id"]),
        ("task_id", task["task_id"]),
    ):
        if existing.get(field) != expected:
            raise ValueError(f"ack {field} does not match")
    return existing


def _verify_completion_identity(
    completion: dict[str, Any], task: dict[str, Any], run_id: str
) -> None:
    """严格校验 completion 身份与 task 一致。

    所有必填身份与版本字段必须存在并匹配。
    """
    # session_id 必须存在且匹配
    completion_session = completion.get("session_id", "")
    task_session = task.get("session_id", "")
    if not completion_session:
        raise ValueError("completion missing session_id")
    if not task_session:
        raise ValueError("task missing session_id")
    if completion_session != task_session:
        raise ValueError("completion session_id does not match task")

    # task_id 必须存在且匹配
    completion_task_id = completion.get("task_id", "")
    task_id = task.get("task_id", "")
    if not completion_task_id:
        raise ValueError("completion missing task_id")
    if not task_id:
        raise ValueError("task missing task_id")
    if completion_task_id != task_id:
        raise ValueError("completion task_id does not match task")

    # task/change version 必须存在且匹配
    for field in ("task_version", "change_version"):
        completion_value = completion.get(field)
        task_value = task.get(field)
        if completion_value in (None, ""):
            raise ValueError(f"completion missing {field}")
        if task_value in (None, ""):
            raise ValueError(f"task missing {field}")
        if completion_value != task_value:
            raise ValueError(f"completion {field} does not match task")

    # agent_id 必须存在且匹配
    completion_agent_id = completion.get("agent_id", "")
    task_agent_id = task.get("agent_id", "")
    if not completion_agent_id:
        raise ValueError("completion missing agent_id")
    if not task_agent_id:
        raise ValueError("task missing agent_id")
    if completion_agent_id != task_agent_id:
        raise ValueError("completion agent_id does not match task")

    # client 必须是 qoder
    if completion.get("client", "") != "qoder":
        raise ValueError("completion client is not qoder")

    # parent_client 必须存在且匹配
    completion_parent = completion.get("parent_client", "")
    task_parent = task.get("parent_client", "")
    if not completion_parent:
        raise ValueError("completion missing parent_client")
    if not task_parent:
        raise ValueError("task missing parent_client")
    if completion_parent != task_parent:
        raise ValueError("completion parent_client does not match task")

    # parent_session_id 必须存在且匹配
    completion_parent_sid = completion.get("parent_session_id", "")
    task_parent_sid = task.get("parent_session_id", "")
    if not completion_parent_sid:
        raise ValueError("completion missing parent_session_id")
    if not task_parent_sid:
        raise ValueError("task missing parent_session_id")
    if _uuid_lower(completion_parent_sid) != _uuid_lower(task_parent_sid):
        raise ValueError("completion parent_session_id does not match task")

    # run_id 必须存在并同时匹配 task 与 run directory
    completion_run_id = completion.get("run_id", "")
    task_run_id = task.get("run_id", "")
    if not completion_run_id:
        raise ValueError("completion missing run_id")
    if not task_run_id:
        raise ValueError("task missing run_id")
    if completion_run_id != run_id:
        raise ValueError("completion run_id does not match run directory")
    if completion_run_id != task_run_id:
        raise ValueError("completion run_id does not match task")


def _uuid_lower(value: str) -> str:
    """统一小写比较 UUID。"""
    return value.lower() if isinstance(value, str) else ""


# --- 替代链 ---


def record_supersession(old_run_dir: Path, new_run_id: str) -> None:
    """标记旧 run 已被新 run 替代。

    消费和替代是独立维度：已 ack 仍记录 next_run_id。
    使用 per-run 锁防止并发修改。已有 next_run_id 时拒绝（不覆盖）。
    """
    with _run_lock(old_run_dir):
        lifecycle = read_lifecycle(old_run_dir)

        # 已有 next_run_id 时拒绝，防止重复或冲突 resume
        existing_next = lifecycle.get("next_run_id", "")
        if existing_next and existing_next != new_run_id:
            raise ValueError(
                f"run already has next_run_id={existing_next}, refusing to overwrite with {new_run_id}"
            )

        # 记录 next_run_id
        lifecycle["next_run_id"] = new_run_id
        lifecycle["superseded_at"] = time.time()

        # 只有未 superseded 时才更新状态（保留 ack 状态）
        if lifecycle.get("status") != "superseded":
            lifecycle["status"] = "superseded"

        write_lifecycle(old_run_dir, lifecycle)


def record_previous_run(new_run_dir: Path, previous_run_id: str) -> None:
    """在新 run 的生命周期中记录前一个 run。使用 per-run 锁。"""
    _validate_run_id(previous_run_id)
    with _run_lock(new_run_dir):
        lifecycle = read_lifecycle(new_run_dir)
        previous = lifecycle.get("previous_run_id")
        if previous and previous != previous_run_id:
            raise ValueError("previous run already recorded")
        lifecycle["previous_run_id"] = previous_run_id
        write_lifecycle(new_run_dir, lifecycle)


def has_next_run(old_run_dir: Path) -> bool:
    """检查旧 run 是否已有后续 run。"""
    lifecycle = read_lifecycle(old_run_dir)
    return bool(lifecycle.get("next_run_id"))


# --- 等待启动标记 ---


def wait_for_started(
    run_dir: Path,
    timeout: float = 2.0,
    poll_interval: float = 0.1,
    sleep_fn: Callable = time.sleep,
    clock_fn: Callable = time.monotonic,
) -> bool:
    """等待 worker 写入 started.json。

    返回是否在超时前检测到启动确认。
    """
    start = clock_fn()
    path = started_path(run_dir)
    while True:
        if path.exists():
            return True
        elapsed = clock_fn() - start
        if elapsed >= timeout:
            return False
        sleep_fn(poll_interval)


# --- _watchdog 子命令入口 ---


def main(argv: list[str] | None = None) -> int:
    """生命周期模块入口。"""
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "_watchdog":
        if len(argv) != 4:
            print(
                "usage: _watchdog <task_dir> <run_id> <repo_root>",
                file=sys.stderr,
            )
            return 1
        try:
            return watchdog_entry(argv[1], argv[2], argv[3])
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    print("error: unknown command", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
