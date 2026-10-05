#!/usr/bin/env python3
"""将 verification 模块测试结果转换为门禁消费的结构化协议。

测试数量、失败和跳过判定归测试模块；内核只校验 JSON 协议，
不依赖 unittest 的内部实现，也不把进程退出零当作测试通过。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
import unittest
from pathlib import Path


class _TimedResult(unittest.TextTestResult):
    """记录每用例的单调计时；保持 TextTestResult 的 verbosity 和诊断输出。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.per_test: dict[str, float] = {}
        self._start: float | None = None
        self._current_id: str | None = None

    def startTest(self, test):
        super().startTest(test)
        self._current_id = test.id()
        self._start = time.monotonic()

    def stopTest(self, test):
        if self._start is not None and self._current_id is not None:
            self.per_test[self._current_id] = round(
                time.monotonic() - self._start, 6
            )
        self._start = None
        self._current_id = None
        super().stopTest(test)


def _top_slow(per_test: dict[str, float], limit: int = 10) -> list[dict[str, float]]:
    """成功时只输出前 N 慢用例摘要，避免长期噪声。"""
    return [
        {"test": name, "seconds": seconds}
        for name, seconds in sorted(
            per_test.items(), key=lambda item: item[1], reverse=True
        )[:limit]
    ]


def verification_tests(root: Path) -> dict[str, object]:
    """发现并执行本模块测试；空集合、失败、异常或跳过均不得返回 PASS。"""
    total_started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(root / "tests" / "verification"), pattern="test_*.py", top_level_dir=str(root))
    stream = io.StringIO()
    timed_result = unittest.TextTestRunner(
        stream=stream, verbosity=2, resultclass=_TimedResult
    ).run(suite)
    python_duration = round(time.monotonic() - total_started, 6)
    # Kernel 已为 stderr 提供受控日志 locator，失败时无需再跑测试来取得 traceback。
    for test, trace in [*timed_result.failures, *timed_result.errors]:
        print(f"FAILED TEST: {test.id()}\n{trace}", file=sys.stderr)
    per_test = timed_result.per_test
    passed = timed_result.wasSuccessful() and timed_result.testsRun > 0 and not timed_result.skipped
    report: dict[str, object] = {
        "status": "PASS" if passed else "FAIL",
        "checks_run": timed_result.testsRun,
        "failures": len(timed_result.failures),
        "errors": len(timed_result.errors),
        "skipped": len(timed_result.skipped),
        "python_unittest_duration_seconds": python_duration,
        "reason": "" if passed else "verification-module-tests-incomplete",
        "detail": {
            "failed_tests": [test.id() for test, _ in timed_result.failures],
            "error_tests": [test.id() for test, _ in timed_result.errors],
            "skipped_tests": [test.id() for test, _ in timed_result.skipped],
        },
        "tool_output_sha256": hashlib.sha256(stream.getvalue().encode("utf-8")).hexdigest(),
    }
    if per_test:
        report["per_test_timing"] = per_test
        if passed:
            report["top_slow_tests"] = _top_slow(per_test)
    return report


def main() -> int:
    """以仓库根运行模块检查并输出 JSON，实际通过状态由协议字段表达。"""
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = verification_tests(Path(__file__).resolve().parents[2])
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
