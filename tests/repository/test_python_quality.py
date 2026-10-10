"""Python Check 的状态、只读命令和完整性合同。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from unittest import mock

from scripts.checks.repository.python import check_python_quality as python_quality


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
            mock.patch(
                "scripts.checks.repository.python.check_python_quality.run", return_value=report
            ) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(python_quality.main([]), 0)
        self.assertEqual(len(run.call_args.args), 1)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            python_quality.main(["--docstrings"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "requirements-dev.txt").write_text("ruff==0.16.8\n")
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
        result = python_quality._run_ruff(self.root)
        self.assertEqual(("PASS", 2), (result["status"], result["checks_run"]))
        self.assertEqual(2, run.call_count)
        for call in run.call_args_list:
            self.assertEqual(
                [python_quality.sys.executable, "-m", "ruff"], call.args[0][:3]
            )
            self.assertNotIn("--fix", call.args[0])
            command = call.args[0]
            self.assertEqual(
                str(self.root / "config/engineering/python-quality.toml"),
                command[command.index("--config") + 1],
            )
            self.assertIn("--no-cache", command)
            self.assertEqual(list(python_quality.PYTHON_SOURCE_ROOTS), command[-3:])
            self.assertNotIn("ops/docker", command)
            self.assertEqual(60, call.kwargs["timeout"])
        self.assertIn("--check", run.call_args_list[1].args[0])

    @mock.patch.object(python_quality.subprocess, "run")
    def test_ruff_pin_uses_dependency_source_without_config_copy(self, run):
        """安装版本必须匹配唯一依赖锁，不需要另维护 Ruff 配置字段。"""
        with mock.patch.object(python_quality.importlib.metadata, "version", return_value="0.0.0"):
            result = python_quality._run_ruff(self.root)
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("ruff-version-mismatch", result["reason"])
        run.assert_not_called()

    @mock.patch.object(python_quality.subprocess, "run")
    def test_missing_or_ambiguous_dependency_pin_cannot_run_ruff(self, run):
        """缺失或重复锁不能退回任意安装版本。"""
        for content in (None, "", "ruff==0.16.8\nruff==0.16.8\n"):
            with self.subTest(content=content):
                path = self.root / "requirements-dev.txt"
                if content is None:
                    path.unlink()
                else:
                    path.write_text(content)
                result = python_quality._run_ruff(self.root)
                self.assertEqual("BLOCKED", result["status"])
                self.assertEqual("ruff-version-unavailable", result["reason"])
        run.assert_not_called()

    @mock.patch.object(python_quality.subprocess, "run")
    def test_lint_or_format_failure_fails_delivery(self, run):
        for codes in ((1, 0), (0, 1), (2, 0)):
            with self.subTest(codes=codes):
                run.side_effect = [
                    subprocess.CompletedProcess([], code, "diagnostic", "")
                    for code in codes
                ]
                result = python_quality._run_ruff(self.root)
                self.assertEqual("FAIL", result["status"])
                self.assertEqual(2, result["checks_run"])

    @mock.patch.object(python_quality.subprocess, "run")
    def test_missing_tool_is_blocked_without_installing(self, run):
        with mock.patch.object(
            python_quality.importlib.util, "find_spec", return_value=None
        ):
            self.assertEqual("BLOCKED", python_quality._run_ruff(self.root)["status"])
        run.assert_not_called()

    @mock.patch.object(python_quality.subprocess, "run")
    def test_timeout_is_blocked(self, run):
        run.side_effect = subprocess.TimeoutExpired("ruff", 60)
        self.assertEqual("BLOCKED", python_quality._run_ruff(self.root)["status"])
        self.assertEqual(1, run.call_count)

    @mock.patch.object(python_quality.subprocess, "run")
    def test_empty_source_set_is_not_pass(self, run):
        (self.root / "scripts/example.py").unlink()
        self.assertEqual("FAIL", python_quality._run_ruff(self.root)["status"])
        run.assert_not_called()

    @mock.patch.object(python_quality.subprocess, "run")
    def test_docstrings_scan_engineering_sources_without_tests(self, run):
        """正式文档门禁扫描工程生产脚本而非普通测试，诊断仍由 Pylint 原生结果定位。"""
        run.return_value = subprocess.CompletedProcess([], 0, "[]", "")
        result = python_quality._run_docstrings(self.root)
        self.assertEqual(("PASS", 1), (result["status"], result["checks_run"]))
        command = run.call_args.args[0]
        self.assertEqual(list(python_quality.PYTHON_SOURCE_ROOTS), command[-3:])
        self.assertNotIn("ops/docker", command)
        self.assertIn("--load-plugins=scripts.checks.repository.python.chinese_docstring_rule", command)
        self.assertNotIn("tests", command)
        self.assertEqual(
            python_quality.os.devnull, command[command.index("--rcfile") + 1]
        )
        self.assertIn("--persistent=n", command)
        self.assertIn("--jobs=1", command)
        self.assertIn("--output-format=json", command)

    @mock.patch.object(python_quality.subprocess, "run")
    def test_ops_sources_keep_checks_nonempty(self, run):
        """移出 scripts 的生产脚本仍属于完整检查集合。"""
        (self.root / "scripts/example.py").unlink()
        path = self.root / "ops/dataset/source.py"
        path.parent.mkdir(parents=True)
        path.write_text('"""合成数据准备脚本。"""\n')
        run.return_value = subprocess.CompletedProcess([], 0, "[]", "")
        self.assertEqual("PASS", python_quality._run_ruff(self.root)["status"])
        self.assertEqual("PASS", python_quality._run_docstrings(self.root)["status"])

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
        result = python_quality._run_docstrings(self.root)
        self.assertEqual("FAIL", result["status"])
        self.assertEqual([finding], result["detail"])

    @mock.patch.object(python_quality.subprocess, "run")
    def test_docstring_tool_error_or_invalid_result_fails_closed(self, run):
        for exit_code, stdout, stderr in (
            (0, "[]", "unexpected tool error"),
            (32, "[]", ""),
            (0, "{}", ""),
            (0, '[{"message": "incomplete"}]', ""),
            (0, "invalid json", ""),
        ):
            with self.subTest(stdout=stdout, exit_code=exit_code):
                run.return_value = subprocess.CompletedProcess(
                    [], exit_code, stdout, stderr
                )
                self.assertEqual(
                    "FAIL", python_quality._run_docstrings(self.root)["status"]
                )

    def test_complete_contract_failure_precedes_blocked(self):
        base = {"checks_run": 1, "failures": 0, "errors": 0, "skipped": 0}
        for style_status, doc_status, expected in (
            ("PASS", "PASS", "PASS"),
            ("BLOCKED", "PASS", "BLOCKED"),
            ("BLOCKED", "FAIL", "FAIL"),
        ):
            with (
                mock.patch.object(
                    python_quality,
                    "_run_ruff",
                    return_value={**base, "status": style_status},
                ),
                mock.patch.object(
                    python_quality,
                    "_run_docstrings",
                    return_value={**base, "status": doc_status},
                ),
            ):
                self.assertEqual(expected, python_quality.run(self.root)["status"])


if __name__ == "__main__":
    unittest.main()
