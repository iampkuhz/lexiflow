from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.gates import repository_verify


class RepositoryVerifyTests(unittest.TestCase):
    def test_doctor_reports_stable_repository_readiness_remediations(self):
        with mock.patch("scripts.gates.repository_verify.resolve_java_home", side_effect=repository_verify.ToolchainError("missing")), \
             mock.patch("scripts.gates.repository_verify.shutil.which", return_value=None):
            result = repository_verify.doctor(".")
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["blocking_scope"], "repository-readiness")
        self.assertEqual({item["remediation_id"] for item in result["missing"]}, {"jdk-25", "podman", "psql"})
        self.assertTrue(all(item["doctor"] and item["bootstrap"] for item in result["missing"]))

    @mock.patch("scripts.gates.repository_verify.check_registry", return_value={"status": "PASS"})
    @mock.patch("scripts.gates.repository_verify.compile_baseline_plan", return_value={"checks": [{"check_id": "baseline"}]})
    def test_readiness_block_precedes_baseline_execution(self, plan, integrity):
        executor = mock.Mock()
        result = repository_verify.verify(".", executor=executor, readiness=lambda _: {"result": "BLOCKED", "missing": []})
        self.assertEqual(result["result"], "BLOCKED")
        self.assertEqual(result["blocking_scope"], "repository-readiness")
        executor.assert_not_called()

    @mock.patch("scripts.gates.repository_verify.check_registry", return_value={"status": "PASS"})
    @mock.patch("scripts.gates.repository_verify.compile_baseline_plan", return_value={"checks": [{"check_id": "baseline"}]})
    def test_baseline_is_independent_of_change_context(self, plan, integrity):
        result = repository_verify.verify(".", readiness=lambda _: {"result": "PASS"},
            executor=lambda _plan, **_: {"run_status": "PASS", "checks": []})
        self.assertEqual(result["result"], "PASS")
        self.assertIn("repository_fingerprint", result)


if __name__ == "__main__":
    unittest.main()
