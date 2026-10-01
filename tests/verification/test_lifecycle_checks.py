"""更新恢复 Check 的选择、冻结输入和失败关闭合同回归。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification import freeze_inputs, verify_repository
from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.scope import select_checks_for_changes

ROOT = Path(__file__).resolve().parents[2]


class LifecycleCheckTest(unittest.TestCase):
    """证明真实更新恢复代码及测试进入标准 Verify，而非仅执行文档检查。"""

    base = "eng.release.lifecycle"

    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]
        cls.by_id = {check["check_id"]: check for check in cls.checks}

    def fixture(self, root):
        """按真实声明复制最小检查配置和合成输入，不读取发行资料。"""
        checks = [
            copy.deepcopy(self.by_id[k]) for k in (self.base, self.base + "-on-change")
        ]
        (root / "harness").mkdir()
        for name in checks[0]["input_paths"]:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic input\n")
        (root / "harness/module-checks.yaml").write_text(
            yaml.safe_dump(
                {"schema_version": "lexiflow.module-checks.v1", "checks": checks}
            )
        )

    def test_scopes_and_required_inputs_match(self):
        """两种范围共享命令、输入和结果合同，清单测试不能被版本测试替代。"""
        base, change = self.by_id[self.base], self.by_id[self.base + "-on-change"]
        for key in (
            "command",
            "required_environment",
            "input_paths",
            "result_contract",
        ):
            self.assertEqual(base[key], change[key], key)
        self.assertEqual(["node", "ops/release/lifecycle-check.mjs"], base["command"])
        self.assertEqual(["node", "git", "sh"], base["required_environment"])
        self.assertEqual([], base["module_dependencies"])
        for name in (
            "manifest.mjs",
            "package.mjs",
            "runtime-verification.sh",
            "lifecycle-check.mjs",
            "tests/lifecycle.test.mjs",
            "runtime-entry.mjs",
            "lifecycle.mjs",
            "lifecycle.sh",
            "lifecycle-state.sh",
            "lifecycle-docker.sh",
            "tests/lifecycle-fixture.mjs",
            "version.mjs",
            "version.txt",
            "check.mjs",
        ):
            self.assertIn("ops/release/" + name, base["input_paths"])
        for name in base["input_paths"]:
            selected = select_checks_for_changes(self.checks, [name])
            self.assertIn(
                self.base + "-on-change", {c["check_id"] for c in selected}, name
            )

    def test_daemon_fixture_is_selected_and_frozen(self):
        """独立 daemon 脚本是实际 fixture 读取输入，两个 scope 均须绑定。"""
        name = "ops/release/tests/fake-docker-daemon.mjs"
        for check_id in (self.base, self.base + "-on-change"):
            check = self.by_id[check_id]
            self.assertIn(name, check["input_paths"])
            self.assertIn({"path": name}, check["triggers"])
        selected = select_checks_for_changes(self.checks, [name])
        self.assertIn(self.base + "-on-change", {c["check_id"] for c in selected})

    def test_runtime_consumer_support_is_frozen_for_both_scopes(self):
        """真实消费者及来源支持的修改须进入两种运行检查快照。"""
        paths = (
            "scripts/verification/release_source_bridge.py",
            "tests/verification/test_release_source_bridge.py",
            "tests/verification/test_release_runtime_check.py",
            "tests/verification/test_release_runtime_artifacts.py",
            "tests/verification/test_release_runtime_lifecycle.py",
            "tests/verification/test_environment.py",
        )
        for check_id in (
            "eng.release.lifecycle-runtime",
            "eng.release.lifecycle-runtime-on-change",
        ):
            check = self.by_id[check_id]
            self.assertIn("scripts/environment", check["input_paths"])
            for path in paths:
                with self.subTest(check=check_id, path=path):
                    self.assertIn(path, check["input_paths"])
                    self.assertIn({"path": path}, check["triggers"])

    def test_each_input_is_hash_bound(self):
        """每项制品工具输入变更都改变冻结哈希，包括许可说明。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            previous = freeze_inputs(root)
            self.assertEqual("PASS", previous["result"], previous)
            for name in self.by_id[self.base]["input_paths"]:
                path = root / name
                path.write_text(path.read_text() + "\n# changed\n")
                current = freeze_inputs(root)
                self.assertEqual("PASS", current["result"], current)
                self.assertNotEqual(
                    previous["input_fingerprint"], current["input_fingerprint"], name
                )
                previous = current

    def test_missing_tool_blocks_execution(self):
        """Node、Git 或 sh 缺失时不能跳过，也不能运行未满足环境的测试。"""
        for missing in ("node", "git", "sh"):
            with (
                self.subTest(missing=missing),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.fixture(root)
                with patch(
                    "scripts.environment.runtime.detect_tool",
                    side_effect=lambda name, missing=missing: {
                        "available": name != missing
                    },
                ):
                    result = verify_repository(
                        root, runner=lambda *_: self.fail("unexpected execution")
                    )
                self.assertEqual("BLOCKED", result["result"])

    def test_nonempty_complete_result_required_and_deduplicated(self):
        """结果须完整；普通入口去重，而 runtime 两种上下文各自执行。"""
        good = {
            "status": "PASS",
            "checks_run": 4,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "reason": "",
        }
        cases = [
            (good, "PASS"),
            (
                {
                    **good,
                    "status": "BLOCKED",
                    "checks_run": 0,
                    "reason": "LIFECYCLE_LEASE_UNAVAILABLE",
                },
                "BLOCKED",
            ),
            ({**good, "checks_run": 0}, "FAIL"),
            ({**good, "skipped": 1}, "FAIL"),
            ({**good, "failures": 1}, "FAIL"),
            ({"status": "PASS"}, "FAIL"),
        ]
        for payload, expected in cases:
            calls = []

            def runner(argv, *_, calls=calls, payload=payload, stdin_payload=None):
                calls.append(argv)
                report = dict(payload)
                if stdin_payload is not None:
                    envelope = json.loads(stdin_payload)
                    report.update(
                        {
                            "run_id": envelope["run_id"],
                            "check_id": envelope["check_id"],
                            "verify_input_fingerprint": envelope["snapshot"][
                                "fingerprint"
                            ],
                        }
                    )
                return {
                    "exit_code": 0,
                    "exit_reason": "exited",
                    "timed_out": False,
                    "stdout": json.dumps(report),
                    "stderr": "",
                    "executed_argv": argv,
                }

            with (
                self.subTest(payload=payload),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.fixture(root)
                # 此用例隔离结果合同；环境缺失由独立负例覆盖。
                with (
                    patch(
                        "scripts.verification.scenarios.check_for",
                        return_value={"status": "PASS", "missing": [], "details": {}},
                    ),
                    patch(
                        "scripts.verification.scenarios.execution_environment",
                        return_value={},
                    ),
                ):
                    result = verify_repository(
                        root,
                        required_check_ids=(self.base + "-on-change",),
                        runner=runner,
                    )
                self.assertEqual(expected, result["result"], result)
                self.assertEqual(
                    2 if self.base == "eng.release.lifecycle-runtime" else 1, len(calls)
                )


class LifecycleInstallCheckTest(LifecycleCheckTest):
    """安装适配与更新流程保持独立 Check，不以一项结果替代另一项。"""

    base = "eng.release.lifecycle-install"

    def test_scopes_and_required_inputs_match(self):
        """安装测试命令与冻结的实际适配文件被独立选择。"""
        first = self.by_id[self.base]
        second = self.by_id[self.base + "-on-change"]
        self.assertEqual(
            first["command"], ["node", "ops/release/lifecycle-install-check.mjs"]
        )
        self.assertEqual(first["input_paths"], second["input_paths"])
        self.assertNotIn("ops/release/lifecycle.mjs", first["input_paths"])
        self.assertNotIn("ops/release/lifecycle.sh", first["input_paths"])
        for name in (
            "ops/release/lifecycle-state.sh",
            "ops/release/lifecycle-docker.sh",
            "ops/release/tests/lifecycle-install.test.mjs",
        ):
            self.assertIn(name, first["input_paths"])
            selected = select_checks_for_changes(self.checks, [name])
            self.assertIn(
                self.base + "-on-change", {item["check_id"] for item in selected}
            )


class LifecycleRuntimeCheckTest(LifecycleCheckTest):
    """真实容器检查不能替换成 Node stub，也不能把环境缺失视为通过。"""

    base = "eng.release.lifecycle-runtime"

    def test_release_source_policy_files_are_selected_and_frozen(self):
        """真实构建的忽略规则与许可模板必须由两种范围选择并冻结。"""
        for name in (".gitignore", "ops/release/third-party-notices.md"):
            for check_id in (self.base, self.base + "-on-change"):
                check = self.by_id[check_id]
                self.assertIn(name, check["input_paths"])
                self.assertIn({"path": name}, check["triggers"])
            selected = select_checks_for_changes(self.checks, [name])
            self.assertIn(
                self.base + "-on-change", {item["check_id"] for item in selected}
            )

    def test_actual_entry_reports_structured_blocked_without_docker(self):
        """实际消费者拒绝缺失 Verify 管道输入，不接触 Docker 或构建。"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-m", "scripts.environment.release_runtime_check"],
                cwd=ROOT,
                env={"PATH": directory},
                input="",
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, "")
            self.assertNotIn(directory, result.stdout)
            self.assertEqual(
                json.loads(result.stdout),
                {
                    "status": "BLOCKED",
                    "checks_run": 0,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                    "reason": "verify-context-unavailable",
                },
            )

    def test_scopes_and_required_inputs_match(self):
        """真实入口绑定冻结消费者及其显式工具、双平台端点和缓存。"""
        first = self.by_id[self.base]
        second = self.by_id[self.base + "-on-change"]
        self.assertEqual(
            first["command"],
            ["python3", "-m", "scripts.environment.release_runtime_check"],
        )
        self.assertEqual(
            first["required_environment"],
            [
                "python3",
                "bash",
                "sh",
                "git",
                "node",
                "npm",
                "docker",
                "java-25-temurin",
                "postgres-test-jdbc-url",
                "release-arm64-docker-host",
                "release-gradle-cache",
                "release-npm-cache",
            ],
        )
        self.assertEqual(first["command"], second["command"])
        self.assertEqual(first["required_environment"], second["required_environment"])
        self.assertEqual(first["input_paths"], second["input_paths"])
        self.assertIn("harness/test-services.json", first["input_paths"])
        self.assertIn("ops/docker/tests/update-recovery.sh", first["input_paths"])

    def test_missing_tool_blocks_execution(self):
        """真实 Docker 缺失时，执行器不能转去调用合成 fixture。"""
        for missing in ("bash", "docker"):
            with (
                self.subTest(missing=missing),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.fixture(root)
                with patch(
                    "scripts.environment.runtime.detect_tool",
                    side_effect=lambda name, missing=missing: {
                        "available": name != missing
                    },
                ):
                    result = verify_repository(
                        root, runner=lambda *_: self.fail("unexpected execution")
                    )
                self.assertEqual("BLOCKED", result["result"])
