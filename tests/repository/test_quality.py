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
