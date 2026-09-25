"""运行 Agent 模块的完整回归测试并输出 JSON 结果。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import unittest
from pathlib import Path


def evaluate_suite(suite: unittest.TestSuite) -> dict[str, object]:
    """将 Agent 模块测试转成结构化结果；零测试或跳过不能 PASS。"""
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
        "reason": "" if passed else "agent-module-tests-incomplete",
        "tool_output_sha256": hashlib.sha256(stream.getvalue().encode()).hexdigest(),
    }


def run(root: Path) -> dict[str, object]:
    """执行 Agent 工具自身的回归测试，不判断被委派任务质量。"""
    suite = unittest.defaultTestLoader.discover(
        str(root / "tests" / "agents"), pattern="test_*.py", top_level_dir=str(root)
    )
    return evaluate_suite(suite)


def main() -> int:
    """输出 Agent 模块测试的 Gate JSON；不启动或验收 Agent。"""
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(
        json.dumps(
            run(Path(__file__).resolve().parents[2]), ensure_ascii=False, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
