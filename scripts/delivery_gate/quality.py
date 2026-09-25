"""运行 Delivery Gate 模块的完整回归测试并输出 JSON 结果。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path


def evaluate_suite(suite: unittest.TestSuite) -> dict[str, object]:
    """把正式Delivery Gate 模块测试转成 Gate JSON，零测试或跳过不能 PASS。"""
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    complete = result.testsRun > 0 and not result.skipped
    passed = result.wasSuccessful() and complete
    return {
        "status": "PASS" if passed else "FAIL",
        "checks_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "reason": "" if passed else "delivery-gate-module-tests-incomplete",
        "tool_output_sha256": hashlib.sha256(stream.getvalue().encode()).hexdigest(),
    }


def run(root: Path) -> dict[str, object]:
    """执行验收工具自身的测试，不签发 validation 或 review。"""
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests" / "delivery_gate"),
        pattern="test_*.py",
        top_level_dir=str(root),
    )
    return evaluate_suite(suite)


def main() -> int:
    """输出验收工具回归测试结果，供 Harness 消费。"""
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(
        json.dumps(
            run(Path(__file__).resolve().parents[2]), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
