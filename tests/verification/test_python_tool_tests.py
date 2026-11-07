"""统一工具测试入口的完整性、诊断和归属回归。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.checks.tool_tests import run_python_tool_tests as adapter


class PythonToolTests(unittest.TestCase):
    """原始诊断只进入本机日志；结构化结果不能把未执行当作通过。"""

    def evaluate(self, case):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            result = adapter.evaluate_suite(unittest.TestSuite(case))
        return result, output.getvalue()

    def test_all_terminal_paths_keep_original_diagnostic(self):
        class Case(unittest.TestCase):
            def test_ok(self):
                pass

            def test_failure(self):
                self.fail("synthetic assertion detail")

            def test_error(self):
                raise RuntimeError("synthetic exception detail")

            def test_skip(self):
                self.skipTest("synthetic skip reason")

        for name, status, diagnostic in (
            ("test_ok", "PASS", "OK"),
            ("test_failure", "FAIL", "AssertionError: synthetic assertion detail"),
            ("test_error", "FAIL", "RuntimeError: synthetic exception detail"),
            ("test_skip", "FAIL", "synthetic skip reason"),
        ):
            with self.subTest(case=name):
                result, output = self.evaluate([Case(name)])
                self.assertEqual(status, result["status"])
                self.assertEqual(1, result["expected_tests"])
                self.assertIn(diagnostic, output)
                self.assertNotIn(diagnostic, json.dumps(result))
                self.assertIn(Case(name).id(), result["per_test_timing"])

    def test_empty_and_early_stopped_suite_cannot_pass(self):
        class Stop(unittest.TestCase):
            def run(self, result=None):
                super().run(result)
                result.stop()

            def test_stop(self):
                pass

        empty, _ = self.evaluate([])
        stopped, _ = self.evaluate([Stop("test_stop"), Stop("test_stop")])
        self.assertEqual("FAIL", empty["status"])
        self.assertEqual("FAIL", stopped["status"])
        self.assertEqual(2, stopped["expected_tests"])
        self.assertEqual(1, stopped["checks_run"])

    def test_discovery_import_error_is_visible_and_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / adapter.SUITES["release"]
            target.mkdir(parents=True)
            for name in ("tests", "tests/release"):
                (root / name / "__init__.py").touch()
            (target / "test_broken.py").write_text("raise RuntimeError('discovery cause')\n")
            # Isolate package naming from the repository's already imported tests.
            loader = unittest.TestLoader()
            suite = loader.discover(str(target))
            with contextlib.redirect_stderr(io.StringIO()) as output:
                result = adapter.evaluate_suite(suite)
            self.assertEqual("FAIL", result["status"])
            self.assertEqual(1, result["errors"])
            self.assertIn("RuntimeError: discovery cause", output.getvalue())


if __name__ == "__main__":
    unittest.main()
