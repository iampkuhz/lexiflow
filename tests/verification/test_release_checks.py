"""版本发布 Check 的实际声明、冻结闭包和完整性结果合同回归。"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification import freeze_inputs, verify_repository
from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.kernel import snapshot_check_inputs
from scripts.verification.scope import select_checks_for_changes

ROOT = Path(__file__).resolve().parents[2]
BASE = "eng.release.version"


class ReleaseCheckDeclarationTest(unittest.TestCase):
    """使用真实声明验证版本源码不能脱离标准 Verify。"""

    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]
        cls.by_id = {check["check_id"]: check for check in cls.checks}

    def selected(self, path):
        """返回单一变动路径触发的实际检查。"""
        return {
            check["check_id"]
            for check in select_checks_for_changes(self.checks, [path])
        }

    def test_version_sources_select_release_and_backend_consumers(self):
        """解析器、单测、入口都触发检查，版本文件同时触发后端。"""
        for path in self.by_id[BASE]["input_paths"]:
            self.assertIn(BASE + "-on-change", self.selected(path), path)
        self.assertIn(
            "eng.backend.delivery-on-change", self.selected("ops/release/version.txt")
        )
        self.assertNotIn(BASE + "-on-change", self.selected("docs/README.md"))
        for check_id in ("eng.backend.delivery", "eng.backend.delivery-on-change"):
            self.assertIn(
                "ops/release/version.txt", self.by_id[check_id]["input_paths"]
            )

    def test_scopes_share_command_environment_and_result_contract(self):
        """两种 scope 共享输入和失败关闭结果，不借用 Java 模块运行 Node。"""
        base, change = self.by_id[BASE], self.by_id[BASE + "-on-change"]
        for key in (
            "command",
            "required_environment",
            "input_paths",
            "result_contract",
        ):
            self.assertEqual(base[key], change[key], key)
        self.assertEqual(["node", "ops/release/check.mjs"], base["command"])
        self.assertEqual(["node", "git"], base["required_environment"])
        self.assertEqual([], base["module_dependencies"])
        self.assertEqual("json-stdout", base["result_contract"]["type"])

    def test_extension_consumers_select_and_freeze_shared_version_sources(self):
        """统一版本及读取器变动必须触发扩展，并进入两种检查的冻结闭包。"""
        base = self.by_id["eng.extension.quality"]
        change = self.by_id["eng.extension.quality-on-change"]
        self.assertEqual(base["input_paths"], change["input_paths"])
        for path in ("ops/release/version.txt", "ops/release/version.mjs"):
            self.assertIn("eng.extension.quality-on-change", self.selected(path))
            for check in (base, change):
                self.assertIn(path, check["input_paths"])
                self.assertIn({"path": path}, check["triggers"])

    def fixture(self, root):
        """创建与实际声明路径相同的合成冻结输入。"""
        checks = [copy.deepcopy(self.by_id[key]) for key in (BASE, BASE + "-on-change")]
        (root / "harness").mkdir()
        (root / "harness/module-checks.yaml").write_text(
            yaml.safe_dump(
                {"schema_version": "lexiflow.module-checks.v1", "checks": checks}
            )
        )
        for name in checks[0]["input_paths"]:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic input\n")

    def test_backend_build_launchers_are_frozen(self):
        """真实构建启动脚本及 Wrapper 字节参与冻结，触发检查不能替代哈希绑定。"""
        files = (
            "backend/gradlew",
            "backend/gradle/wrapper/gradle-wrapper.jar",
            "backend/gradle/wrapper/gradle-wrapper.properties",
        )
        base = self.by_id["eng.backend.delivery"]
        change = self.by_id["eng.backend.delivery-on-change"]
        self.assertEqual(base["input_paths"], change["input_paths"])
        for name in files:
            self.assertIn(name, base["input_paths"])
            self.assertIn("eng.backend.delivery-on-change", self.selected(name))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in base["input_paths"]:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic build input\n")
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
                path.write_text(path.read_text() + "changed\n")
                current = freeze_inputs(root)
                self.assertEqual("PASS", current["result"], current)
                self.assertNotEqual(
                    previous["input_fingerprint"], current["input_fingerprint"], name
                )
                previous = current

    def test_runtime_selects_and_freezes_artifact_producer_inputs(self):
        """JAR 和扩展的生产者输入必须同时触发并绑定发布运行检查。"""
        runtime = self.by_id["eng.release.lifecycle-runtime"]
        changed = self.by_id["eng.release.lifecycle-runtime-on-change"]
        self.assertEqual(runtime["input_paths"], changed["input_paths"])
        producers = ("eng.backend.delivery", "eng.extension.quality")
        required = {"tests/verification/test_release_checks.py"}
        for producer in producers:
            required.update(self.by_id[producer]["input_paths"])
        for name in sorted(required):
            with self.subTest(path=name):
                self.assertIn(name, runtime["input_paths"])
                for check in (runtime, changed):
                    self.assertIn({"path": name}, check["triggers"])
                self.assertIn(changed["check_id"], self.selected(name))

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
                            "eng.release.lifecycle-runtime-on-change",
                            self.selected(added.relative_to(root).as_posix()),
                        )
                        added.unlink()
                        self.assertEqual(
                            current["fingerprint"],
                            snapshot_check_inputs(root, check)["fingerprint"],
                        )

    def test_required_scope_deduplicates_and_incomplete_results_fail(self):
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
                    "scripts.environment.runtime.detect_tool",
                    return_value={"available": True},
                ):
                    report = verify_repository(
                        root, required_check_ids=(BASE + "-on-change",), runner=runner
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
                    "scripts.environment.runtime.detect_tool",
                    side_effect=lambda name: {"available": name != missing},
                ):
                    report = verify_repository(
                        root, runner=lambda *_: self.fail("missing resource ran check")
                    )
                self.assertEqual("BLOCKED", report["result"], report)
                self.assertEqual("missing-environment", report["checks"][0]["reason"])
