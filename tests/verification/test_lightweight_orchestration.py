"""验证现有检查的轻量编排，不以 fixture 代替产品或真实服务验证。"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.verification.planning.check_definitions import filter_checks_by_scope, load_declarations
from scripts.verification.execution.check_runner import snapshot_check_inputs
from scripts.verification.planning.check_scope_selection import (
    resolve_module_dependencies,
    select_checks_for_changes,
)

ROOT = Path(__file__).resolve().parents[2]
BASE_IDS = {
    "eng.backend.build-contract",
    "eng.backend.delivery",
    "eng.verification.module-tests",
    "eng.agents.module-tests",
    "eng.delivery-gate.module-tests",
    "eng.repository.module-tests",
    "eng.repository.docs",
    "eng.repository.planning",
    "eng.repository.python-quality",
    "eng.extension.quality",
    "eng.release.version",
    "eng.release.manifest",
    "eng.release.docker-contract",
    "eng.release.runtime-entry",
    "eng.release.lifecycle-install",
    "eng.release.lifecycle",
    "eng.release.lifecycle-runtime",
    "eng.release.build",
    "eng.release.candidate-runtime",
}


class LightweightOrchestrationTest(unittest.TestCase):
    """使用实际 Harness 声明校核范围、输入及反馈顺序。"""

    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations(ROOT)["checks"]
        cls.by_id = {check["check_id"]: check for check in cls.checks}

    def test_all_existing_ids_and_complete_baseline_remain(self):
        expected = BASE_IDS | {
            "eng.release.docker-surface-on-change",
            "eng.release.docker-shell-on-change",
            "eng.release.docker-local-on-change",
            "eng.release.docker-node-on-change",
        }
        self.assertEqual(23, len(self.checks))
        self.assertEqual(
            22, len(filter_checks_by_scope(self.checks, "change-targeted"))
        )
        for check in self.checks:
            self.assertNotIn("scope", check)
            self.assertNotIn("module", check)
        self.assertEqual(expected, set(self.by_id))
        baseline = filter_checks_by_scope(self.checks, "repository-baseline")
        self.assertEqual(BASE_IDS, {check["check_id"] for check in baseline})
        closed = resolve_module_dependencies(baseline, self.checks)
        self.assertEqual(BASE_IDS, {check["check_id"] for check in closed})

    def test_light_checks_and_java_delivery_precede_framework_self_tests(self):
        selected = select_checks_for_changes(
            self.checks, ["config/engineering/module-checks.yaml"]
        )
        closed = resolve_module_dependencies(selected, self.checks)
        ids = [check["check_id"] for check in closed]
        java = ids.index("eng.backend.delivery")
        for name in ("version",):
            self.assertLess(ids.index("eng.release." + name), java)
        for name in ("docs", "planning", "python-quality"):
            self.assertLess(ids.index("eng.repository." + name), java)
        for check_id in (
            "eng.backend.build-contract",
            "eng.verification.module-tests",
            "eng.agents.module-tests",
            "eng.delivery-gate.module-tests",
            "eng.repository.module-tests",
        ):
            self.assertLess(java, ids.index(check_id))

    def test_independent_rule_check_does_not_pull_tool_test_suites(self):
        for name in ("docs", "planning"):
            for scope in ("repository-baseline", "change-targeted"):
                check = next(
                    c
                    for c in filter_checks_by_scope(self.checks, scope)
                    if c["check_id"] == "eng.repository." + name
                )
                with self.subTest(check=check["check_id"]):
                    closed = resolve_module_dependencies([check], self.checks)
                    self.assertEqual(
                        [check["check_id"]], [c["check_id"] for c in closed]
                    )
                    self.assertIn("scripts/checks/repository", check["input_paths"])

    def test_docs_only_change_selects_docs_not_repository_unit_tests(self):
        selected = select_checks_for_changes(self.checks, ["docs/user/example.md"])
        closed = resolve_module_dependencies(selected, self.checks)
        self.assertEqual(
            {"eng.repository.docs"},
            {check["check_id"] for check in closed},
        )

    def test_tool_source_changes_still_select_own_tests(self):
        for path, required in (
            ("scripts/checks/repository/run_repository_check_group.py", "eng.repository.module-tests"),
            (
                "scripts/verification/execution/check_runner.py",
                "eng.verification.module-tests",
            ),
        ):
            with self.subTest(path=path):
                selected = select_checks_for_changes(self.checks, [path])
                ids = {
                    c["check_id"]
                    for c in resolve_module_dependencies(selected, self.checks)
                }
                self.assertIn(required, ids)

    def test_rule_input_snapshot_includes_actual_checker_source(self):
        for name, source in (
            ("docs", "documentation/check_documentation.py"),
            ("planning", "catalog/task_catalog_rules.py"),
        ):
            with self.subTest(name=name):
                check = self.by_id["eng.repository." + name]
                snapshot = snapshot_check_inputs(ROOT, check)
                paths = {item["locator"] for item in snapshot["files"]}
                self.assertIn("scripts/checks/repository/run_repository_check_group.py", paths)
                self.assertIn("scripts/checks/repository/" + source, paths)
                self.assertFalse(snapshot["missing"])

    def test_backend_scope_views_keep_identical_operations(self):
        baseline = next(
            c
            for c in filter_checks_by_scope(self.checks, "repository-baseline")
            if c["check_id"] == "eng.backend.delivery"
        )
        change = next(
            c
            for c in filter_checks_by_scope(self.checks, "change-targeted")
            if c["check_id"] == "eng.backend.delivery"
        )
        selection = {"check_id", "scope", "triggers"}
        self.assertEqual(
            {k: v for k, v in baseline.items() if k not in selection},
            {k: v for k, v in change.items() if k not in selection},
        )
        self.assertEqual(
            len(baseline["input_paths"]), len(set(baseline["input_paths"]))
        )
        self.assertIn("deliveryFull", baseline["command"])
        self.assertEqual(
            ["eng.backend.delivery"],
            self.by_id["eng.extension.quality"]["module_dependencies"],
        )

    def test_mechanical_javadoc_scopes_and_compact_constructor_stay_required(self):
        config = ET.parse(ROOT / "backend/gradle/config/checkstyle/checkstyle.xml")
        modules = {node.get("name"): node for node in config.iter("module")}
        for name, expected_scope in (
            ("MissingJavadocType", "private"),
            ("MissingJavadocMethod", "public"),
        ):
            values = {
                p.get("name"): p.get("value") for p in modules[name].findall("property")
            }
            self.assertEqual(expected_scope, values["scope"])
            if name == "MissingJavadocMethod":
                self.assertIn("COMPACT_CTOR_DEF", values["tokens"].split(","))
                self.assertEqual("false", values["allowMissingPropertyJavadoc"])


if __name__ == "__main__":
    unittest.main()
