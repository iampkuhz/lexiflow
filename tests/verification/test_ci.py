"""验证 CI quick 的选择、漂移防护与结果语义。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.triggers import run_ci_quick_checks as ci
from scripts.verification.planning.check_definitions import load_declarations
from tests.verification.declaration_fixtures import write_declarations

ROOT = Path(__file__).resolve().parents[2]


class CiQuickTests(unittest.TestCase):
    """覆盖 quick 计划和执行边界。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "harness").mkdir()
        (self.root / "scripts/checks/repository").mkdir(parents=True)
        self.declarations = load_declarations(ROOT)
        write_declarations(
            self.root / "config/engineering/module-checks.yaml", self.declarations["checks"]
        )
        self.policy = {
            "always_required_check_ids": [
                "eng.release.version",
                "eng.repository.docs",
            ],
            "formal_only_check_ids": [
                "eng.release.lifecycle-runtime",
                "eng.release.candidate-runtime",
            ],
        }
        self._write_policy()
        (self.root / "scripts/checks/repository/run_repository_check_group.py").write_text("fixture")
        self.base_patches()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_policy(self) -> None:
        path = self.root / ci.MODULES
        document = yaml.safe_load(path.read_bytes())
        document["scenario_policy"]["ci_quick"] = self.policy
        path.write_text(yaml.safe_dump(document))

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
        result = {
            "kind": "ci-quick",
            "status": "FAIL",
            "diagnostic": secret,
            "selected_diagnostic": {
                "selected_report": {
                    "checks": [
                        {
                            "check_id": "eng.fixture",
                            "status": "FAIL",
                            "reason": secret,
                            "process": {"exit_code": 1, "stderr": secret},
                            "result_contract": {
                                "report": {
                                    "checks_run": 2,
                                    "detail": {
                                        "failed_tests": [
                                            "tests.fixture.Test.test_failure",
                                            secret + " /private",
                                        ],
                                        "issues": [secret],
                                        "traceback": secret,
                                    },
                                }
                            },
                        }
                    ]
                }
            },
        }
        summary = ci.public_summary(result, self.root)
        self.assertNotIn(secret, json.dumps(summary))
        self.assertEqual(
            summary["checks"][0]["test_ids"], ["tests.fixture.Test.test_failure"]
        )
        self.assertFalse(summary["formal_eligible"])

    def test_real_backend_extension_docs_and_version_mapping(self) -> None:
        for path, expected in [
            ("backend/product/api/src/X.java", "eng.backend.delivery"),
            ("extension/src/content.ts", "eng.extension.quality"),
            ("docs/system/x.md", "eng.repository.docs"),
            ("ops/release/version.txt", "eng.release.version"),
        ]:
            with self.subTest(path=path):
                with patch.object(ci, "changed_paths", return_value=[path]):
                    selected = set(ci.make_plan(self.root)["selected_check_ids"])
                self.assertIn(expected, selected)

    def test_workflow_database_matches_isolated_jar_test_contract(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_bytes())
        job = workflow["jobs"]["quick"]
        self.assertEqual(
            job["services"]["postgres"]["env"]["POSTGRES_DB"], "lexiflow_hook_test"
        )
        self.assertEqual(
            job["env"]["LEXIFLOW_POSTGRES_TEST_JDBC_URL"],
            "jdbc:postgresql://127.0.0.1:5432/lexiflow_hook_test?user=postgres",
        )

    def test_public_summary_projects_only_python_traceback_code_location(self) -> None:
        source = self.root / "ops/docker/tests/test_operation_ownership.py"
        source.parent.mkdir(parents=True)
        source.write_text("synthetic source")
        private_source = self.root / "private/sensitive.py"
        private_source.parent.mkdir()
        private_source.write_text("synthetic private data")
        log = self.root / "tmp/quality/synthetic-stderr.log"
        log.parent.mkdir(parents=True)
        raw = (
            '  File "/private/checkout/ops/docker/tests/test_operation_ownership.py", line 492, in test_example\n'
            '  File "/private/user-data/sensitive.py", line 12, in private_function\n'
            '  File "/private/checkout/ops/../private/sensitive.py", line 13, in private_function\n'
            "AssertionError: private-caption-and-token-value\n"
        ).encode()
        log.write_bytes(raw)
        result = {
            "kind": "ci-quick",
            "status": "FAIL",
            "selected_diagnostic": {
                "selected_report": {
                    "checks": [
                        {
                            "check_id": "eng.fixture",
                            "status": "FAIL",
                            "process": {
                                "output_artifacts": {
                                    "stderr": {
                                        "locator": log.relative_to(self.root).as_posix(),
                                        "sha256": hashlib.sha256(raw).hexdigest(),
                                    }
                                }
                            },
                        }
                    ]
                }
            },
        }
        summary = ci.public_summary(result, self.root)
        self.assertEqual(
            summary["checks"][0]["source_locations"],
            ["ops/docker/tests/test_operation_ownership.py:492"],
        )
        self.assertNotIn("private-caption-and-token-value", json.dumps(summary))
        self.assertNotIn("/private/", json.dumps(summary))

    def test_clean_runtime_is_deferred_and_plan_says_zero_executed(self) -> None:
        plan = ci.make_plan(self.root)
        self.assertEqual(plan["checks_executed"], 0)
        with patch.object(
            ci, "changed_paths", return_value=["ops/release/build/release-runtime-entry-generator.mjs"]
        ):
            plan = ci.make_plan(self.root)
        self.assertNotIn("eng.release.lifecycle-runtime", plan["selected_check_ids"])
        self.assertEqual(
            set(plan["deferred_for_formal"]),
            {"eng.release.lifecycle-runtime", "eng.release.candidate-runtime"},
        )
        self.assertNotIn("eng.release.candidate-runtime", plan["selected_check_ids"])

    def test_run_rejects_retired_execute_mode(self):
        with self.assertRaises(TypeError):
            ci.run(self.root, execute=False)

    def test_uncovered_unknown_and_missing_profile_fail(self) -> None:
        with patch.object(ci, "changed_paths", return_value=["mystery.file"]):
            self.assertEqual(ci.run(self.root)["status"], "FAIL")
        self.policy["always_required_check_ids"] = ["unknown.id"]
        self._write_policy()
        self.assertEqual(ci.run(self.root)["status"], "FAIL")
        (self.root / ci.MODULES).unlink()
        self.assertEqual(ci.run(self.root)["status"], "FAIL")

    def test_formal_dependency_closure_is_rejected(self) -> None:
        baseline = next(
            item
            for item in self.declarations["checks"]
            if item["check_id"] == "eng.extension.quality"
        )
        baseline["module_dependencies"] = ["eng.release.lifecycle-runtime"]
        write_declarations(self.root / ci.MODULES, self.declarations["checks"])
        self.policy["always_required_check_ids"] = ["eng.extension.quality"]
        self._write_policy()
        with patch.object(ci, "changed_paths", return_value=[]):
            self.assertEqual(ci.run(self.root)["status"], "FAIL")

    def test_invalid_policy_fields_and_always_formal_rejected(self) -> None:
        self.policy["extra"] = True
        self._write_policy()
        self.assertEqual(ci.run(self.root)["status"], "FAIL")
        self.policy.pop("extra")
        self.policy["always_required_check_ids"] = ["eng.release.lifecycle-runtime"]
        self._write_policy()
        self.assertEqual(ci.run(self.root)["status"], "FAIL")

    def test_base_injection_and_noncommit_rejected(self) -> None:
        self.base_context.stop()
        self.assertEqual(ci.run(self.root, "x" * 39)["status"], "FAIL")
        with patch.object(
            ci.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "", ""),
        ):
            self.assertEqual(ci.run(self.root, "a" * 40)["status"], "FAIL")

    def test_ci_does_not_convert_partial_repository_qualification(self):
        import inspect

        source = inspect.getsource(ci.run)
        self.assertNotIn("diagnose(", source)
        self.assertNotIn("partial-check-selection", source)
        self.assertIn("_execute(", source)

    def test_profile_drift_after_plan_fails_before_execution(self):
        plan = ci.make_plan(self.root)
        (self.root / ci.MODULES).write_text(
            (self.root / ci.MODULES).read_text() + "# drift\n"
        )
        with patch.object(ci, "make_plan", return_value=plan):
            self.assertEqual(ci.run(self.root)["status"], "FAIL")

    def test_real_diagnostic_executes_synthetic_command_without_formal_publication(
        self,
    ) -> None:
        """使用真实固定 Kernel 执行合成命令，CI 不转换诊断或部分仓库资格。"""
        self.policy["always_required_check_ids"] = ["eng.release.version"]
        self._write_policy()
        declarations = []
        for check_id in (
            "eng.release.version",
            "eng.release.lifecycle-runtime",
            "eng.release.candidate-runtime",
        ):
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
                write_declarations(
                    self.root / ci.MODULES, declarations, ci_quick=self.policy
                )
                result = ci.run(self.root)
                self.assertEqual(result["status"], status, result)
                self.assertEqual(result["checks_executed"], 1)
                self.assertFalse(result["formal_eligible"])
                self.assertFalse(result["full_repository_executed"])
                diagnostic = result["selected_diagnostic"]
                self.assertEqual(diagnostic["kind"], "ci-quick-execution")
                self.assertNotIn("qualification_result", diagnostic)
                self.assertNotIn("publication", diagnostic["selected_report"])

    def test_all_mode_uses_git_visible_paths_and_mutual_exclusion(self) -> None:
        with patch.object(ci, "_all_paths", return_value=["extension/src/content.ts"]):
            plan = ci.make_plan(self.root, all_paths=True)
        self.assertEqual(plan["base"], "all")
        self.assertIn("eng.extension.quality", plan["selected_check_ids"])
        self.assertEqual(ci.run(self.root, "a" * 40, all_paths=True)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
