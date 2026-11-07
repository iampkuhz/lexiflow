#!/usr/bin/env python3
"""使用标准 unittest 发现工程工具测试，并输出唯一的完整性与诊断协议。

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
            self.per_test[self._current_id] = round(time.monotonic() - self._start, 6)
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


SUITES = {
    "verification": "tests/verification",
    "acceptance": "tests/delivery_gate",
    "agents": "tests/agents",
    "repository": "tests/repository",
    "release": "tests/release",
}


class _DiagnosticStream(io.StringIO):
    """即时保留本机诊断；stdout 只用于结构化结果，摘要不复制 traceback。"""

    def write(self, text: str) -> int:
        """透传标准 unittest 输出，异常与超时前的用例进度均可定位。"""
        count = super().write(text)
        sys.stderr.write(text)
        sys.stderr.flush()
        return count


def evaluate_suite(suite: unittest.TestSuite) -> dict[str, object]:
    """共享工具测试完整性判据；空、未完成、失败、异常和跳过均失败关闭。"""
    total_started = time.monotonic()
    expected = suite.countTestCases()
    stream = _DiagnosticStream()
    timed_result = unittest.TextTestRunner(
        stream=stream, verbosity=2, resultclass=_TimedResult
    ).run(suite)
    python_duration = round(time.monotonic() - total_started, 6)
    per_test = timed_result.per_test
    passed = (
        timed_result.wasSuccessful()
        and timed_result.testsRun == expected > 0
        and not timed_result.skipped
    )
    report: dict[str, object] = {
        "status": "PASS" if passed else "FAIL",
        "checks_run": timed_result.testsRun,
        "expected_tests": expected,
        "failures": len(timed_result.failures),
        "errors": len(timed_result.errors),
        "skipped": len(timed_result.skipped),
        "python_unittest_duration_seconds": python_duration,
        "reason": "" if passed else "python-tool-tests-incomplete",
        "detail": {
            "failed_tests": [test.id() for test, _ in timed_result.failures],
            "error_tests": [test.id() for test, _ in timed_result.errors],
            "skipped_tests": [test.id() for test, _ in timed_result.skipped],
        },
        "tool_output_sha256": hashlib.sha256(
            stream.getvalue().encode("utf-8")
        ).hexdigest(),
    }
    if per_test:
        report["per_test_timing"] = per_test
        if passed:
            report["top_slow_tests"] = _top_slow(per_test)
    return report


def run(root: Path, suite_name: str) -> dict[str, object]:
    """由固定 suite 归属发现完整测试集合，不接受任意命令或排除列表。"""
    started = time.monotonic()
    suite = unittest.TestLoader().discover(
        str(root / SUITES[suite_name]), pattern="test_*.py", top_level_dir=str(root)
    )
    report = evaluate_suite(suite)
    report["python_unittest_duration_seconds"] = round(time.monotonic() - started, 6)
    return report


def main(argv: list[str] | None = None) -> int:
    """以唯一标准入口运行工具测试，实际结论由 JSON 协议字段表达。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=tuple(SUITES), required=True)
    args = parser.parse_args(argv)
    report = run(Path(__file__).resolve().parents[3], args.suite)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
