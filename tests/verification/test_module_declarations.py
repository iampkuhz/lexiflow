"""Verify module-checks.yaml declares real product module entries.

After the product wrapper retirement, the global module-checks.yaml must
register backend, extension, and toolchain checks with native commands
instead of pending-product-coverage placeholders.  Product operation
wrappers must not remain in scripts/toolchain/.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
MODULE_CHECKS_PATH = ROOT / "harness" / "module-checks.yaml"

RETIRED_MODULES = {
    "scripts/toolchain/lexicon_import.py",
    "scripts/toolchain/postgres_test.py",
    "scripts/toolchain/local_stack.py",
}

RETAINED_MODULES = {
    "scripts/environment/java_runtime.py",
    "scripts/environment/java_exec.py",
}


class ModuleChecksDeclarationTest(unittest.TestCase):
    """The global module-checks.yaml must register real product checks."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = yaml.safe_load(MODULE_CHECKS_PATH.read_text(encoding="utf-8"))

    def test_schema_version_is_declared(self) -> None:
        self.assertEqual("lexiflow.module-checks.v1", self.document["schema_version"])

    def test_pending_product_coverage_placeholder_is_removed(self) -> None:
        check_ids = [check["check_id"] for check in self.document["checks"]]
        self.assertNotIn("eng.product-quality.pending-migration", check_ids)

    def test_backend_delivery_check_is_registered(self) -> None:
        backend_checks = [
            c for c in self.document["checks"] if c["check_id"] == "eng.backend.delivery"
        ]
        self.assertEqual(1, len(backend_checks), "backend delivery check must exist")
        check = backend_checks[0]
        self.assertEqual("backend", check["module"])
        self.assertIn("deliveryFull", " ".join(str(a) for a in check["command"]))
        self.assertEqual("exit-code", check["result_contract"]["type"])

    def test_extension_quality_check_is_registered(self) -> None:
        ext_checks = [
            c for c in self.document["checks"] if c["check_id"] == "eng.extension.quality"
        ]
        self.assertEqual(1, len(ext_checks), "extension quality check must exist")
        check = ext_checks[0]
        self.assertEqual("extension", check["module"])
        self.assertEqual(["node", "extension/scripts/quality-check.mjs"], check["command"])
        self.assertEqual("json-stdout", check["result_contract"]["type"])

    def test_all_engineering_modules_have_baseline_and_change_entries(self) -> None:
        checks = {check["check_id"]: check for check in self.document["checks"]}
        expected = {
            "eng.verification.module-tests",
            "eng.agents.module-tests",
            "eng.delivery-gate.module-tests",
            "eng.repository.module-tests",
            "eng.repository.docs",
            "eng.repository.policy",
            "eng.repository.planning",
            "eng.repository.hooks",
            "eng.backend.delivery",
            "eng.extension.quality",
        }
        for check_id in expected:
            with self.subTest(check_id=check_id):
                self.assertIn(check_id, checks)
                self.assertIn(check_id + "-on-change", checks)

    def test_repository_module_tests_check_is_registered(self) -> None:
        repo_checks = [
            c for c in self.document["checks"] if c["check_id"] == "eng.repository.module-tests"
        ]
        self.assertEqual(1, len(repo_checks), "repository unit test check must exist")
        check = repo_checks[0]
        self.assertEqual("repository", check["module"])
        self.assertIn("scripts.repository.quality", " ".join(str(a) for a in check["command"]))

    def test_structured_python_module_contract_rejects_empty_or_skipped(self) -> None:
        for check in self.document["checks"]:
            if check["check_id"].startswith(("eng.verification.", "eng.agents.",
                                              "eng.delivery-gate.", "eng.repository.")):
                with self.subTest(check_id=check["check_id"]):
                    contract = check["result_contract"]
                    self.assertEqual("json-stdout", contract["type"])
                    self.assertEqual(1, contract["minimum"]["checks_run"])
                    self.assertEqual(0, contract["equals"]["skipped"])
                    self.assertEqual(["PASS", "BLOCKED", "FAIL"], contract["allowed_statuses"])

    def test_no_check_references_retired_modules(self) -> None:
        """Global checks must not hardcode retired product wrapper paths."""
        serialized = yaml.dump(self.document)
        for retired in RETIRED_MODULES:
            self.assertNotIn(
                retired,
                serialized,
                f"module-checks.yaml still references retired module {retired}",
            )


class RetiredWrapperRemovalTest(unittest.TestCase):
    """Product operation wrappers must not exist in scripts/toolchain/."""

    def test_retired_product_wrappers_are_absent(self) -> None:
        for relative_path in RETIRED_MODULES:
            full_path = ROOT / relative_path
            self.assertFalse(
                full_path.exists(),
                f"retired product wrapper must not exist: {relative_path}",
            )

    def test_retired_wrapper_tests_are_absent(self) -> None:
        retired_tests = {
            "tests/toolchain/test_lexicon_import.py",
            "tests/toolchain/test_postgres_test.py",
            "tests/toolchain/test_postgres_migrations.py",
        }
        for relative_path in retired_tests:
            full_path = ROOT / relative_path
            self.assertFalse(
                full_path.exists(),
                f"retired wrapper test must not exist: {relative_path}",
            )

    def test_retained_environment_modules_exist(self) -> None:
        for relative_path in RETAINED_MODULES:
            full_path = ROOT / relative_path
            self.assertTrue(
                full_path.is_file(),
                f"retained toolchain module must exist: {relative_path}",
            )


class ExtensionQualityEntryTest(unittest.TestCase):
    """The extension must declare a quality:check npm script."""

    def test_quality_check_script_is_declared(self) -> None:
        import json

        package_json = json.loads((ROOT / "extension" / "package.json").read_text(encoding="utf-8"))
        scripts = package_json.get("scripts", {})
        self.assertIn("quality:check", scripts)
        self.assertIn("quality-check.mjs", scripts["quality:check"])

    def test_product_checks_have_strict_public_contracts_and_source_inputs(self) -> None:
        document = yaml.safe_load(MODULE_CHECKS_PATH.read_text(encoding="utf-8"))
        checks = {check["check_id"]: check for check in document["checks"]}
        for check_id in (
            "eng.backend.delivery",
            "eng.backend.delivery-on-change",
            "eng.extension.quality",
            "eng.extension.quality-on-change",
        ):
            with self.subTest(check_id=check_id):
                check = checks[check_id]
                self.assertNotIn("backend", check["input_paths"])
        self.assertEqual("exit-code", checks["eng.backend.delivery"]["result_contract"]["type"])
        self.assertEqual("json-stdout", checks["eng.extension.quality"]["result_contract"]["type"])
        self.assertIn("browser_smoke", checks["eng.extension.quality"]["result_contract"]["required_fields"])
        self.assertIn("extension/package-lock.json", checks["eng.extension.quality"]["input_paths"])
        self.assertEqual(
            ["java-25-temurin", "postgres-test-jdbc-url", "redis-test-endpoint"],
            checks["eng.backend.delivery"]["required_environment"],
        )



if __name__ == "__main__":
    unittest.main()
