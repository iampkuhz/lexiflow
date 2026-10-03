#!/usr/bin/env python3
"""执行 Docker 封装合同测试；不代表容器生命周期或目标平台验收。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path


class DiagnosticStream(io.StringIO):
    """即时输出合成测试诊断，同时保留完整摘要输入。"""

    def write(self, text: str) -> int:
        """逐段透传并刷新，超时前已执行部分不会丢失。"""
        written = super().write(text)
        sys.stderr.write(text)
        sys.stderr.flush()
        return written


def run_tests() -> dict[str, object]:
    """真实发现并执行本目录测试，零测试和任何跳过均失败关闭。"""
    suite = unittest.defaultTestLoader.discover(
        str(Path(__file__).resolve().parent), pattern="test_*.py"
    )
    stream = DiagnosticStream()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    root = Path(__file__).resolve().parents[3]
    runner = (
        "import {runTests} from './ops/release/check.mjs';"
        "const r=await runTests({args:['--test','--test-reporter=tap',...process.argv.slice(1)]});"
        "console.log(JSON.stringify(r)); if(r.status!=='PASS') process.exitCode=1;"
    )
    node_suite = subprocess.run(
        ["node", "--input-type=module", "-e", runner,
         *map(str, sorted((root / "ops/podman/tests").glob("*.test.mjs")))],
        cwd=root, capture_output=True, text=True, check=False, timeout=180,
    )
    node_output = node_suite.stdout + node_suite.stderr
    sys.stderr.write(node_output)
    sys.stderr.flush()
    try:
        node_report = json.loads(node_suite.stdout)
    except ValueError:
        node_report = {"status": "FAIL", "checks_run": 0, "errors": 1}
    node_ok = node_suite.returncode == 0 and node_report.get("status") == "PASS"
    output = stream.getvalue() + node_output
    passed = result.wasSuccessful() and result.testsRun > 0 and not result.skipped and node_ok
    return {
        "status": "PASS" if passed else "FAIL",
        "checks_run": result.testsRun + node_report.get("checks_run", 0),
        "failures": len(result.failures) + (0 if node_ok else 1),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "node_transaction_exit_code": node_suite.returncode,
        "node_transaction_no_skips": node_ok,
        "reason": "" if passed else "docker-contract-tests-incomplete",
        "tool_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    """公开无参数封装检查，向 Verify 返回准确结果和非零失败退出码。"""
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = run_tests()
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
