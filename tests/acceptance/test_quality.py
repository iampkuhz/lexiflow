from __future__ import annotations

import unittest

from scripts.acceptance.quality import evaluate_suite


class AcceptanceQualityResultTest(unittest.TestCase):
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
