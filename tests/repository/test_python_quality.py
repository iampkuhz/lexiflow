"""Python Check 的状态、只读命令和完整性合同。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest import mock

from scripts.repository import python_quality


class PythonQualityTest(unittest.TestCase):
    def test_public_cli_runs_full_contract_without_docstring_switch(self):
        """人工零参数入口与 Verify 使用同一 Ruff/Pylint 聚合。"""
        report = {
            "status": "PASS",
            "checks_run": 3,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }
        with (
            mock.patch("scripts.repository.quality.run", return_value=report) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(python_quality.main([]), 0)
        self.assertEqual(run.call_args.args[1], "python")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            python_quality.main(["--docstrings"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts/example.py").write_text('"""合成测试模块。"""\n')
        self.spec = mock.patch.object(
            python_quality.importlib.util, "find_spec", return_value=object()
        )
        self.spec.start()
        self.addCleanup(self.spec.stop)

    @mock.patch.object(python_quality.subprocess, "run")
    def test_both_tools_are_read_only_and_use_same_interpreter(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "ok", "")
        result = python_quality.run(self.root)
        self.assertEqual(("PASS", 2), (result["status"], result["checks_run"]))
        self.assertEqual(2, run.call_count)
        for call in run.call_args_list:
            self.assertEqual(
                [python_quality.sys.executable, "-m", "ruff"], call.args[0][:3]
            )
            self.assertNotIn("--fix", call.args[0])
            self.assertEqual(60, call.kwargs["timeout"])
        self.assertIn("--check", run.call_args_list[1].args[0])

    @mock.patch.object(python_quality.subprocess, "run")
    def test_lint_or_format_failure_fails_delivery(self, run):
        for codes in ((1, 0), (0, 1), (2, 0)):
            with self.subTest(codes=codes):
                run.side_effect = [
                    subprocess.CompletedProcess([], code, "diagnostic", "")
                    for code in codes
                ]
                result = python_quality.run(self.root)
                self.assertEqual("FAIL", result["status"])
                self.assertEqual(2, result["checks_run"])

    @mock.patch.object(python_quality.subprocess, "run")
    def test_missing_tool_is_blocked_without_installing(self, run):
        with mock.patch.object(
            python_quality.importlib.util, "find_spec", return_value=None
        ):
            self.assertEqual("BLOCKED", python_quality.run(self.root)["status"])
        run.assert_not_called()

    @mock.patch.object(python_quality.subprocess, "run")
    def test_timeout_is_blocked(self, run):
        run.side_effect = subprocess.TimeoutExpired("ruff", 60)
        self.assertEqual("BLOCKED", python_quality.run(self.root)["status"])
        self.assertEqual(1, run.call_count)

    @mock.patch.object(python_quality.subprocess, "run")
    def test_empty_source_set_is_not_pass(self, run):
        (self.root / "scripts/example.py").unlink()
        self.assertEqual("FAIL", python_quality.run(self.root)["status"])
        run.assert_not_called()

    @mock.patch.object(python_quality.subprocess, "run")
    def test_docstrings_scan_scripts_without_tests(self, run):
        """正式文档门禁只约束 scripts，诊断仍由 Pylint 原生结果定位。"""
        run.return_value = subprocess.CompletedProcess([], 0, "[]", "")
        result = python_quality.run_docstrings(self.root)
        self.assertEqual(("PASS", 1), (result["status"], result["checks_run"]))
        command = run.call_args.args[0]
        self.assertIn("scripts", command)
        self.assertNotIn("tests", command)
        self.assertIn("harness/python-docstrings.toml", " ".join(command))

    @mock.patch.object(python_quality.subprocess, "run")
    def test_docstring_finding_blocks_python_delivery(self, run):
        """缺少说明时保留真实诊断和文件位置，不能用退出码零冒充 PASS。"""
        finding = {
            "path": "scripts/example.py",
            "line": 1,
            "message-id": "C9001",
            "message": "缺少中文文档",
        }
        import json

        run.return_value = subprocess.CompletedProcess([], 2, json.dumps([finding]), "")
        result = python_quality.run_docstrings(self.root)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual([finding], result["detail"])


if __name__ == "__main__":
    unittest.main()
