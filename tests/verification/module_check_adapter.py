#!/usr/bin/env python3
"""将 verification 模块测试结果转换为门禁消费的结构化协议。

测试数量、失败和跳过判定归测试模块；内核只校验 JSON 协议，
不依赖 unittest 的内部实现，也不把进程退出零当作测试通过。
"""
from __future__ import annotations

import argparse
import io
import json
import unittest
from pathlib import Path


def verification_tests(root: Path) -> dict[str, object]:
    """发现并执行本模块测试；空集合、失败、异常或跳过均不得返回 PASS。"""
    suite = unittest.defaultTestLoader.discover(str(root / "tests" / "verification"), pattern="test_*.py", top_level_dir=str(root))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return {
        "status": "PASS" if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else "FAIL",
        "checks_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "reason": "" if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else "verification-module-tests-incomplete",
        "tool_output_sha256": __import__("hashlib").sha256(stream.getvalue().encode("utf-8")).hexdigest(),
    }


def main() -> int:
    """以仓库根运行模块检查并输出 JSON，实际通过状态由协议字段表达。"""
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = verification_tests(Path(__file__).resolve().parents[2])
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
