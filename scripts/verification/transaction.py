"""单次多视图 Verification 调用中的进程内等价执行台账。"""

from __future__ import annotations

import os
import threading
import uuid
import weakref
from pathlib import Path
from typing import Any

from scripts.verification.coordination import VerificationWindow


_ACTIVE: weakref.WeakSet = weakref.WeakSet()


class VerificationTransaction:
    """仅在同一个真实窗口、进程和线程内存活的执行台账。"""

    def __init__(self, root: Path, window: VerificationWindow):
        self.root = root.resolve()
        self.window = window
        self.owner = (os.getpid(), threading.get_ident())
        self.nonce = str(uuid.uuid4())
        self.executions: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.active = True

    def check(self, root: Path, window: VerificationWindow) -> None:
        """每次取用台账前核对单次调用的窗口及线程身份。"""
        if (
            self not in _ACTIVE
            or not self.active
            or self.owner != (os.getpid(), threading.get_ident())
            or self.root != root.resolve()
            or self.window is not window
        ):
            raise ValueError("verification transaction is not active in this call")
        window.check(root)

    def close(self) -> None:
        """销毁本轮可复用结果，禁止跨调用持久化。"""
        self.active = False
        self.executions.clear()
        self.operations.clear()
        _ACTIVE.discard(self)


def _begin(root: Path, window: VerificationWindow) -> VerificationTransaction:
    """注册当前调用取得的台账；不接受调用者传入的历史状态。"""
    window.check(root)
    transaction = VerificationTransaction(root, window)
    _ACTIVE.add(transaction)
    return transaction
