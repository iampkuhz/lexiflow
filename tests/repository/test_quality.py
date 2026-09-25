from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from scripts.repository.quality import evaluate_suite, run


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
            "scripts.repository.quality.docs_check.run",
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

    def test_python_gate_requires_ruff_and_docstrings(self) -> None:
        """任一静态工具缺失或发现问题都不能形成完整 Python PASS。"""
        base = {
            "status": "PASS",
            "checks_run": 2,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "reason": "",
            "detail": [],
        }
        docs = {**base, "checks_run": 1}
        with (
            mock.patch(
                "scripts.repository.quality.python_quality.run", return_value=base
            ),
            mock.patch(
                "scripts.repository.quality.python_quality.run_docstrings",
                return_value=docs,
            ),
        ):
            report = run(Path("."), "python")
        self.assertEqual(("PASS", 3), (report["status"], report["checks_run"]))

        for status in ("FAIL", "BLOCKED"):
            with (
                self.subTest(status=status),
                mock.patch(
                    "scripts.repository.quality.python_quality.run",
                    return_value=base,
                ),
                mock.patch(
                    "scripts.repository.quality.python_quality.run_docstrings",
                    return_value={
                        **docs,
                        "status": status,
                        "checks_run": 0,
                        "failures": int(status == "FAIL"),
                    },
                ),
            ):
                report = run(Path("."), "python")
            self.assertEqual(status, report["status"])
            self.assertEqual(2, report["checks_run"])
