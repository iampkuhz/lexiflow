from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from scripts.checks.repository.run_repository_check_group import evaluate_suite, run


class RepositoryQualityResultTest(unittest.TestCase):
    def test_empty_suite_cannot_pass(self) -> None:
        self.assertEqual("FAIL", evaluate_suite(unittest.TestSuite())["status"])

    def test_skipped_test_cannot_pass(self) -> None:
        class Skipped(unittest.TestCase):
            @unittest.skip("fixture")
            def test_skip(self) -> None:
                pass

        report = evaluate_suite(
            unittest.defaultTestLoader.loadTestsFromTestCase(Skipped)
        )
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["skipped"])
        self.assertTrue(
            report["detail"]["skipped_tests"][0].endswith("Skipped.test_skip")
        )

    def test_failure_preserves_test_identity_without_traceback(self) -> None:
        class Broken(unittest.TestCase):
            def test_failure(self) -> None:
                self.fail("private fixture detail")

        report = evaluate_suite(
            unittest.defaultTestLoader.loadTestsFromTestCase(Broken)
        )
        self.assertEqual("FAIL", report["status"])
        self.assertTrue(
            report["detail"]["failed_tests"][0].endswith("Broken.test_failure")
        )
        self.assertNotIn("private fixture detail", str(report))

    def test_documentation_failure_preserves_location(self) -> None:
        issues = ["docs/demo.md: 失效链接：missing.md"]
        with mock.patch(
            "scripts.checks.repository.run_repository_check_group.docs_check.run",
            return_value={
                "status": "FAIL",
                "files": 1,
                "diagrams": 0,
                "issues": issues,
            },
        ):
            report = run(Path("."), "docs")
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["failures"])
        self.assertEqual(issues, report["detail"]["issues"])

    def test_python_machine_adapter_preserves_complete_result(self):
        report = {"status": "BLOCKED", "checks_run": 2, "failures": 0, "errors": 0, "skipped": 0, "reason": "python-quality-incomplete", "detail": {"ruff": {}, "docstrings": {}}}
        with mock.patch("scripts.checks.repository.run_repository_check_group.python_quality.run", return_value=report):
            self.assertIs(report, run(Path("."), "python"))

    def test_machine_transport_is_zero_exit_on_failed_check(self):
        import contextlib
        import io
        import json
        from scripts.checks.repository import run_repository_check_group as quality
        with mock.patch.object(quality, "run", return_value={"status": "FAIL"}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, quality.main(["--mode", "python"]))
        self.assertEqual("FAIL", json.loads(output.getvalue())["status"])

    def test_retired_hook_mode_rejected(self):
        with self.assertRaises(ValueError):
            run(Path("."), "hooks")
