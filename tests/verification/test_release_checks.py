"""版本发布 Check 的实际声明、冻结闭包和完整性结果合同回归。"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verification import freeze_inputs
from tests.verification.test_scenarios import verify_repository
from tests.verification.declaration_fixtures import (
    input_member,
    input_closure,
    trigger_covers,
)
from scripts.verification.planning.check_definitions import (
    filter_checks_by_scope,
    load_declarations_snapshot,
)
from scripts.verification.execution.check_runner import snapshot_check_inputs
from scripts.verification.planning.check_scope_selection import (
    select_checks_for_changes,
)
from tests.verification.declaration_fixtures import write_declarations

ROOT = Path(__file__).resolve().parents[2]
BASE = "eng.release.version"


class ReleaseCheckDeclarationTest(unittest.TestCase):
    """使用真实声明验证版本源码不能脱离标准 Verify。"""

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

    def selected(self, path):
        """返回单一变动路径触发的实际检查。"""
        return {
            check["check_id"]
            for check in select_checks_for_changes(self.checks, [path])
        }

    def test_version_sources_select_release_and_backend_consumers(self):
        """解析器、单测、入口都触发检查，版本文件同时触发后端。"""
        for path in self.by_id[BASE]["input_paths"]:
            self.assertIn(BASE, self.selected(input_member(ROOT, path)), path)
        self.assertIn("eng.backend.delivery", self.selected("ops/release/version.txt"))
        self.assertNotIn(BASE, self.selected("docs/README.md"))
        for scope in ("repository-baseline", "change-targeted"):
            self.assertIn(
                "ops/release/version.txt",
                input_closure(ROOT, self.by_scope[scope]["eng.backend.delivery"]),
            )

    def test_scopes_share_command_environment_and_result_contract(self):
        """两种 scope 共享输入和失败关闭结果，不借用 Java 模块运行 Node。"""
        base, change = (
            self.by_scope[scope][BASE]
            for scope in ("repository-baseline", "change-targeted")
        )
        for key in (
            "command",
            "required_environment",
            "input_paths",
            "result_contract",
        ):
            self.assertEqual(base[key], change[key], key)
        self.assertEqual(
            ["node", "ops/release/test_tools/run-release-tool-tests.mjs"],
            base["command"],
        )
        self.assertEqual(["node", "git"], base["required_environment"])
        self.assertEqual([], base["module_dependencies"])
        self.assertEqual("json-stdout", base["result_contract"]["type"])

    def test_extension_consumers_select_and_freeze_shared_version_sources(self):
        """统一版本及读取器变动必须触发扩展，并进入两种检查的冻结闭包。"""
        base = self.by_scope["repository-baseline"]["eng.extension.quality"]
        change = self.by_scope["change-targeted"]["eng.extension.quality"]
        self.assertEqual(base["input_paths"], change["input_paths"])
        for path in (
            "ops/release/version.txt",
            "ops/release/integrity/source-build-identity.mjs",
        ):
            self.assertIn("eng.extension.quality", self.selected(path))
            for check in (base, change):
                self.assertIn(path, input_closure(ROOT, check))
                self.assertTrue(trigger_covers(self.by_id[check["check_id"]], path))

    def fixture(self, root):
        """创建与实际声明路径相同的合成冻结输入。"""
        checks = [copy.deepcopy(self.by_id[key]) for key in (BASE,)]
        (root / "harness").mkdir()
        write_declarations(root / "config/engineering/module-checks.yaml", checks)
        for name in checks[0]["input_paths"]:
            path = root / name
            if (ROOT / name).is_dir():
                path = path / "synthetic.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic input\n")

    def test_backend_build_launchers_are_frozen(self):
        """真实构建启动脚本及 Wrapper 字节参与冻结，触发检查不能替代哈希绑定。"""
        files = (
            "backend/gradlew",
            "backend/gradle/wrapper/gradle-wrapper.jar",
            "backend/gradle/wrapper/gradle-wrapper.properties",
        )
        base = self.by_scope["repository-baseline"]["eng.backend.delivery"]
        change = self.by_scope["change-targeted"]["eng.backend.delivery"]
        self.assertEqual(base["input_paths"], change["input_paths"])
        base_closure = input_closure(ROOT, base)
        for name in files:
            self.assertIn(name, base_closure)
            self.assertIn("eng.backend.delivery", self.selected(name))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in base["input_paths"]:
                target = root / name
                if (ROOT / name).is_dir():
                    target = target / "synthetic.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic build input\n")
            for name in files:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic launcher input\n")
            previous = snapshot_check_inputs(root, base)
            self.assertEqual([], previous["missing"])
            for name in files:
                target = root / name
                target.write_bytes(target.read_bytes() + b"changed\n")
                current = snapshot_check_inputs(root, base)
                self.assertNotEqual(
                    previous["fingerprint"], current["fingerprint"], name
                )
                self.assertIn(name, {item["locator"] for item in current["files"]})
                previous = current

    def test_each_declared_input_changes_freeze(self):
        """版本、解析器及测试都参与冻结哈希，不仅影响选择。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            previous = freeze_inputs(root)
            self.assertEqual("PASS", previous["result"], previous)
            for name in self.by_id[BASE]["input_paths"]:
                path = root / name
                if path.is_dir():
                    path = path / "synthetic.txt"
                path.write_text(path.read_text() + "changed\n")
                current = freeze_inputs(root)
                self.assertEqual("PASS", current["result"], current)
                self.assertNotEqual(
                    previous["input_fingerprint"], current["input_fingerprint"], name
                )
                previous = current

    def test_runtime_selects_and_freezes_artifact_producer_inputs(self):
        """JAR 和扩展的生产者输入必须同时触发并绑定发布运行检查。"""
        runtime = self.by_scope["repository-baseline"]["eng.release.lifecycle-runtime"]
        changed = self.by_scope["change-targeted"]["eng.release.lifecycle-runtime"]
        self.assertEqual(runtime["input_paths"], changed["input_paths"])
        producers = ("eng.backend.delivery", "eng.extension.quality")
        required = {"tests/verification"}
        for producer in producers:
            required.update(self.by_id[producer]["input_paths"])
        runtime_closure = input_closure(ROOT, runtime)
        for name in sorted(required):
            with self.subTest(path=name):
                self.assertIn(name, runtime_closure)
                for check in (runtime, changed):
                    self.assertTrue(trigger_covers(self.by_id[check["check_id"]], name))
                # Git reports files, not directory roots; exercise a real member path.
                path = (
                    f"{name}/synthetic-source.txt" if (ROOT / name).is_dir() else name
                )
                self.assertIn(changed["check_id"], self.selected(path))

    def test_runtime_artifact_bytes_and_directory_membership_change_snapshot(self):
        """合成源文件变化及目录成员增删均改变 runtime 指纹，不执行发行命令。"""
        check = self.by_id["eng.release.lifecycle-runtime"]
        producers = ("eng.backend.delivery", "eng.extension.quality")
        required = sorted(
            {
                name
                for producer in producers
                for name in self.by_id[producer]["input_paths"]
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            samples = {}
            for name in check["input_paths"]:
                target = root / name
                if (ROOT / name).is_dir():
                    target = target / "nested/synthetic-source.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic artifact input\n")
                samples[name] = target
            previous = snapshot_check_inputs(root, check)
            self.assertEqual([], previous["missing"])
            for name in required:
                with self.subTest(path=name):
                    self.assertIn(name, samples)
                    target = samples[name]
                    target.write_bytes(target.read_bytes() + b"changed\n")
                    current = snapshot_check_inputs(root, check)
                    self.assertNotEqual(previous["fingerprint"], current["fingerprint"])
                    previous = current
                    if (ROOT / name).is_dir():
                        added = root / name / "nested/added-source.txt"
                        added.write_bytes(b"new synthetic source\n")
                        expanded = snapshot_check_inputs(root, check)
                        self.assertNotEqual(
                            current["fingerprint"], expanded["fingerprint"]
                        )
                        self.assertIn(
                            "eng.release.lifecycle-runtime",
                            self.selected(added.relative_to(root).as_posix()),
                        )
                        added.unlink()
                        self.assertEqual(
                            current["fingerprint"],
                            snapshot_check_inputs(root, check)["fingerprint"],
                        )

    def test_required_canonical_check_runs_once_and_incomplete_results_fail(self):
        """执行真实结果合同：零测试、跳过或失败均不能包装成 PASS。"""
        good = dict(
            status="PASS", checks_run=3, failures=0, errors=0, skipped=0, reason=""
        )
        cases = [
            (good, "PASS"),
            ({**good, "checks_run": 0}, "FAIL"),
            ({**good, "skipped": 1}, "FAIL"),
            ({**good, "failures": 1}, "FAIL"),
            ({"status": "PASS"}, "FAIL"),
        ]
        for payload, expected in cases:
            calls = []

            def runner(argv, *_):
                calls.append(argv)
                return dict(
                    exit_code=0,
                    exit_reason="exited",
                    timed_out=False,
                    stdout=json.dumps(payload),
                    stderr="",
                    executed_argv=argv,
                )

            with (
                self.subTest(payload=payload),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.fixture(root)
                with patch(
                    "scripts.environment.configuration.verification_environment._detect_tool",
                    return_value={"available": True},
                ):
                    report = verify_repository(
                        root, required_check_ids=(BASE,), runner=runner
                    )
                self.assertEqual(expected, report["result"], report)
                self.assertEqual(1, len(calls), report)

    def test_missing_node_or_git_blocks_without_running(self):
        """缺少声明工具时阻断，不偷偷跳过测试。"""
        for missing in ("node", "git"):
            with (
                self.subTest(missing=missing),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.fixture(root)
                with patch(
                    "scripts.environment.configuration.verification_environment._detect_tool",
                    side_effect=lambda name, _source: {"available": name != missing},
                ):
                    report = verify_repository(
                        root, runner=lambda *_: self.fail("missing resource ran check")
                    )
                self.assertEqual("BLOCKED", report["result"], report)
                self.assertEqual("missing-environment", report["checks"][0]["reason"])

    def test_fixed_release_suites_have_one_entrypoint_and_original_budgets(self):
        """真实声明保留独立范围及外层预算，统一入口仍进入冻结闭包。"""
        for suffix, suite, budget in (
            ("build", "build", 180),
            ("manifest", "manifest", 90),
            ("runtime-entry", "runtime-entry", 90),
            ("lifecycle", "lifecycle", 180),
            ("lifecycle-install", "lifecycle-install", 180),
        ):
            with self.subTest(suite=suite):
                base = self.by_scope["repository-baseline"][f"eng.release.{suffix}"]
                changed = self.by_scope["change-targeted"][f"eng.release.{suffix}"]
                self.assertEqual(
                    [
                        "node",
                        "ops/release/test_tools/run-release-tool-tests.mjs",
                        "--suite",
                        suite,
                    ],
                    base["command"],
                )
                self.assertEqual(base["command"], changed["command"])
                self.assertEqual(budget, base["timeout_seconds"])
                base_closure = input_closure(ROOT, base)
                changed_closure = input_closure(ROOT, changed)
                self.assertIn(
                    "ops/release/test_tools/run-release-tool-tests.mjs", base_closure
                )
                if suite == "runtime-entry":
                    for name in (
                        "runtime/release-installation-state.sh",
                        "runtime/release-installation-docker.sh",
                        "runtime/release-installation-lifecycle.sh",
                    ):
                        source = f"ops/release/{name}"
                        self.assertIn(source, base_closure)
                        self.assertIn(changed["check_id"], self.selected(source))
                for closure in (base_closure, changed_closure):
                    self.assertNotIn(f"ops/release/{suite}-check.mjs", closure)
                    self.assertNotIn("ops/release/lifecycle.mjs", closure)
