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
from tests.verification.declaration_fixtures import input_closure
from scripts.verification.planning.check_definitions import load_declarations


ROOT = Path(__file__).resolve().parents[2]
MODULE_CHECKS_PATH = ROOT / "config" / "engineering" / "module-checks.yaml"

RETIRED_MODULES = {
    "scripts/toolchain/lexicon_import.py",
    "scripts/toolchain/postgres_test.py",
    "scripts/toolchain/local_stack.py",
}

RETAINED_MODULES = {
    "scripts/environment/toolchains/java_toolchain_selection.py",
    "scripts/local/run_with_java_toolchain.py",
}


class ModuleChecksDeclarationTest(unittest.TestCase):
    """The global module-checks.yaml must register real product checks."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.document = load_declarations(ROOT)

    def test_acceptance_module_fixed_budget_preserves_full_contract(self) -> None:
        """完整验收工具回归只增加固定调度余量，不改变集合与依赖。"""
        checks = {check["check_id"]: check for check in self.document["checks"]}
        check = checks["eng.delivery-gate.module-tests"]
        self.assertEqual(check["timeout_seconds"], 300)
        self.assertEqual(
            check["command"],
            ["python3", "-m", "scripts.checks.tool_tests.run_acceptance_tool_tests"],
        )
        self.assertEqual(
            check["module_dependencies"],
            ["eng.verification.module-tests", "eng.agents.module-tests"],
        )
        self.assertEqual(check["scenarios"], ["repository-baseline", "change-targeted"])

    def test_protocol_is_not_repeated_as_configuration(self) -> None:
        self.assertEqual({"checks", "scenario_policy"}, set(self.document))

    def test_pending_product_coverage_placeholder_is_removed(self) -> None:
        check_ids = [check["check_id"] for check in self.document["checks"]]
        self.assertNotIn("eng.product-quality.pending-migration", check_ids)

    def test_backend_delivery_check_is_registered(self) -> None:
        backend_checks = [
            c
            for c in self.document["checks"]
            if c["check_id"] == "eng.backend.delivery"
        ]
        self.assertEqual(1, len(backend_checks), "backend delivery check must exist")
        check = backend_checks[0]
        self.assertIn("deliveryFull", " ".join(str(a) for a in check["command"]))
        self.assertEqual("exit-code", check["result_contract"]["type"])

    def test_extension_quality_check_is_registered(self) -> None:
        ext_checks = [
            c
            for c in self.document["checks"]
            if c["check_id"] == "eng.extension.quality"
        ]
        self.assertEqual(1, len(ext_checks), "extension quality check must exist")
        check = ext_checks[0]
        self.assertEqual(
            ["node", "extension/scripts/checks/run-extension-checks.mjs"],
            check["command"],
        )
        self.assertEqual("json-stdout", check["result_contract"]["type"])

    def test_docker_group_members_remain_exact_and_nonoverlapping(self) -> None:
        from scripts.checks.deployment.run_deployment_contract_tests import _members

        checks = {check["check_id"]: check for check in self.document["checks"]}
        members = _members(checks)
        expected = {
            "surface": {
                "test_contract.py",
                "test_module_check_adapter.py",
                "test_podman_compose_contract.py",
            },
            "shell": {
                "test_docker_bounds.py",
                "test_docker_environment.py",
                "test_lifecycle_flow.py",
                "test_operation_ownership.py",
                "test_secret_publication.py",
                "test_state_records.py",
            },
            "local": {
                "test_ecdict_fetch.py",
                "test_podman_doctor.py",
                "test_podman_fake_transport.py",
                "test_podman_local.py",
                "test_podman_network.py",
                "test_podman_progress.py",
            },
        }
        for group, names in expected.items():
            self.assertEqual(names, {Path(path).name for path in members[group]})
        self.assertTrue(members["node"])
        self.assertTrue(all(path.endswith(".test.mjs") for path in members["node"]))
        flattened = [path for paths in members.values() for path in paths]
        self.assertEqual(len(flattened), len(set(flattened)))

    def test_all_engineering_modules_have_baseline_and_change_entries(self) -> None:
        checks = {check["check_id"]: check for check in self.document["checks"]}
        expected = {
            "eng.verification.module-tests",
            "eng.agents.module-tests",
            "eng.delivery-gate.module-tests",
            "eng.repository.module-tests",
            "eng.repository.docs",
            "eng.repository.planning",
            "eng.backend.delivery",
            "eng.extension.quality",
        }
        for check_id in expected:
            with self.subTest(check_id=check_id):
                self.assertIn(check_id, checks)
                self.assertEqual(
                    set(checks[check_id]["scenarios"]),
                    {"repository-baseline", "change-targeted"},
                )

    def test_repository_module_tests_check_is_registered(self) -> None:
        repo_checks = [
            c
            for c in self.document["checks"]
            if c["check_id"] == "eng.repository.module-tests"
        ]
        self.assertEqual(1, len(repo_checks), "repository unit test check must exist")
        check = repo_checks[0]
        self.assertIn(
            "scripts.checks.repository.run_repository_check_group",
            " ".join(str(a) for a in check["command"]),
        )

    def test_structured_python_module_contract_rejects_empty_or_skipped(self) -> None:
        for check in self.document["checks"]:
            if check["check_id"].startswith(
                (
                    "eng.verification.",
                    "eng.agents.",
                    "eng.delivery-gate.",
                    "eng.repository.",
                )
            ):
                with self.subTest(check_id=check["check_id"]):
                    contract = check["result_contract"]
                    self.assertEqual("json-stdout", contract["type"])
                    self.assertEqual(1, contract["minimum"]["checks_run"])
                    self.assertEqual(0, contract["equals"]["skipped"])
                    self.assertEqual(
                        ["PASS", "BLOCKED", "FAIL"], contract["allowed_statuses"]
                    )

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

        package_json = json.loads(
            (ROOT / "extension" / "package.json").read_text(encoding="utf-8")
        )
        scripts = package_json.get("scripts", {})
        self.assertIn("quality:check", scripts)
        self.assertIn(
            "scripts/checks/run-extension-checks.mjs", scripts["quality:check"]
        )

    def test_product_checks_have_strict_public_contracts_and_source_inputs(
        self,
    ) -> None:
        document = load_declarations(ROOT)
        checks = {check["check_id"]: check for check in document["checks"]}
        for check_id in (
            "eng.backend.delivery",
            "eng.extension.quality",
        ):
            with self.subTest(check_id=check_id):
                check = checks[check_id]
                self.assertNotIn("backend", input_closure(ROOT, check))
        self.assertEqual(
            "exit-code", checks["eng.backend.delivery"]["result_contract"]["type"]
        )
        self.assertEqual(
            "json-stdout", checks["eng.extension.quality"]["result_contract"]["type"]
        )
        self.assertIn(
            "browser_smoke",
            checks["eng.extension.quality"]["result_contract"]["required_fields"],
        )
        self.assertIn(
            "extension/package-lock.json",
            input_closure(ROOT, checks["eng.extension.quality"]),
        )
        self.assertEqual(
            [
                "python3",
                "java-25-temurin",
                "node",
                "git",
                "postgres-test-jdbc-url",
                "redis-test-endpoint",
            ],
            checks["eng.backend.delivery"]["required_environment"],
        )


if __name__ == "__main__":
    unittest.main()
