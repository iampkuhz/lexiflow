"""验证现有检查的轻量编排，不以 fixture 代替产品或真实服务验证。"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.verification.declarations import filter_checks_by_scope, load_declarations
from scripts.verification.kernel import snapshot_check_inputs
from scripts.verification.scope import (
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
    "eng.repository.policy",
    "eng.repository.planning",
    "eng.repository.hooks",
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
        expected = (
            BASE_IDS
            | {
                check_id + "-on-change"
                for check_id in BASE_IDS
                if check_id != "eng.release.docker-contract"
            }
            | {
                "eng.release.docker-surface-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
            }
        )
        self.assertEqual(expected, set(self.by_id))
        baseline = filter_checks_by_scope(self.checks, "repository-baseline")
        self.assertEqual(BASE_IDS, {check["check_id"] for check in baseline})
        closed = resolve_module_dependencies(baseline, self.checks)
        self.assertEqual(BASE_IDS, {check["check_id"] for check in closed})

    def test_light_checks_and_java_delivery_precede_framework_self_tests(self):
        selected = select_checks_for_changes(
            self.checks, ["harness/module-checks.yaml"]
        )
        closed = resolve_module_dependencies(selected, self.checks)
        ids = [check["check_id"] for check in closed]
        java = ids.index("eng.backend.delivery-on-change")
        for name in ("version",):
            self.assertLess(ids.index("eng.release." + name + "-on-change"), java)
        for name in ("docs", "policy", "planning", "hooks", "python-quality"):
            self.assertLess(ids.index("eng.repository." + name + "-on-change"), java)
        for check_id in (
            "eng.backend.build-contract-on-change",
            "eng.verification.module-tests-on-change",
            "eng.agents.module-tests-on-change",
            "eng.delivery-gate.module-tests-on-change",
            "eng.repository.module-tests-on-change",
        ):
            self.assertLess(java, ids.index(check_id))

    def test_independent_rule_check_does_not_pull_tool_test_suites(self):
        for name in ("docs", "policy", "planning", "hooks"):
            for suffix in ("", "-on-change"):
                check = self.by_id["eng.repository." + name + suffix]
                with self.subTest(check=check["check_id"]):
                    closed = resolve_module_dependencies([check], self.checks)
                    self.assertEqual(
                        [check["check_id"]], [c["check_id"] for c in closed]
                    )
                    self.assertIn("scripts/repository", check["input_paths"])

    def test_docs_only_change_selects_docs_not_repository_unit_tests(self):
        selected = select_checks_for_changes(self.checks, ["docs/user/example.md"])
        closed = resolve_module_dependencies(selected, self.checks)
        self.assertEqual(
            {"eng.repository.docs-on-change"},
            {check["check_id"] for check in closed},
        )

    def test_tool_source_changes_still_select_own_tests(self):
        for path, required in (
            ("scripts/repository/quality.py", "eng.repository.module-tests-on-change"),
            (
                "scripts/verification/kernel.py",
                "eng.verification.module-tests-on-change",
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
            ("docs", "docs_check.py"),
            ("policy", "planning_validator.py"),
            ("planning", "planning_validator.py"),
            ("hooks", "quality.py"),
        ):
            with self.subTest(name=name):
                check = self.by_id["eng.repository." + name]
                snapshot = snapshot_check_inputs(ROOT, check)
                paths = {item["locator"] for item in snapshot["files"]}
                self.assertIn("scripts/repository/quality.py", paths)
                self.assertIn("scripts/repository/" + source, paths)
                self.assertFalse(snapshot["missing"])
        self.assertIn(
            "harness/agent-policy.manifest.yaml",
            self.by_id["eng.repository.policy"]["input_paths"],
        )
        self.assertIn(
            "scripts/repository", self.by_id["eng.repository.policy"]["input_paths"]
        )

    def test_backend_scope_views_keep_identical_operations(self):
        baseline = self.by_id["eng.backend.delivery"]
        change = self.by_id["eng.backend.delivery-on-change"]
        selection = {"check_id", "module", "scope", "triggers"}
        self.assertEqual(
            {k: v for k, v in baseline.items() if k not in selection},
            {k: v for k, v in change.items() if k not in selection},
        )
        self.assertEqual(
            len(baseline["input_paths"]), len(set(baseline["input_paths"]))
        )
        self.assertIn("deliveryFull", baseline["command"])
        self.assertEqual(
            ["backend"], self.by_id["eng.extension.quality"]["module_dependencies"]
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
