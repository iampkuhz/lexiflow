"""共享执行预算与取消边界；只回收本次 Check 的进程组，不处理未知进程。"""

from __future__ import annotations

import contextlib
import signal
import threading
import time


class Interrupted(BaseException):
    """让预算耗尽或用户取消穿过普通异常捕获，不能被解释为检查成功。"""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        super().__init__(detail or reason)


@contextlib.contextmanager
def bounded(seconds: int):
    """限定整个调用的墙钟预算，返回单调时钟截止点。

    seconds 为 1..7200 的整数，包含冻结、预检和所有命令；子命令还受自身
    timeout 限制。只允许 POSIX 主线程且没有既有计时器，避免覆盖外层预算。
    退出时恢复信号处理器；正在执行的进程组由 Verification 内核清理。
    """
    if type(seconds) is not int or not 1 <= seconds <= 7200:
        raise ValueError("execution budget must be an integer in 1..7200")
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("execution budget requires the main thread")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ValueError("execution budget cannot replace an active outer timer")

    def interrupt(signum, _frame):
        raise Interrupted(
            "budget-exhausted" if signum == signal.SIGALRM else "cancelled",
            f"总执行超过 {seconds} 秒" if signum == signal.SIGALRM else "执行被中断",
        )

    signals = (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.getsignal(sig) for sig in signals}
    deadline = time.monotonic() + seconds
    try:
        for sig in signals:
            signal.signal(sig, interrupt)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield deadline
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)
