from __future__ import annotations

import unittest
import contextlib
import io

from scripts.delivery_gate.quality import evaluate_suite


class DeliveryGateQualityResultTest(unittest.TestCase):
    def test_empty_suite_cannot_pass(self) -> None:
        self.assertEqual("FAIL", evaluate_suite(unittest.TestSuite())["status"])

    def test_skipped_test_cannot_pass(self) -> None:
        class Skipped(unittest.TestCase):
            @unittest.skip("fixture")
            def test_skip(self) -> None:
                pass

        report = evaluate_suite(unittest.defaultTestLoader.loadTestsFromTestCase(Skipped))
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["skipped"])
        self.assertEqual(1, len(report["detail"]["skipped_tests"]))

    def test_failure_locations_preserve_ids_without_traceback_payload(self) -> None:
        class Failed(unittest.TestCase):
            def test_assertion(self):
                self.fail("private synthetic payload")

            def test_error(self):
                raise ValueError("private synthetic payload")

        diagnostic = io.StringIO()
        with contextlib.redirect_stderr(diagnostic):
            report = evaluate_suite(unittest.defaultTestLoader.loadTestsFromTestCase(Failed))
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, len(report["detail"]["failed_tests"]))
        self.assertEqual(1, len(report["detail"]["error_tests"]))
        self.assertNotIn("private synthetic payload", str(report))
        self.assertIn("FAILED TEST:", diagnostic.getvalue())
        self.assertIn("private synthetic payload", diagnostic.getvalue())
