"""构建输入 Check 的选择、冻结输入和失败关闭合同回归。"""

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
BASE = "eng.release.build"
RUNTIME = "eng.release.lifecycle-runtime"


class BuildCheckTest(unittest.TestCase):
    """证明真实构建输入代码及测试进入标准 Verify，而非仅执行文档检查。"""

    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]
        cls.by_id = {check["check_id"]: check for check in cls.checks}

    def fixture(self, root):
        """按真实声明复制最小检查配置和合成输入，不读取发行资料。"""
        checks = [copy.deepcopy(self.by_id[k]) for k in (BASE, BASE + "-on-change")]
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
        base, change = self.by_id[BASE], self.by_id[BASE + "-on-change"]
        for key in (
            "command",
            "required_environment",
            "input_paths",
            "result_contract",
        ):
            self.assertEqual(base[key], change[key], key)
        self.assertEqual(["node", "ops/release/build-check.mjs"], base["command"])
        self.assertEqual(["node", "git", "sh", "/bin/sh", "mkfifo"], base["required_environment"])
        self.assertEqual([], base["module_dependencies"])
        for name in (
            "manifest.mjs",
            "build-check.mjs",
            "tests/build.test.mjs",
            "build.mjs",
            "build-command.mjs",
            "build-execution.mjs",
            "tests/build-execution.test.mjs",
            "candidate.mjs",
            "tests/candidate.test.mjs",
            "pipeline.mjs",
            "tests/pipeline.test.mjs",
            "package.mjs",
            "runtime-entry.mjs",
            "runtime-verification.sh",
            "lifecycle.mjs",
            "lifecycle-state.sh",
            "lifecycle-docker.sh",
            "lifecycle.sh",
            "version.mjs",
            "version.txt",
            "check.mjs",
        ):
            self.assertIn("ops/release/" + name, base["input_paths"])
        for name in (
            "base-images.json",
            "Dockerfile",
            "Dockerfile.postgres",
            ".dockerignore",
            "entrypoint.sh",
            "bootstrap.sh",
        ):
            self.assertIn("ops/docker/" + name, base["input_paths"])
        self.assertNotIn("ops/release/tests/lifecycle.test.mjs", base["input_paths"])
        for name in base["input_paths"]:
            selected = select_checks_for_changes(self.checks, [name])
            self.assertIn(BASE + "-on-change", {c["check_id"] for c in selected}, name)

    def test_each_input_is_hash_bound(self):
        """每项制品工具输入变更都改变冻结哈希，包括许可说明。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            previous = freeze_inputs(root)
            self.assertEqual("PASS", previous["result"], previous)
            for name in self.by_id[BASE]["input_paths"]:
                path = root / name
                path.write_text(path.read_text() + "\n# changed\n")
                current = freeze_inputs(root)
                self.assertEqual("PASS", current["result"], current)
                self.assertNotEqual(
                    previous["input_fingerprint"], current["input_fingerprint"], name
                )
                previous = current

    def test_build_changes_select_real_runtime_and_freeze_same_inputs(self):
        """构建与装配变动也选择真实运行检查，不能仅以合成 Node 测试交付。"""
        runtime = self.by_id[RUNTIME]
        changed = self.by_id[RUNTIME + "-on-change"]
        for key in (
            "command",
            "input_paths",
            "required_environment",
            "result_contract",
        ):
            self.assertEqual(runtime[key], changed[key], key)
        self.assertEqual(
            ["python3", "-m", "scripts.environment.release_runtime_check"],
            runtime["command"],
        )
        self.assertEqual(
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
            runtime["required_environment"],
        )
        self.assertEqual([], runtime["module_dependencies"])
        for name in self.by_id[BASE]["input_paths"]:
            with self.subTest(path=name):
                self.assertIn(name, runtime["input_paths"])
                self.assertIn({"path": name}, runtime["triggers"])
                self.assertIn({"path": name}, changed["triggers"])
                selected = select_checks_for_changes(self.checks, [name])
                self.assertIn(RUNTIME + "-on-change", {c["check_id"] for c in selected})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in runtime["input_paths"]:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic runtime input\n")
            before = snapshot_check_inputs(root, runtime)
            self.assertEqual([], before["missing"])
            for name in self.by_id[BASE]["input_paths"]:
                target = root / name
                target.write_text(target.read_text() + "changed\n")
                after = snapshot_check_inputs(root, runtime)
                self.assertNotEqual(before["fingerprint"], after["fingerprint"], name)
                before = after

    def test_runtime_blocked_is_not_hidden_by_build_pass(self):
        """同次构建检查通过也不能覆盖缺 Docker 或实际运行入口的阻塞。"""
        for docker_available, runtime_exit, expected in (
            (False, 0, "BLOCKED"),
            (True, 0, "BLOCKED"),
            (True, 3, "FAIL"),
        ):
            with (
                self.subTest(
                    docker_available=docker_available, runtime_exit=runtime_exit
                ),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                checks = [copy.deepcopy(self.by_id[key]) for key in (BASE, RUNTIME)]
                for name in {name for check in checks for name in check["input_paths"]}:
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("synthetic input\n")
                (root / "harness/module-checks.yaml").write_text(
                    yaml.safe_dump(
                        {
                            "schema_version": "lexiflow.module-checks.v1",
                            "checks": checks,
                        }
                    )
                )
                executed = []

                def runner(argv, *_, stdin_payload=None):
                    executed.append(argv)
                    is_build = argv == self.by_id[BASE]["command"]
                    self.assertTrue(is_build or docker_available)
                    payload = {
                        "status": "PASS" if is_build else "BLOCKED",
                        "checks_run": 1 if is_build else 0,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                        "reason": ""
                        if is_build
                        else "LIFECYCLE_LEASE_ADAPTER_UNAVAILABLE",
                    }
                    if stdin_payload is not None:
                        envelope = json.loads(stdin_payload)
                        payload.update(
                            {
                                "run_id": envelope["run_id"],
                                "check_id": envelope["check_id"],
                                "verify_input_fingerprint": envelope["snapshot"][
                                    "fingerprint"
                                ],
                            }
                        )
                    return {
                        "exit_code": 0 if is_build else runtime_exit,
                        "exit_reason": "exited",
                        "timed_out": False,
                        "stdout": json.dumps(payload),
                        "stderr": "",
                        "executed_argv": argv,
                    }

                # 这里只隔离构建与 runtime 的结果聚合，环境能力映射另有直接测试。
                def environment(_root, check):
                    missing = check["check_id"] == RUNTIME and not docker_available
                    return {
                        "status": "BLOCKED" if missing else "PASS",
                        "missing": ["docker"] if missing else [],
                        "details": {},
                    }

                with (
                    patch(
                        "scripts.verification.scenarios.check_for",
                        side_effect=environment,
                    ),
                    patch(
                        "scripts.verification.scenarios.execution_environment",
                        return_value={},
                    ),
                ):
                    result = verify_repository(root, runner=runner)
                # 内核先处理非零退出；退出 3 的结构化 BLOCKED 目前记录为 FAIL。
                # 保留这一真实边界，不为通过本测试更改内核或伪造执行器退出码。
                self.assertEqual(expected, result["result"], result["reason"])
                outcomes = {
                    check["check_id"]: check["status"] for check in result["checks"]
                }
                self.assertEqual({BASE: "PASS", RUNTIME: expected}, outcomes)
                self.assertEqual(2 if docker_available else 1, len(executed))

    def test_missing_tool_blocks_execution(self):
        """Node 或 Git 缺失时不能跳过，也不能运行未满足环境的测试。"""
        for missing in ("node", "git"):
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
        """失败、跳过、零测试和缺字段不能产生 PASS，等价入口只执行一次。"""
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
            ({**good, "checks_run": 0}, "FAIL"),
            ({**good, "skipped": 1}, "FAIL"),
            ({**good, "failures": 1}, "FAIL"),
            ({"status": "PASS"}, "FAIL"),
        ]
        for payload, expected in cases:
            calls = []

            def runner(argv, *_, calls=calls, payload=payload):
                calls.append(argv)
                return {
                    "exit_code": 0,
                    "exit_reason": "exited",
                    "timed_out": False,
                    "stdout": json.dumps(payload),
                    "stderr": "",
                    "executed_argv": argv,
                }

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
                    result = verify_repository(
                        root, required_check_ids=(BASE + "-on-change",), runner=runner
                    )
                self.assertEqual(expected, result["result"], result)
                self.assertEqual(1, len(calls))
