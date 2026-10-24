"""日常选择与声明消费闭包逐项配对，防止触发覆盖静默漂移。"""

from __future__ import annotations

import ast
import unittest
import tempfile
from pathlib import Path

from tests.verification.declaration_fixtures import input_closure
from scripts.verification.planning.check_definitions import (
    filter_checks_by_scope,
    load_declarations_snapshot,
)
from scripts.verification.execution.check_runner import snapshot_check_inputs
from scripts.verification.planning.change_risk_assessment import formal_only_ids
from scripts.verification.planning.check_scope_selection import (
    select_checks_for_changes,
)
from scripts.verification.planning.check_scope_selection import (
    ScopeError,
    resolve_module_dependencies,
)
from scripts.verification import freeze_inputs
from scripts.triggers.run_ci_quick_checks import make_plan

ROOT = Path(__file__).resolve().parents[2]


class DevelopmentSelectionMatrixTest(unittest.TestCase):
    """逐项验证规范操作、场景触发和真实消费者闭包。"""

    @classmethod
    def setUpClass(cls):
        """加载唯一规范声明，并分别建立真实场景视图。"""
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]
        cls.by_id = {check["check_id"]: check for check in cls.checks}
        cls.by_scope = {
            scope: {
                check["check_id"]: check
                for check in filter_checks_by_scope(cls.checks, scope)
            }
            for scope in ("repository-baseline", "change-targeted")
        }
        cls.targeted = filter_checks_by_scope(cls.checks, "change-targeted")

    def selected(self, path):
        """返回单个变更路径命中的规范 Check 集合。"""
        return {
            check["check_id"]
            for check in select_checks_for_changes(self.checks, [path])
        }

    def frozen_ids(self, path):
        """冻结真实输入闭包并返回含模块依赖的最终 Check 集合。"""
        report = freeze_inputs(
            ROOT,
            verification_scope="development-change",
            base="HEAD",
            _subject_changed_files=[path],
        )
        self.assertEqual("PASS", report["result"], report)
        return {item["check_id"] for item in report["checks"]}

    def test_canonical_checks_and_scene_views_preserve_execution_and_input_contract(
        self,
    ):
        """场景视图只提供资格与触发，不复制或更改实际操作。"""
        self.assertEqual(23, len(self.checks))
        self.assertEqual(23, len(self.by_id))
        self.assertEqual(19, len(self.by_scope["repository-baseline"]))
        self.assertEqual(22, len(self.targeted))
        for canonical in self.checks:
            self.assertNotIn("scope", canonical)
            self.assertNotIn("module", canonical)
            for scope, by_id in self.by_scope.items():
                if scope not in canonical["scenarios"]:
                    self.assertNotIn(canonical["check_id"], by_id)
                    continue
                view = by_id[canonical["check_id"]]
                with self.subTest(check=canonical["check_id"], scope=scope):
                    self.assertEqual(scope, view["scope"])
                    self.assertEqual(
                        canonical["triggers"] if scope == "change-targeted" else [],
                        view["triggers"],
                    )
                    for key in (
                        "command",
                        "input_paths",
                        "result_contract",
                        "timeout_seconds",
                        "required_environment",
                    ):
                        self.assertEqual(canonical[key], view[key], key)
        docker_groups = [
            item
            for item in self.targeted
            if item["check_id"].startswith("eng.release.docker-")
        ]
        self.assertEqual(4, len(docker_groups))
        self.assertTrue(
            all(
                item["command"][:3]
                == [
                    "python3",
                    "scripts/checks/deployment/run_deployment_contract_tests.py",
                    "--group",
                ]
                for item in docker_groups
            )
        )

    def test_trigger_matrix_tracks_real_consumers_and_fails_closed_for_unknown_paths(
        self,
    ):
        """触发按真实消费者收窄；冻结输入闭包仍保留，未知源不猜测覆盖。"""
        docs = "ops/release/third-party-notices.md"
        licenses = "ops/release/distribution-licenses.json"
        cases = {
            docs: {"eng.release.manifest"},
            licenses: {"eng.release.manifest"},
            "LICENSE": {"eng.release.version"},
            "ops/release/version.txt": {
                "eng.release.version",
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.extension.quality",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
            },
            "ops/release/integrity/source-build-identity.mjs": {
                "eng.release.version",
                "eng.backend.delivery",
                "eng.backend.build-contract",
                "eng.extension.quality",
                "eng.release.manifest",
                "eng.release.build",
                "eng.release.runtime-entry",
                "eng.release.lifecycle",
                "eng.release.lifecycle-install",
                "eng.release.docker-surface-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.delivery-gate.module-tests",
                "eng.agents.module-tests",
                "eng.repository.python-quality",
                "eng.verification.module-tests",
            },
            "ops/release/build/package-release-artifacts.mjs": {
                "eng.release.version",
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.extension.quality",
                "eng.release.build",
                "eng.release.manifest",
                "eng.release.lifecycle",
                "eng.release.lifecycle-install",
                "eng.release.runtime-entry",
                "eng.release.docker-surface-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.delivery-gate.module-tests",
                "eng.agents.module-tests",
                "eng.repository.python-quality",
                "eng.verification.module-tests",
            },
            "backend/product/api/src/main/java/io/lexiflow/api/example/Example.java": {
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.extension.quality",
                "eng.release.docker-surface-on-change",
            },
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(expected, self.frozen_ids(path))
        for path in (docs, licenses, "LICENSE"):
            selected = self.selected(path)
            self.assertNotIn("eng.backend.delivery", selected)
            self.assertNotIn("eng.backend.build-contract", selected)
            self.assertNotIn("eng.extension.quality", selected)
        unknown = freeze_inputs(
            ROOT,
            verification_scope="development-change",
            base="HEAD",
            _subject_changed_files=["ops/release/new-unclassified-source.bin"],
        )
        self.assertEqual("FAIL", unknown["result"])
        self.assertEqual("coverage-gap", unknown["reason"])
        # Trigger contraction must not weaken frozen consumer input closure.
        for check in self.checks:
            self.assertTrue(check["input_paths"], check["check_id"])

    def test_release_python_package_initializer_triggers_its_consumers(self):
        """Python 包初始化执行实际消费检查，不因身份变化扩大 Java／扩展检查。"""
        self.assertEqual(
            {
                "eng.repository.python-quality",
                "eng.verification.module-tests",
                "eng.agents.module-tests",
                "eng.delivery-gate.module-tests",
            },
            self.frozen_ids("ops/release/__init__.py"),
        )
        selected = self.selected("ops/release/__init__.py")
        self.assertIn("eng.release.lifecycle-runtime", selected)
        self.assertIn("eng.release.candidate-runtime", selected)
        self.assertNotIn("eng.backend.delivery", selected)
        self.assertNotIn("eng.extension.quality", selected)

    def test_ci_all_source_inventory_has_complete_trigger_coverage(self):
        """首推分支的 --all 真实清单可规划，不执行检查或获得正式资格。"""
        plan = make_plan(ROOT, all_paths=True)
        self.assertEqual("PASS", plan["status"])
        self.assertEqual("all", plan["base"])
        self.assertEqual(0, plan["checks_executed"])
        self.assertFalse(plan["formal_eligible"])
        self.assertTrue(plan["selected_check_ids"])
        self.assertIn("ops/release/__init__.py", plan["changed_paths"])

    def test_docker_consumption_edges_are_frozen_by_consumers_not_only_surface(self):
        """共享源必须进入实际消费者输入与触发，不可由 surface 库存守门代替。"""
        baseline = self.by_id["eng.release.docker-contract"]
        groups = {
            name: self.by_id[f"eng.release.docker-{name}-on-change"]
            for name in ("surface", "shell", "local", "node")
        }
        baseline_closure = input_closure(ROOT, baseline)
        for check in groups.values():
            self.assertTrue(input_closure(ROOT, check) <= baseline_closure)
        self.assertIn("infra/local/compose.yaml", baseline["input_paths"])
        for group in ("local", "node"):
            check = groups[group]
            self.assertIn("infra/local/compose.yaml", check["input_paths"])
            self.assertIn("infra/postgres/schema.sql", check["input_paths"])
        self.assertIn(
            "ops/release/runtime/release-installation-docker.sh",
            input_closure(ROOT, groups["shell"]),
        )
        self.assertTrue(
            any(
                path == "ops/release" or path.startswith("ops/release/")
                for path in groups["node"]["input_paths"]
            )
        )
        self.assertEqual(groups["local"]["command"][-1], "local")
        self.assertIn("ops/docker", groups["shell"]["input_paths"])
        self.assertIn("ops/docker", groups["local"]["input_paths"])
        self.assertIn("ops/docker", groups["node"]["input_paths"])
        for group in ("local", "node"):
            selected = self.selected("infra/local/compose.yaml")
            self.assertIn(groups[group]["check_id"], selected)

    def test_real_daily_freeze_selects_exact_scenario_consumers(self):
        """真实日常冻结精确选择消费者和规范 Check 依赖闭包。"""
        expectations = {
            "docs/reference/hooks.md": {
                "eng.repository.docs",
            },
            "backend/product/api/src/main/java/io/lexiflow/api/example/Example.java": {
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.extension.quality",
                "eng.release.docker-surface-on-change",
            },
            "backend/product/lexicon/src/main/java/io/lexiflow/lexicon/example/Entry.java": {
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.extension.quality",
            },
            "infra/postgres/schema.sql": {
                "eng.backend.delivery",
                "eng.extension.quality",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
            },
            "backend/product/api/src/main/java/io/lexiflow/api/runtime/ContainerHealthController.java": {
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.extension.quality",
                "eng.release.docker-surface-on-change",
            },
            "scripts/environment/configuration/verification_environment.py": {
                "eng.agents.module-tests",
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.delivery-gate.module-tests",
                "eng.extension.quality",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-surface-on-change",
                "eng.repository.module-tests",
                "eng.repository.python-quality",
                "eng.verification.module-tests",
            },
            ".codex/hooks.json": {
                "eng.agents.module-tests",
                "eng.verification.module-tests",
            },
            "config/engineering/risk.yaml": {
                "eng.agents.module-tests",
                "eng.delivery-gate.module-tests",
                "eng.repository.module-tests",
                "eng.verification.module-tests",
            },
            "scripts/verification/commands/run_development_checks.py": {
                "eng.agents.module-tests",
                "eng.delivery-gate.module-tests",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-surface-on-change",
                "eng.repository.module-tests",
                "eng.repository.python-quality",
                "eng.verification.module-tests",
            },
            "tests/verification/test_development.py": {
                "eng.repository.module-tests",
                "eng.verification.module-tests",
            },
            "backend/build.gradle.kts": {
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.extension.quality",
                "eng.release.version",
            },
            "ops/docker/tests/test_contract.py": {
                "eng.release.build",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-surface-on-change",
                "eng.release.lifecycle-install",
                "eng.release.runtime-entry",
            },
            "ops/docker/tests/test_podman_local.py": {
                "eng.release.build",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-surface-on-change",
                "eng.release.lifecycle-install",
                "eng.release.runtime-entry",
            },
            "ops/docker/Dockerfile.runtime": {
                "eng.release.build",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-surface-on-change",
                "eng.release.lifecycle",
                "eng.release.lifecycle-install",
                "eng.release.runtime-entry",
            },
            "ops/docker/runtime-entrypoint.sh": {
                "eng.release.build",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-surface-on-change",
                "eng.release.lifecycle",
                "eng.release.lifecycle-install",
                "eng.release.runtime-entry",
            },
            "scripts/acceptance/evidence/acceptance_actor_identity.py": {
                "eng.agents.module-tests",
                "eng.delivery-gate.module-tests",
                "eng.repository.module-tests",
                "eng.repository.python-quality",
                "eng.verification.module-tests",
            },
            "ops/podman/execution/deployment-process-control.mjs": {
                "eng.backend.build-contract",
                "eng.backend.delivery",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.release.docker-surface-on-change",
            },
        }
        for path, expected in expectations.items():
            with self.subTest(path=path):
                self.assertEqual(expected, self.frozen_ids(path))

    def test_deleted_files_and_new_descendants_still_match_directory_triggers(self):
        # Selection receives normalized changed paths for additions, edits, and deletions.
        """新增与删除的子路径都匹配目录触发器。"""
        self.assertIn(
            "eng.backend.delivery",
            self.selected("backend/product/api/src/new/File.java"),
        )
        self.assertIn(
            "eng.backend.delivery",
            self.selected("backend/product/api/src/removed/File.java"),
        )

    def test_new_and_deleted_docker_files_change_surface_freeze(self):
        """隔离目录中的真实文件增删改变 surface 快照，不写其他归属路径。"""
        check = self.by_id["eng.release.docker-surface-on-change"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in check["input_paths"]:
                target = root / name
                if (ROOT / name).is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("synthetic source\n")
            for relative in (
                "ops/docker/zz-selection-source-audit.tmp",
                "ops/docker/tests/test_zz_selection_audit.py",
            ):
                with self.subTest(path=relative):
                    before = snapshot_check_inputs(root, check)
                    self.assertEqual([], before["missing"])
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("synthetic, not executable\n")
                    added = snapshot_check_inputs(root, check)
                    self.assertNotEqual(before["fingerprint"], added["fingerprint"])
                    self.assertEqual(
                        {
                            f"eng.release.docker-{group}-on-change"
                            for group in ("surface", "shell", "local", "node")
                        },
                        {
                            identifier
                            for identifier in self.selected(relative)
                            if identifier.startswith("eng.release.docker-")
                        },
                    )
                    path.unlink()
                    self.assertEqual(
                        before["fingerprint"],
                        snapshot_check_inputs(root, check)["fingerprint"],
                    )

    def test_cross_module_source_consumers_have_trigger_and_exact_daily_freeze(self):
        # These paths come from the actual E2E startup, Gate imports, and fixture copy.
        """实际跨模块来源既触发消费者，也进入冻结选择闭包。"""
        cases = {
            "scripts/verification/planning/change_risk_assessment.py": {
                "eng.release.docker-surface-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
                "eng.delivery-gate.module-tests",
                "eng.agents.module-tests",
                "eng.repository.module-tests",
                "eng.repository.python-quality",
                "eng.verification.module-tests",
            },
            "scripts/agents/identity/codex_actor_identity.py": {
                "eng.agents.module-tests",
                "eng.delivery-gate.module-tests",
                "eng.repository.module-tests",
                "eng.repository.python-quality",
                "eng.verification.module-tests",
            },
            "tests/delivery_gate/fixtures.py": {
                "eng.delivery-gate.module-tests",
                "eng.agents.module-tests",
                "eng.repository.module-tests",
                "eng.verification.module-tests",
            },
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(expected, self.frozen_ids(path))

        # Static consumer map is deliberately bounded to observed imports/copies.
        consumer_sources = {
            "eng.delivery-gate.module-tests": (
                "scripts/verification/",
                "scripts/agents/",
                "config/engineering/risk.yaml",
            ),
            "eng.extension.quality": ("backend/", "infra/postgres/"),
        }
        for check_id, sources in consumer_sources.items():
            triggers = [
                x["path"].rstrip("/")
                for x in self.by_scope["change-targeted"][check_id]["triggers"]
            ]
            for source in sources:
                with self.subTest(check=check_id, source=source):
                    normalized = source.rstrip("/")
                    self.assertTrue(
                        any(
                            normalized == trigger
                            or normalized.startswith(trigger + "/")
                            for trigger in triggers
                        ),
                        f"consumer source lacks trigger: {source}",
                    )

    def test_python_cross_module_imports_are_frozen_and_select_the_consumer(self):
        """静态导入递归覆盖消费者；动态配置与进程启动另由显式场景核对。"""
        formal = formal_only_ids(ROOT)
        imports = {}
        selections = {}
        for module in ("verification", "agents", "delivery_gate", "repository"):
            check_id = f"eng.{module.replace('_', '-')}.module-tests"
            dependencies = resolve_module_dependencies(
                [self.by_id[check_id]], self.checks
            )
            inputs = {
                path.rstrip("/")
                for check in dependencies
                for path in check["input_paths"]
            }
            pending = {
                str(path.relative_to(ROOT))
                for folder in (
                    {
                        "delivery_gate": "scripts/acceptance",
                        "repository": "scripts/checks/repository",
                    }.get(module, f"scripts/{module}"),
                    f"tests/{module}",
                )
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
                                f"{node.module}.{alias.name}"
                                for alias in node.names
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
                    self.assertTrue(
                        any(
                            source == path or source.startswith(path + "/")
                            for path in inputs
                        ),
                        f"consumer input is not frozen: {source}",
                    )
                    if source not in selections:
                        selected = [
                            check
                            for check in select_checks_for_changes(
                                self.checks, [source]
                            )
                            if check["check_id"] not in formal
                        ]
                        selections[source] = {
                            check["check_id"]
                            for check in resolve_module_dependencies(
                                selected, self.checks
                            )
                        }
                    self.assertIn(check_id, selections[source])

    def test_unknown_dependency_and_dependency_cycle_fail_closed(self):
        """未知规范 Check 依赖及依赖环均显式拒绝。"""
        with self.assertRaisesRegex(ScopeError, "unknown-check-dependency"):
            resolve_module_dependencies(
                [
                    {
                        "check_id": "selected",
                        "module_dependencies": ["missing-module"],
                    }
                ],
                [],
            )
        cyclic = [
            {
                "check_id": "first",
                "module_dependencies": ["second"],
            },
            {
                "check_id": "second",
                "module_dependencies": ["first"],
            },
        ]
        with self.assertRaisesRegex(ScopeError, "module-dependency-cycle"):
            resolve_module_dependencies([cyclic[0]], cyclic)


if __name__ == "__main__":
    unittest.main()
