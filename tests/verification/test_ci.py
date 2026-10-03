"""验证 CI quick 的选择、漂移防护与结果语义。"""

from __future__ import annotations

import subprocess
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification import ci

ROOT = Path(__file__).resolve().parents[2]


class CiQuickTests(unittest.TestCase):
    """覆盖 quick 计划和执行边界。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "harness").mkdir()
        (self.root / "scripts/repository").mkdir(parents=True)
        self.declarations = yaml.safe_load(
            (ROOT / "harness/module-checks.yaml").read_text()
        )
        (self.root / "harness/module-checks.yaml").write_text(
            yaml.safe_dump(self.declarations)
        )
        self.policy = {
            "schema_version": "lexiflow.ci-quick.v1",
            "always_required_check_ids": [
                "eng.release.version",
                "eng.repository.docs",
                "eng.repository.policy",
            ],
            "formal_only_check_ids": ["eng.release.lifecycle-runtime", "eng.release.candidate-runtime"],
        }
        self._write_policy()
        (self.root / "scripts/repository/quality.py").write_text("fixture")
        self.base_patches()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_policy(self) -> None:
        (self.root / "harness/ci-policy.yaml").write_text(yaml.safe_dump(self.policy))

    def base_patches(self, changed: list[str] | None = None) -> None:
        self.base_context = patch.object(ci, "_base", return_value="a" * 40)
        self.changed_context = patch.object(
            ci, "changed_paths", return_value=changed or []
        )
        self.base_context.start()
        self.changed_context.start()
        self.addCleanup(self.base_context.stop)
        self.addCleanup(self.changed_context.stop)

    def test_public_summary_excludes_private_payloads(self) -> None:
        secret = "private-caption-and-token-value"
        result = {"kind": "ci-quick", "status": "FAIL", "diagnostic": secret,
                  "selected_diagnostic": {"selected_report": {"checks": [{
                      "check_id": "eng.fixture", "status": "FAIL", "reason": secret,
                      "process": {"exit_code": 1, "stderr": secret},
                      "result_contract": {"report": {"checks_run": 2, "detail": {
                          "failed_tests": ["tests.fixture.Test.test_failure", secret + " /private"],
                          "issues": [secret], "traceback": secret}}}}]}}}
        summary = ci.public_summary(result, self.root)
        self.assertNotIn(secret, json.dumps(summary))
        self.assertEqual(summary["checks"][0]["test_ids"], ["tests.fixture.Test.test_failure"])
        self.assertFalse(summary["formal_eligible"])

    def test_real_backend_extension_docs_and_version_mapping(self) -> None:
        for path, expected in [
            ("backend/product/api/src/X.java", "eng.backend.delivery"),
            ("extension/src/content.ts", "eng.extension.quality"),
            ("docs/product/x.md", "eng.repository.docs"),
            ("ops/release/version.txt", "eng.release.version"),
        ]:
            with self.subTest(path=path):
                with patch.object(ci, "changed_paths", return_value=[path]):
                    selected = set(ci.make_plan(self.root)["selected_check_ids"])
                self.assertIn(expected, selected)

    def test_clean_runtime_is_deferred_and_plan_says_zero_executed(self) -> None:
        plan = ci.make_plan(self.root)
        self.assertEqual(plan["checks_executed"], 0)
        with patch.object(
            ci, "changed_paths", return_value=["ops/release/lifecycle.mjs"]
        ):
            plan = ci.make_plan(self.root)
        self.assertNotIn("eng.release.lifecycle-runtime", plan["selected_check_ids"])
        self.assertEqual(set(plan["deferred_for_formal"]), {"eng.release.lifecycle-runtime", "eng.release.candidate-runtime"})
        self.assertNotIn("eng.release.candidate-runtime", plan["selected_check_ids"])

    def test_uncovered_unknown_and_missing_profile_fail(self) -> None:
        with patch.object(ci, "changed_paths", return_value=["mystery.file"]):
            self.assertEqual(ci.run(self.root, execute=False)["status"], "FAIL")
        self.policy["always_required_check_ids"] = ["unknown.id"]
        self._write_policy()
        self.assertEqual(ci.run(self.root, execute=False)["status"], "FAIL")
        (self.root / ci.POLICY).unlink()
        self.assertEqual(ci.run(self.root, execute=False)["status"], "FAIL")

    def test_formal_dependency_closure_is_rejected(self) -> None:
        baseline = next(
            item
            for item in self.declarations["checks"]
            if item["check_id"] == "eng.extension.quality"
        )
        baseline["module_dependencies"] = ["release-lifecycle-runtime"]
        (self.root / ci.MODULES).write_text(yaml.safe_dump(self.declarations))
        self.policy["always_required_check_ids"] = ["eng.extension.quality"]
        self._write_policy()
        with patch.object(ci, "changed_paths", return_value=[]):
            self.assertEqual(ci.run(self.root, execute=False)["status"], "FAIL")

    def test_invalid_policy_fields_and_always_formal_rejected(self) -> None:
        self.policy["extra"] = True
        self._write_policy()
        self.assertEqual(ci.run(self.root, execute=False)["status"], "FAIL")
        self.policy.pop("extra")
        self.policy["always_required_check_ids"] = ["eng.release.lifecycle-runtime"]
        self._write_policy()
        self.assertEqual(ci.run(self.root, execute=False)["status"], "FAIL")

    def test_base_injection_and_noncommit_rejected(self) -> None:
        self.base_context.stop()
        self.assertEqual(ci.run(self.root, "x" * 39, execute=False)["status"], "FAIL")
        with patch.object(
            ci.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", ""),
        ):
            self.assertEqual(
                ci.run(self.root, "a" * 40, execute=False)["status"], "FAIL"
            )

    def test_results_are_order_independent_but_exact_set_required(self) -> None:
        plan = ci.make_plan(self.root)
        ids = plan["selected_check_ids"]
        checks = [{"check_id": item, "status": "PASS"} for item in reversed(ids)]
        report = {
            "checks": checks,
            "coverage_gaps": [],
            "executed_check_ids": ids,
            "reason": "partial-check-selection",
        }
        with patch.object(
            ci,
            "diagnose",
            return_value={"selected_result": "BLOCKED", "selected_report": report},
        ):
            outcome = ci.run(self.root, execute=True)
        self.assertEqual(outcome["status"], "PASS")
        self.assertEqual(outcome["selected_diagnostic"]["selected_result"], "BLOCKED")
        self.assertFalse(outcome["formal_eligible"])
        for altered in (
            checks + [checks[0]],
            checks[:-1],
            checks + [{"check_id": "extra", "status": "PASS"}],
        )[:]:
            report["checks"] = altered
            with patch.object(
                ci,
                "diagnose",
                return_value={"selected_result": "BLOCKED", "selected_report": report},
            ):
                self.assertEqual(ci.run(self.root, execute=True)["status"], "FAIL")

    def test_real_blocked_and_fail_are_not_upgraded(self) -> None:
        plan = ci.make_plan(self.root)
        ids = plan["selected_check_ids"]
        for status in ("BLOCKED", "FAIL"):
            report = {
                "checks": [{"check_id": item, "status": "PASS"} for item in ids],
                "coverage_gaps": [],
                "executed_check_ids": ids,
                "reason": "environment-unavailable",
            }
            result = {"selected_result": status, "selected_report": report}
            with patch.object(ci, "diagnose", return_value=result):
                self.assertEqual(ci.run(self.root, execute=True)["status"], status)

    def test_profile_and_source_drift_after_plan_fail(self) -> None:
        (self.root / "scripts/repository/quality.py").write_text("initial")
        with patch.object(ci, "changed_paths", return_value=[]):
            plan = ci.make_plan(self.root)
        ids = plan["selected_check_ids"]
        report = {
            "checks": [{"check_id": item, "status": "PASS"} for item in ids],
            "coverage_gaps": [],
            "executed_check_ids": ids,
            "reason": "partial-check-selection",
        }

        def drift_source(*args, **kwargs):
            (self.root / "scripts/repository/quality.py").write_text("changed")
            return {"selected_result": "BLOCKED", "selected_report": report}

        with (
            patch.object(ci, "make_plan", return_value=plan),
            patch.object(ci, "diagnose", side_effect=drift_source),
        ):
            self.assertEqual(ci.run(self.root, execute=True)["status"], "FAIL")

        with patch.object(ci, "make_plan", return_value=plan):
            (self.root / "harness/ci-policy.yaml").write_text(
                (self.root / "harness/ci-policy.yaml").read_text() + "# drift\n"
            )
            self.assertEqual(ci.run(self.root, execute=True)["status"], "FAIL")

    def test_real_diagnostic_executes_synthetic_command_without_formal_publication(
        self,
    ) -> None:
        """使用真实 Diagnose/Kernel 执行合成命令，不把 partial 报告升级成 Formal。"""
        self.policy["always_required_check_ids"] = ["eng.release.version"]
        self._write_policy()
        declarations = []
        for check_id in ("eng.release.version", "eng.release.lifecycle-runtime", "eng.release.candidate-runtime"):
            check = dict(
                next(
                    item
                    for item in self.declarations["checks"]
                    if item["check_id"] == check_id
                )
            )
            check.update(
                input_paths=["sample.txt"],
                required_environment=["python3"],
                executable="python3",
                timeout_seconds=10,
                module_dependencies=[],
            )
            declarations.append(check)
        (self.root / "sample.txt").write_text("synthetic-only")
        for status in ("PASS", "FAIL"):
            with self.subTest(status=status):
                payload = json.dumps(
                    {
                        "status": status,
                        "checks_run": 1,
                        "failures": int(status == "FAIL"),
                        "errors": 0,
                        "skipped": 0,
                        "reason": "",
                    }
                )
                for check in declarations:
                    check["command"] = ["python3", "-c", f"print({payload!r})"]
                (self.root / ci.MODULES).write_text(
                    yaml.safe_dump(
                        {
                            "schema_version": "lexiflow.module-checks.v1",
                            "checks": declarations,
                        }
                    )
                )
                result = ci.run(self.root, execute=True)
                self.assertEqual(result["status"], status, result)
                self.assertEqual(result["checks_executed"], 1)
                self.assertFalse(result["formal_eligible"])
                self.assertFalse(result["full_repository_executed"])
                diagnostic = result["selected_diagnostic"]
                self.assertEqual(
                    diagnostic["selected_result"],
                    "BLOCKED" if status == "PASS" else "FAIL",
                )
                self.assertNotIn("publication", diagnostic["selected_report"])

    def test_all_mode_uses_git_visible_paths_and_mutual_exclusion(self) -> None:
        with patch.object(ci, "_all_paths", return_value=["extension/src/content.ts"]):
            plan = ci.make_plan(self.root, all_paths=True)
        self.assertEqual(plan["base"], "all")
        self.assertIn("eng.extension.quality", plan["selected_check_ids"])
        self.assertEqual(
            ci.run(self.root, "a" * 40, execute=False, all_paths=True)["status"], "FAIL"
        )


if __name__ == "__main__":
    unittest.main()
