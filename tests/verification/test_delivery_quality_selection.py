"""交付语言检查按声明选择，不把工具事件变成重复执行入口。"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.scope import select_checks_for_changes

ROOT = Path(__file__).resolve().parents[2]


class DeliverySelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]

    def selected(self, paths):
        return {c["check_id"] for c in select_checks_for_changes(self.checks, paths)}

    def test_any_backend_path_requires_native_delivery(self):
        for path in (
            "backend/modules/new/src/Main.java",
            "backend/tests/new/Test.java",
            "backend/build.gradle.kts",
        ):
            with self.subTest(path=path):
                self.assertIn("eng.backend.delivery-on-change", self.selected([path]))
        check = next(
            c for c in self.checks if c["check_id"] == "eng.backend.delivery-on-change"
        )
        self.assertIn("deliveryFull", check["command"])

    def test_scripts_or_tool_policy_require_python_quality(self):
        for path in (
            "scripts/new.py",
            "scripts/agents/removed.py",
            "harness/python-quality.toml",
            "harness/python-docstrings.toml",
            "requirements-dev.txt",
        ):
            with self.subTest(path=path):
                self.assertIn(
                    "eng.repository.python-quality-on-change", self.selected([path])
                )

    def test_docs_only_does_not_select_language_quality(self):
        ids = self.selected(["docs/development/change-delivery/hooks.md"])
        self.assertNotIn("eng.repository.python-quality-on-change", ids)
        self.assertNotIn("eng.backend.delivery-on-change", ids)

    def test_both_client_hook_configs_select_and_freeze_hook_check(self):
        for path in (".codex/hooks.json", ".qoder/settings.json"):
            with self.subTest(path=path):
                self.assertIn("eng.repository.hooks-on-change", self.selected([path]))
                for check in self.checks:
                    if check["check_id"] in (
                        "eng.repository.hooks",
                        "eng.repository.hooks-on-change",
                    ):
                        self.assertIn(path, check["input_paths"])
                        self.assertIn("verification", check["module_dependencies"])

    def test_baseline_and_change_share_frozen_contract(self):
        by_id = {c["check_id"]: c for c in self.checks}
        for check_id in ("eng.backend.delivery", "eng.repository.python-quality"):
            base, change = by_id[check_id], by_id[check_id + "-on-change"]
            for key in (
                "command",
                "input_paths",
                "result_contract",
                "timeout_seconds",
                "required_environment",
            ):
                self.assertEqual(base[key], change[key], (check_id, key))
        self.assertIn(
            "harness/python-quality.toml",
            by_id["eng.repository.python-quality"]["input_paths"],
        )
        self.assertIn(
            "harness/python-docstrings.toml",
            by_id["eng.repository.python-quality"]["input_paths"],
        )


if __name__ == "__main__":
    unittest.main()
