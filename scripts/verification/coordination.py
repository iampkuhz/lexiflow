"""协调同一 checkout 的验证运行；不锁定源码编辑或其他写入者。"""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
import threading
import weakref
from collections.abc import Iterator
from pathlib import Path

from scripts.verification.reports import _open_directory

# 仅登记本进程实际成功取得 flock 的 holder；公开构造器/伪造 fd 不授予窗口。
_HELD_WINDOWS: weakref.WeakSet = weakref.WeakSet()


class WindowError(RuntimeError):
    """表示验证窗口不能安全取得或持续维持。"""

    status = "BLOCKED"

    def __init__(self, detail: str, *, code: str = "verification-window-unsafe"):
        self.code = code
        super().__init__(detail)


class VerificationWindow:
    """显式传递一个仍由当前调用线程持有的窗口；不隐式允许同进程并发重入。"""

    def __init__(self, root: Path, directory_fd: int, lock_fd: int):
        self.root = root
        self._directory_fd = directory_fd
        self._lock_fd = lock_fd
        self._owner = (os.getpid(), threading.get_ident())
        self._active = True

    def check(self, root: Path) -> None:
        """每次复用前核对存活、checkout、进程线程和持有路径，拒绝外传或过期窗口。"""
        if (
            self not in _HELD_WINDOWS
            or not self._active
            or Path(root).resolve() != self.root
            or self._owner != (os.getpid(), threading.get_ident())
        ):
            raise WindowError("窗口已失效或不属于当前 checkout/调用线程")
        _ensure_current(self.root, self._directory_fd, self._lock_fd)


@contextlib.contextmanager
def verification_window(root: Path) -> Iterator[VerificationWindow]:
    """在 checkout 内持有排他非阻塞验证锁，不保证源码在验证期间静止。"""
    root = Path(root).resolve()
    try:
        directory_fd = _open_directory(
            root, ("tmp", "quality", "verification-window"), create=True
        )
    except (OSError, ValueError) as exc:
        raise WindowError(f"验证窗口目录不安全：{exc}") from None

    lock_fd = None
    window = None
    try:
        directory_info = os.fstat(directory_fd)
        if directory_info.st_uid != os.getuid() or directory_info.st_mode & (
            stat.S_IWGRP | stat.S_IWOTH
        ):
            raise WindowError("验证窗口目录必须由当前用户拥有且不可被组或其他用户写入")
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            lock_fd = os.open("execution.lock", flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            raise WindowError(f"验证锁不可安全打开：{exc}") from None
        info = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise WindowError(
                "验证锁必须是当前用户拥有且不可被组或其他用户写入的普通单链接文件"
            )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WindowError(
                "同 checkout 已有验证运行持有窗口", code="verification-window-busy"
            ) from None
        _ensure_current(root, directory_fd, lock_fd)
        window = VerificationWindow(root, directory_fd, lock_fd)
        _HELD_WINDOWS.add(window)
        yield window
        _ensure_current(root, directory_fd, lock_fd)
    except WindowError:
        raise
    except OSError as exc:
        raise WindowError(f"验证窗口状态不可确认：{exc}") from None
    finally:
        if window is not None:
            _HELD_WINDOWS.discard(window)
            window._active = False
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        os.close(directory_fd)


def _ensure_current(root: Path, held_directory_fd: int, lock_fd: int) -> None:
    """拒绝路径或锁文件被替换；绝不按锁内容/PID清理文件。"""
    try:
        current_directory = _open_directory(
            root, ("tmp", "quality", "verification-window")
        )
    except (OSError, ValueError) as exc:
        raise WindowError(f"验证窗口路径漂移：{exc}") from None
    try:
        held_dir = os.fstat(held_directory_fd)
        current_dir = os.fstat(current_directory)
        if current_dir.st_uid != os.getuid() or current_dir.st_mode & 0o022:
            raise WindowError("验证窗口目录属性已变化")
        if (held_dir.st_dev, held_dir.st_ino) != (
            current_dir.st_dev,
            current_dir.st_ino,
        ):
            raise WindowError("验证窗口目录已被替换")
        try:
            current_lock = os.open(
                "execution.lock",
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=current_directory,
            )
        except OSError as exc:
            raise WindowError(f"验证锁路径已变化：{exc}") from None
        try:
            held = os.fstat(lock_fd)
            current = os.fstat(current_lock)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_uid != os.getuid()
                or current.st_nlink != 1
                or current.st_mode & 0o022
            ):
                raise WindowError("验证锁安全属性已变化")
            if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
                raise WindowError("验证锁文件已被替换")
        finally:
            os.close(current_lock)
    finally:
        os.close(current_directory)
