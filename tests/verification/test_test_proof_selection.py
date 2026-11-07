"""证明测试归属和运行前置的收敛，不削弱未知覆盖与冻结拒绝。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from scripts.checks.tool_tests.run_python_tool_tests import SUITES
from scripts.verification import freeze_inputs
from scripts.verification.execution.check_runner import snapshot_check_inputs
from scripts.verification.planning.check_definitions import load_declarations
from scripts.verification.planning.check_scope_selection import (
    resolve_module_dependencies,
    select_checks_for_changes,
)

ROOT = Path(__file__).resolve().parents[2]


class TestProofSelectionTest(unittest.TestCase):
    """检查选择是静态事实，不能伪装为已执行的验证或耗时。"""

    @classmethod
    def setUpClass(cls):
        cls.declarations = load_declarations(ROOT)
        cls.checks = cls.declarations["checks"]
        cls.by_id = {c["check_id"]: c for c in cls.checks}

    def selected(self, path):
        formal = set(self.declarations["scenario_policy"]["ci_quick"]["formal_only_check_ids"])
        selected = [c for c in select_checks_for_changes(self.checks, [path]) if c["check_id"] not in formal]
        return {c["check_id"] for c in resolve_module_dependencies(selected, self.checks)}

    def test_representative_changes_keep_only_behavior_consumers(self):
        cases = {
            "extension/src/content.ts": {"eng.extension.quality"},
            "ops/release/workflow/promote_verified_release.py": {
                "eng.repository.python-quality", "eng.release.python-tests",
            },
            "scripts/verification/reports/verification_report_summary.py": {
                "eng.repository.python-quality", "eng.verification.module-tests",
            },
            "ops/podman/tests/logs.test.mjs": {
                "eng.release.docker-surface-on-change", "eng.release.docker-node-on-change",
            },
            "docs/reference/hooks.md": {"eng.repository.docs"},
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(expected, self.selected(path))

    def test_backend_changes_still_require_full_delivery_and_real_browser(self):
        for path in (
            "backend/product/api/src/main/java/New.java",
            "backend/product/lexicon/src/main/java/Deleted.java",
            "backend/settings.gradle.kts",
            "ops/release/integrity/source-build-identity.mjs",
        ):
            with self.subTest(path=path):
                self.assertTrue({"eng.backend.delivery", "eng.backend.build-contract", "eng.extension.quality"} <= self.selected(path))
        self.assertEqual([], self.by_id["eng.extension.quality"]["module_dependencies"])
        self.assertEqual([], self.by_id["eng.delivery-gate.module-tests"]["module_dependencies"])

    def test_library_changes_still_select_provider_and_consumer_tests(self):
        self.assertTrue({"eng.agents.module-tests", "eng.delivery-gate.module-tests"} <= self.selected("scripts/agents/identity/codex_actor_identity.py"))
        self.assertTrue({"eng.verification.module-tests", "eng.delivery-gate.module-tests"} <= self.selected("scripts/verification/execution/check_runner.py"))
        self.assertIn("eng.release.python-tests", self.selected("tests/release/test_new_release_behavior.py"))
        self.assertNotIn("eng.verification.module-tests", self.selected("tests/release/test_new_release_behavior.py"))

        # 真实仓库断言与可执行 fixture 不能因 suite 迁移而丢掉消费者。
        from tests.verification.declaration_fixtures import input_closure
        release = self.by_id["eng.release.python-tests"]
        for path in (
            "LICENSE",
            "ops/release/distribution-licenses.json",
            "ops/release/notes/TEMPLATE.md",
            "backend/gradle/build-logic/src/main/kotlin/io/lexiflow/buildlogic/GenerateDataGenerationTask.kt",
            "backend/product/api/build.gradle.kts",
            "extension/scripts/packaging/package-extension-release.mjs",
            "extension/scripts/build/expected-dataset.mjs",
        ):
            with self.subTest(consumer_source=path):
                self.assertIn("eng.release.python-tests", self.selected(path))
                self.assertIn(path, input_closure(ROOT, release))

    def test_unknown_paths_remain_coverage_errors(self):
        for path in ("ops/release/new-unclassified-source.bin", "config/engineering/unknown.yaml", "unknown/source.py"):
            with self.subTest(path=path):
                result = freeze_inputs(ROOT, verification_scope="development-change", base="HEAD", _subject_changed_files=[path])
                self.assertEqual("FAIL", result["result"])
                self.assertEqual("coverage-gap", result["reason"])

    def test_directory_snapshots_bind_new_deleted_and_modified_consumer_files(self):
        check = self.by_id["eng.extension.quality"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in check["input_paths"]:
                target = root / name
                if (ROOT / name).is_dir():
                    target = target / "synthetic-source.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic source\n")
            before = snapshot_check_inputs(root, check)
            self.assertEqual([], before["missing"])
            source = root / "backend/product/api/src/main/java/Synthetic.java"
            source.parent.mkdir(parents=True)
            source.write_text("synthetic new source\n")
            added = snapshot_check_inputs(root, check)
            self.assertNotEqual(before["fingerprint"], added["fingerprint"])
            source.write_text("synthetic changed source\n")
            changed = snapshot_check_inputs(root, check)
            self.assertNotEqual(added["fingerprint"], changed["fingerprint"])
            source.unlink()
            self.assertEqual(before["fingerprint"], snapshot_check_inputs(root, check)["fingerprint"])

    def test_python_suites_have_disjoint_complete_discovery_ownership(self):
        def cases(suite):
            for item in suite:
                if isinstance(item, unittest.TestSuite):
                    yield from cases(item)
                else:
                    yield item

        all_ids = []
        for name, start in SUITES.items():
            loader = unittest.TestLoader()
            with contextlib.redirect_stderr(io.StringIO()):
                discovered = list(cases(loader.discover(str(ROOT / start), top_level_dir=str(ROOT))))
            self.assertEqual([], loader.errors, name)
            self.assertTrue(discovered, name)
            ids = [case.id() for case in discovered]
            self.assertEqual(len(ids), len(set(ids)), name)
            self.assertTrue(all(case.__class__.__module__.startswith(start.replace("/", ".") + ".") for case in discovered), name)
            all_ids.extend(ids)
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertFalse(any("gradle_build_contract" in name for name in all_ids))


if __name__ == "__main__":
    unittest.main()
