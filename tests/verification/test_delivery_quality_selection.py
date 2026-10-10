"""交付语言检查按声明选择，不把工具事件变成重复执行入口。"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.verification.planning.check_definitions import (
    load_declarations_snapshot,
    filter_checks_by_scope,
)
from scripts.verification.execution.check_runner import snapshot_check_inputs
from scripts.verification.planning.check_scope_selection import (
    select_checks_for_changes,
)

ROOT = Path(__file__).resolve().parents[2]


class DeliverySelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]

    def selected(self, paths):
        return {c["check_id"] for c in select_checks_for_changes(self.checks, paths)}

    def test_any_backend_path_requires_native_delivery(self):
        for path in (
            "backend/product/new/src/Main.java",
            "backend/verification/new/Test.java",
            "backend/build.gradle.kts",
        ):
            with self.subTest(path=path):
                self.assertIn("eng.backend.delivery", self.selected([path]))
        check = next(c for c in self.checks if c["check_id"] == "eng.backend.delivery")
        self.assertIn("deliveryFull", check["command"])

    def test_scripts_or_tool_policy_require_python_quality(self):
        for path in (
            "scripts/new.py",
            "scripts/agents/removed.py",
            "config/engineering/python-quality.toml",
            "scripts/checks/repository/python/check_python_quality.py",
            "requirements-dev.txt",
        ):
            with self.subTest(path=path):
                self.assertIn("eng.repository.python-quality", self.selected([path]))

    def test_docs_only_does_not_select_language_quality(self):
        ids = self.selected(["docs/reference/hooks.md"])
        self.assertNotIn("eng.repository.python-quality", ids)
        self.assertNotIn("eng.backend.delivery", ids)

    def test_both_client_hook_configs_select_and_freeze_hook_check(self):
        for path in (".codex/hooks.json", ".qoder/settings.json"):
            with self.subTest(path=path):
                self.assertIn("eng.verification.module-tests", self.selected([path]))
                check = next(
                    c
                    for c in self.checks
                    if c["check_id"] == "eng.verification.module-tests"
                )
                self.assertIn(path, check["input_paths"])
                self.assertTrue(
                    any(
                        "scripts/triggers/run_stop_checks.py" == p
                        or "scripts/triggers/run_stop_checks.py".startswith(
                            p.rstrip("/") + "/"
                        )
                        for p in check["input_paths"]
                    )
                )

    def test_baseline_and_change_share_frozen_contract(self):
        by_id = {
            c["check_id"]: c
            for c in filter_checks_by_scope(self.checks, "repository-baseline")
        }
        changed_by_id = {
            c["check_id"]: c
            for c in filter_checks_by_scope(self.checks, "change-targeted")
        }
        for check_id in ("eng.backend.delivery", "eng.repository.python-quality"):
            base, change = by_id[check_id], changed_by_id[check_id]
            for key in (
                "command",
                "input_paths",
                "result_contract",
                "timeout_seconds",
                "required_environment",
            ):
                self.assertEqual(base[key], change[key], (check_id, key))
        self.assertIn(
            "config/engineering/python-quality.toml",
            by_id["eng.repository.python-quality"]["input_paths"],
        )
        self.assertIn(
            "scripts/checks/repository/python/check_python_quality.py",
            {
                item["locator"]
                for item in snapshot_check_inputs(
                    ROOT, by_id["eng.repository.python-quality"]
                )["files"]
            },
        )
        for check_id in (
            "eng.backend.delivery",
            "eng.verification.module-tests",
            "eng.release.lifecycle-runtime",
            "eng.release.candidate-runtime",
        ):
            with self.subTest(check_id=check_id):
                snapshot = snapshot_check_inputs(ROOT, by_id[check_id])
                self.assertEqual([], snapshot["missing"])
                self.assertIn(
                    "scripts/environment/services/isolated_postgres_redis.py",
                    {item["locator"] for item in snapshot["files"]},
                )


if __name__ == "__main__":
    unittest.main()
