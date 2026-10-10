"""日常选择与声明消费闭包逐项配对，防止触发覆盖静默漂移。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.scenarios import freeze_inputs
from scripts.verification.risk import formal_only_ids
from scripts.verification.scope import select_checks_for_changes
from scripts.verification.scope import ScopeError, resolve_module_dependencies

ROOT = Path(__file__).resolve().parents[2]


class DevelopmentSelectionMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]
        cls.by_id = {check["check_id"]: check for check in cls.checks}
        cls.targeted = [
            check for check in cls.checks if check["scope"] == "change-targeted"
        ]

    def selected(self, path):
        return {
            check["check_id"]
            for check in select_checks_for_changes(self.checks, [path])
        }

    def test_all_21_baseline_targeted_pairs_preserve_execution_and_input_contract(self):
        targeted = [check for check in self.checks if check["check_id"].endswith("-on-change")]
        self.assertEqual(24, len(targeted))
        self.assertEqual(24, len({check["check_id"] for check in targeted}))
        for change in targeted:
            if change["check_id"] in {
                "eng.release.docker-surface-on-change", "eng.release.docker-shell-on-change",
                "eng.release.docker-local-on-change", "eng.release.docker-node-on-change",
            }:
                continue
            base_id = change["check_id"].removesuffix("-on-change")
            with self.subTest(pair=base_id):
                self.assertIn(base_id, self.by_id)
                base = self.by_id[base_id]
                for key in (
                    "command",
                    "input_paths",
                    "result_contract",
                    "timeout_seconds",
                    "required_environment",
                ):
                    if key == "input_paths":
                        self.assertEqual(set(base[key]), set(change[key]), key)
                    else:
                        self.assertEqual(base[key], change[key], key)
        docker_groups = [item for item in targeted if item["check_id"].startswith("eng.release.docker-")]
        self.assertEqual(4, len(docker_groups))
        self.assertTrue(all(item["command"][:3] == ["python3", "ops/docker/tests/module_check_adapter.py", "--group"] for item in docker_groups))

    def test_every_declared_input_is_covered_by_its_targeted_triggers(self):
        for check in self.targeted:
            triggers = [trigger["path"].rstrip("/") for trigger in check["triggers"]]
            for input_path in check["input_paths"]:
                with self.subTest(check=check["check_id"], input=input_path):
                    normalized = input_path.rstrip("/")
                    self.assertTrue(
                        any(
                            normalized == trigger
                            or normalized.startswith(trigger + "/")
                            for trigger in triggers
                        ),
                        f"uncovered declared input: {input_path}",
                    )

    def test_docker_consumption_edges_are_frozen_by_consumers_not_only_surface(self):
        """共享源必须进入实际消费者输入与触发，不可由 surface 库存守门代替。"""
        baseline = self.by_id["eng.release.docker-contract"]
        groups = {name: self.by_id[f"eng.release.docker-{name}-on-change"] for name in ("surface", "shell", "local", "node")}
        for check in groups.values():
            self.assertTrue(set(check["input_paths"]) <= set(baseline["input_paths"]))
        self.assertIn("infra/local/compose.yaml", baseline["input_paths"])
        for group in ("local", "node"):
            check = groups[group]
            self.assertIn("infra/local/compose.yaml", check["input_paths"])
            self.assertIn("infra/postgres/schema.sql", check["input_paths"])
        self.assertIn("ops/release/lifecycle-docker.sh", groups["shell"]["input_paths"])
        self.assertTrue(any(path == "ops/release" or path.startswith("ops/release/") for path in groups["node"]["input_paths"]))
        self.assertNotIn("ops/release/lifecycle-docker.sh", groups["local"]["input_paths"])
        self.assertNotIn("ops/docker", groups["shell"]["input_paths"])
        self.assertNotIn("ops/docker", groups["local"]["input_paths"])
        self.assertNotIn("ops/docker", groups["node"]["input_paths"])
        for group in ("local", "node"):
            selected = self.selected("infra/local/compose.yaml")
            self.assertIn(groups[group]["check_id"], selected)

    def test_real_daily_freeze_selects_exact_scenario_consumers(self):
        expectations = {
            "docs/reference/hooks.md": {
                "eng.repository.docs-on-change",
            },
            "backend/product/api/src/main/java/io/lexiflow/api/example/Example.java": {
                "eng.backend.delivery-on-change",
                "eng.backend.build-contract-on-change",
                "eng.backend.delivery",
                "eng.extension.quality-on-change",
            },
            "backend/product/lexicon/src/main/java/io/lexiflow/lexicon/example/Entry.java": {
                "eng.backend.delivery-on-change",
                "eng.backend.build-contract-on-change",
                "eng.backend.delivery",
                "eng.extension.quality-on-change",
            },
            "infra/postgres/schema.sql": {
                "eng.backend.delivery-on-change",
                "eng.backend.delivery",
                "eng.extension.quality-on-change",
                "eng.release.docker-local-on-change", "eng.release.docker-node-on-change",
            },
            "backend/product/api/src/main/java/io/lexiflow/api/release/RuntimeHealthCommand.java": {
                "eng.backend.delivery-on-change",
                "eng.backend.build-contract-on-change",
                "eng.backend.delivery",
                "eng.extension.quality-on-change",
                "eng.release.docker-surface-on-change",
            },
            "scripts/environment/runtime.py": {
                "eng.repository.python-quality-on-change",
                "eng.backend.delivery-on-change",
                "eng.backend.build-contract-on-change",
                "eng.verification.module-tests-on-change",
                "eng.repository.module-tests-on-change",
                "eng.backend.delivery",
                "eng.extension.quality-on-change",
                "eng.agents.module-tests",
                "eng.verification.module-tests",
                "eng.delivery-gate.module-tests-on-change",
            },
            ".codex/hooks.json": {
                "eng.repository.hooks-on-change",
                "eng.verification.module-tests-on-change",
                "eng.agents.module-tests-on-change",
            },
            "harness/agent-policy.manifest.yaml": {
                "eng.repository.policy-on-change",
                "eng.repository.planning-on-change",
                "eng.agents.module-tests",
                "eng.agents.module-tests-on-change",
                "eng.verification.module-tests",
                "eng.verification.module-tests-on-change",
                "eng.delivery-gate.module-tests-on-change",
            },
            "scripts/verification/development.py": {
                "eng.repository.python-quality-on-change",
                "eng.verification.module-tests-on-change",
                "eng.repository.module-tests-on-change",
                "eng.agents.module-tests",
                "eng.verification.module-tests",
                "eng.delivery-gate.module-tests-on-change",
            },
            "tests/verification/test_development.py": {
                "eng.verification.module-tests-on-change",
                "eng.repository.module-tests-on-change",
            },
            "backend/build.gradle.kts": {
                "eng.release.version-on-change",
                "eng.backend.delivery-on-change",
                "eng.backend.build-contract-on-change",
                "eng.backend.delivery",
                "eng.extension.quality-on-change",
            },
            "ops/docker/tests/test_contract.py": {
                "eng.release.docker-surface-on-change",
            },
            "ops/docker/tests/test_podman_local.py": {
                "eng.release.docker-surface-on-change", "eng.release.docker-local-on-change",
            },
            "ops/release/lifecycle-docker.sh": {
                "eng.release.docker-surface-on-change", "eng.release.docker-shell-on-change",
                "eng.release.docker-node-on-change", "eng.backend.build-contract-on-change",
                "eng.release.lifecycle-install-on-change", "eng.release.lifecycle-on-change",
                "eng.release.build-on-change", "eng.agents.module-tests",
                "eng.delivery-gate.module-tests-on-change", "eng.verification.module-tests-on-change",
                "eng.verification.module-tests",
            },
            "ops/podman/command.mjs": {
                "eng.release.docker-surface-on-change", "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
            },
        }
        for path, expected in expectations.items():
            with self.subTest(path=path):
                frozen = freeze_inputs(
                    ROOT,
                    verification_scope="development-change",
                    base="HEAD",
                    _subject_changed_files=[path],
                )
                self.assertEqual("PASS", frozen["result"], frozen)
                self.assertEqual(
                    expected, {check["check_id"] for check in frozen["checks"]}
                )

    def test_deleted_files_and_new_descendants_still_match_directory_triggers(self):
        # Selection receives normalized changed paths for additions, edits, and deletions.
        self.assertIn("eng.backend.delivery-on-change", self.selected("backend/product/api/src/new/File.java"))
        self.assertIn("eng.backend.delivery-on-change", self.selected("backend/product/api/src/removed/File.java"))

    def test_real_new_and_deleted_docker_files_change_surface_freeze(self):
        """真实文件增删改变 surface 冻结，不用只改声明模拟。"""
        for relative in ("ops/docker/zz-selection-source-audit.tmp", "ops/docker/tests/test_zz_selection_audit.py"):
            path = ROOT / relative
            self.assertFalse(path.exists())
            try:
                path.write_text("synthetic, not executable\n")
                added = freeze_inputs(ROOT, verification_scope="development-change", base="HEAD",
                                      _subject_changed_files=[relative])
                self.assertEqual("PASS", added["result"], added)
                self.assertEqual({"eng.release.docker-surface-on-change"},
                                 {check["check_id"] for check in added["checks"] if check["check_id"].startswith("eng.release.docker-")})
                path.unlink()
                deleted = freeze_inputs(ROOT, verification_scope="development-change", base="HEAD",
                                        _subject_changed_files=[relative])
                self.assertEqual("PASS", deleted["result"], deleted)
                self.assertNotEqual(added["input_fingerprint"], deleted["input_fingerprint"])
            finally:
                path.unlink(missing_ok=True)

    def test_cross_module_source_consumers_have_trigger_and_exact_daily_freeze(self):
        # These paths come from the actual E2E startup, Gate imports, and fixture copy.
        cases = {
            "scripts/verification/risk.py": {
                "eng.agents.module-tests", "eng.delivery-gate.module-tests-on-change",
                "eng.repository.module-tests-on-change", "eng.repository.python-quality-on-change",
                "eng.verification.module-tests", "eng.verification.module-tests-on-change",
            },
            "scripts/agents/local_codex_runtime.py": {
                "eng.agents.module-tests", "eng.agents.module-tests-on-change",
                "eng.delivery-gate.module-tests-on-change", "eng.repository.module-tests-on-change",
                "eng.repository.python-quality-on-change", "eng.verification.module-tests",
                "eng.verification.module-tests-on-change",
            },
            "tests/delivery_gate/fixtures.py": {
                "eng.agents.module-tests", "eng.delivery-gate.module-tests-on-change",
                "eng.repository.module-tests-on-change", "eng.verification.module-tests",
                "eng.verification.module-tests-on-change",
            },
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                frozen = freeze_inputs(
                    ROOT, verification_scope="development-change", base="HEAD",
                    _subject_changed_files=[path],
                )
                self.assertEqual("PASS", frozen["result"], frozen)
                self.assertEqual(expected, {item["check_id"] for item in frozen["checks"]})

        # Static consumer map is deliberately bounded to observed imports/copies.
        consumer_sources = {
            "eng.delivery-gate.module-tests-on-change": (
                "scripts/verification/", "scripts/agents/",
                "harness/agent-policy.manifest.yaml", "harness/agent-runtime.manifest.yaml",
            ),
            "eng.extension.quality-on-change": ("backend/", "infra/postgres/"),
        }
        for check_id, sources in consumer_sources.items():
            triggers = [x["path"].rstrip("/") for x in self.by_id[check_id]["triggers"]]
            for source in sources:
                with self.subTest(check=check_id, source=source):
                    normalized = source.rstrip("/")
                    self.assertTrue(any(
                        normalized == trigger or normalized.startswith(trigger + "/")
                        for trigger in triggers
                    ), f"consumer source lacks trigger: {source}")

    def test_python_cross_module_imports_are_frozen_and_select_the_consumer(self):
        """静态导入递归覆盖消费者；动态配置与进程启动另由显式场景核对。"""
        formal = formal_only_ids(ROOT)
        imports = {}
        selections = {}
        for module in ("verification", "agents", "delivery_gate", "repository"):
            check_id = f"eng.{module.replace('_', '-')}.module-tests"
            dependencies = resolve_module_dependencies([self.by_id[check_id]], self.checks)
            inputs = {
                path.rstrip("/")
                for check in dependencies
                for path in check["input_paths"]
            }
            pending = {
                str(path.relative_to(ROOT))
                for folder in (f"scripts/{module}", f"tests/{module}")
                for path in (ROOT / folder).rglob("*.py")
            }
            visited = set()
            while pending:
                source = pending.pop()
                if source in visited:
                    continue
                visited.add(source)
                if source not in imports:
                    imports[source] = set()
                    for node in ast.walk(ast.parse((ROOT / source).read_text())):
                        names = []
                        if isinstance(node, ast.Import):
                            names = [alias.name for alias in node.names]
                        elif isinstance(node, ast.ImportFrom) and node.module:
                            names = [node.module] + [
                                f"{node.module}.{alias.name}" for alias in node.names
                                if alias.name != "*"
                            ]
                        for name in names:
                            if not name.startswith(("scripts.", "tests.")):
                                continue
                            path = name.replace(".", "/")
                            for candidate in (f"{path}.py", f"{path}/__init__.py"):
                                if (ROOT / candidate).is_file():
                                    imports[source].add(candidate)
                                    break
                pending.update(imports[source] - visited)
            for source in sorted(visited):
                with self.subTest(consumer=check_id, source=source):
                    self.assertTrue(any(
                        source == path or source.startswith(path + "/")
                        for path in inputs
                    ), f"consumer input is not frozen: {source}")
                    if source not in selections:
                        selected = [
                            check for check in select_checks_for_changes(self.checks, [source])
                            if check["check_id"] not in formal
                        ]
                        selections[source] = {
                            check["check_id"].removesuffix("-on-change")
                            for check in resolve_module_dependencies(selected, self.checks)
                        }
                    self.assertIn(check_id, selections[source])

    def test_unknown_dependency_and_dependency_cycle_fail_closed(self):
        with self.assertRaisesRegex(ScopeError, "unknown-module-dependency"):
            resolve_module_dependencies(
                [
                    {
                        "check_id": "selected",
                        "module": "selected-module",
                        "module_dependencies": ["missing-module"],
                    }
                ],
                [],
            )
        cyclic = [
            {
                "check_id": "first",
                "module": "first-module",
                "module_dependencies": ["second-module"],
            },
            {
                "check_id": "second",
                "module": "second-module",
                "module_dependencies": ["first-module"],
            },
        ]
        with self.assertRaisesRegex(ScopeError, "module-dependency-cycle"):
            resolve_module_dependencies([cyclic[0]], cyclic)


if __name__ == "__main__":
    unittest.main()
