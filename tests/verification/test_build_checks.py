"""构建输入 Check 的选择、冻结输入和失败关闭合同回归。"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.environment import CheckEnvironment
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
BASE = "eng.release.build"
RUNTIME = "eng.release.lifecycle-runtime"


class BuildCheckTest(unittest.TestCase):
    """证明真实构建输入代码及测试进入标准 Verify，而非仅执行文档检查。"""

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

    @staticmethod
    def yaml_probe_result(argv):
        """结果聚合 fixture 区分前置包探测，不将探测冒充构建 Check。"""
        return {
            "status": "PASS",
            "exit_code": 0,
            "exit_reason": "exited",
            "timed_out": False,
            "stdout": yaml.__file__,
            "stderr": "",
            "executed_argv": argv,
        }

    def fixture(self, root):
        """按真实声明复制最小检查配置和合成输入，不读取发行资料。"""
        checks = [copy.deepcopy(self.by_id[k]) for k in (BASE,)]
        (root / "harness").mkdir()
        for name in checks[0]["input_paths"]:
            path = root / name
            if (ROOT / name).is_dir():
                path = path / "synthetic.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic input\n")
        write_declarations(root / "config/engineering/module-checks.yaml", checks)

    def test_scopes_and_required_inputs_match(self):
        """两种范围共享命令、输入和结果合同，清单测试不能被版本测试替代。"""
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
            [
                "node",
                "ops/release/test_tools/run-release-tool-tests.mjs",
                "--suite",
                "build",
            ],
            base["command"],
        )
        self.assertEqual(
            ["node", "python-package-yaml", "git", "sh", "/bin/sh", "mkfifo"],
            base["required_environment"],
        )
        self.assertEqual([], base["module_dependencies"])
        base_closure = input_closure(ROOT, base)
        for name in (
            "integrity/release-artifact-manifest.mjs",
            "tests/build.test.mjs",
            "build/release-image-build-plan.mjs",
            "build/release-build-process.mjs",
            "build/release-image-builder.mjs",
            "tests/build-execution.test.mjs",
            "build/release-candidate-assembler.mjs",
            "tests/candidate.test.mjs",
            "workflow/run-release-build-pipeline.mjs",
            "tests/pipeline.test.mjs",
            "build/package-release-artifacts.mjs",
            "build/release-runtime-entry-generator.mjs",
            "runtime/release-artifact-verification.sh",
            "runtime/release-installation-state.sh",
            "runtime/release-installation-docker.sh",
            "runtime/release-installation-lifecycle.sh",
            "integrity/source-build-identity.mjs",
            "version.txt",
            "test_tools/run-release-tool-tests.mjs",
        ):
            self.assertIn("ops/release/" + name, base_closure)
        for name in (
            "base-images.json",
            "Dockerfile",
            "Dockerfile.postgres",
            ".dockerignore",
            "run-container-command.sh",
            "initialize-postgres-app-role.sh",
        ):
            self.assertIn("ops/docker/" + name, base_closure)
        self.assertEqual(base["command"][-1], "build")
        for name in base["input_paths"]:
            selected = select_checks_for_changes(
                self.checks, [input_member(ROOT, name, check=self.by_id[BASE])]
            )
            self.assertIn(BASE, {c["check_id"] for c in selected}, name)

    def test_each_input_is_hash_bound(self):
        """每项制品工具输入变更都改变冻结哈希，包括许可说明。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            previous = freeze_inputs(root)
            self.assertEqual("PASS", previous["result"], previous)
            for name in self.by_id[BASE]["input_paths"]:
                path = root / name
                if path.is_dir():
                    path = path / "synthetic.txt"
                path.write_text(path.read_text() + "\n# changed\n")
                current = freeze_inputs(root)
                self.assertEqual("PASS", current["result"], current)
                self.assertNotEqual(
                    previous["input_fingerprint"], current["input_fingerprint"], name
                )
                previous = current

    def test_build_changes_select_real_runtime_and_freeze_same_inputs(self):
        """构建与装配变动也选择真实运行检查，不能仅以合成 Node 测试交付。"""
        runtime = self.by_scope["repository-baseline"][RUNTIME]
        changed = self.by_scope["change-targeted"][RUNTIME]
        for key in (
            "command",
            "input_paths",
            "required_environment",
            "result_contract",
        ):
            self.assertEqual(runtime[key], changed[key], key)
        self.assertEqual(
            [
                "python3",
                "-m",
                "scripts.checks.release.source.check_synthetic_release_lifecycle",
            ],
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
        runtime_closure = input_closure(ROOT, runtime)
        for name in self.by_id[BASE]["input_paths"]:
            with self.subTest(path=name):
                self.assertIn(name, runtime_closure)
                probe = input_member(ROOT, name, check=self.by_id[RUNTIME])
                self.assertTrue(trigger_covers(self.by_id[RUNTIME], probe))
                self.assertTrue(trigger_covers(changed, probe))
                selected = select_checks_for_changes(
                    self.checks, [input_member(ROOT, name, check=self.by_id[BASE])]
                )
                self.assertIn(RUNTIME, {c["check_id"] for c in selected})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in runtime["input_paths"]:
                target = root / name
                if (ROOT / name).is_dir():
                    target = target / "synthetic.txt"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic runtime input\n")
            before = snapshot_check_inputs(root, runtime)
            self.assertEqual([], before["missing"])
            for name in self.by_id[BASE]["input_paths"]:
                target = root / name
                if target.is_dir():
                    target = target / "synthetic.txt"
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
                    if (ROOT / name).is_dir():
                        target = target / "synthetic.txt"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("synthetic input\n")
                write_declarations(
                    root / "config/engineering/module-checks.yaml", checks
                )
                executed = []

                def runner(argv, *_, stdin_payload=None):
                    if argv[1:] == ["-c", "import yaml; print(yaml.__file__)"]:
                        return self.yaml_probe_result(argv)
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
                        self.assertEqual(
                            {"schema_version", "run_id", "check_id", "snapshot"},
                            set(envelope),
                        )
                        self.assertEqual(
                            "lexiflow.verify-child-input.v2", envelope["schema_version"]
                        )
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
                    return CheckEnvironment(
                        {},
                        {
                            "status": "BLOCKED" if missing else "PASS",
                            "missing": ["docker"] if missing else [],
                            "details": {},
                        },
                    )

                with (
                    patch(
                        "scripts.verification.execution.check_plan_runner.resolve_check_environment",
                        side_effect=environment,
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
                    "scripts.environment.configuration.verification_environment._detect_tool",
                    side_effect=lambda name, _source, missing=missing: {
                        "available": name != missing
                    },
                ):
                    result = verify_repository(
                        root, runner=lambda *_: self.fail("unexpected execution")
                    )
                self.assertEqual("BLOCKED", result["result"])
                self.assertIn(missing, result["checks"][0]["environment"]["missing"])

    def test_nonempty_complete_result_required_and_canonical_check_runs_once(self):
        """失败、跳过、零测试和缺字段不能产生 PASS，规范 Check 只执行一次。"""
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
                if argv[1:] == ["-c", "import yaml; print(yaml.__file__)"]:
                    return self.yaml_probe_result(argv)
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
                    "scripts.environment.configuration.verification_environment._detect_tool",
                    return_value={"available": True},
                ):
                    result = verify_repository(
                        root, required_check_ids=(BASE,), runner=runner
                    )
                self.assertEqual(expected, result["result"], result)
                self.assertEqual(1, len(calls))
