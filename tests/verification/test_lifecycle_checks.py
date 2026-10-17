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
from scripts.verification.planning.check_scope_selection import (
    select_checks_for_changes,
)
from tests.verification.declaration_fixtures import write_declarations

ROOT = Path(__file__).resolve().parents[2]


class LifecycleCheckTest(unittest.TestCase):
    """证明真实更新恢复代码及测试进入标准 Verify，而非仅执行文档检查。"""

    base = "eng.release.lifecycle"

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

    def fixture(self, root):
        """按真实声明复制最小检查配置和合成输入，不读取发行资料。"""
        checks = [copy.deepcopy(self.by_id[k]) for k in (self.base,)]
        (root / "harness").mkdir()
        for name in checks[0]["input_paths"]:
            path = root / name
            if (ROOT / name).is_dir():
                path = path / "synthetic.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic input\n")
        write_declarations(root / "config/engineering/module-checks.yaml", checks)

    def test_runtime_bootjar_generation_sources_are_declared_without_candidate_fake_build(
        self,
    ):
        """只有 lifecycle 实际执行 bootJar，预建 candidate 不伪扩资料生成读取。"""
        reads = {
            "ops/dataset/dataset-generation-identity.mjs",
            "ops/dataset/generation-inputs.json",
            "ops/dataset/ecdict-source.lock.json",
            "ops/dataset/prepare_ecdict_bundle.py",
            "ops/podman/source-tools.Containerfile",
        }
        from scripts.verification.execution.check_runner import snapshot_check_inputs

        runtime = self.by_id["eng.release.lifecycle-runtime"]
        files = {
            item["locator"] for item in snapshot_check_inputs(ROOT, runtime)["files"]
        }
        self.assertTrue(reads.issubset(files))
        for path in sorted(reads):
            with self.subTest(path=path):
                self.assertIn(
                    "eng.release.lifecycle-runtime",
                    {
                        c["check_id"]
                        for c in select_checks_for_changes(self.checks, [path])
                    },
                )
        candidate = self.by_id["eng.release.candidate-runtime"]
        candidate_files = {
            item["locator"] for item in snapshot_check_inputs(ROOT, candidate)["files"]
        }
        self.assertTrue(
            {p for p in reads if p.startswith("ops/dataset/")}.isdisjoint(
                candidate_files
            )
        )
        self.assertNotIn("ops/dataset", input_closure(ROOT, candidate))

    def test_scopes_and_required_inputs_match(self):
        """两种范围共享命令、输入和结果合同，清单测试不能被版本测试替代。"""
        base, change = (
            self.by_scope[scope][self.base]
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
                "lifecycle",
            ],
            base["command"],
        )
        self.assertEqual(
            ["node", "git", "sh", "mktemp", "sha256-tool"], base["required_environment"]
        )
        self.assertEqual([], base["module_dependencies"])
        base_closure = input_closure(ROOT, base)
        for name in (
            "integrity/release-artifact-manifest.mjs",
            "build/package-release-artifacts.mjs",
            "runtime/release-artifact-verification.sh",
            "tests/lifecycle.test.mjs",
            "build/release-runtime-entry-generator.mjs",
            "runtime/release-installation-lifecycle.sh",
            "runtime/release-installation-state.sh",
            "runtime/release-installation-docker.sh",
            "tests/lifecycle-fixture.mjs",
            "integrity/source-build-identity.mjs",
            "version.txt",
            "test_tools/run-release-tool-tests.mjs",
        ):
            self.assertIn("ops/release/" + name, base_closure)
        for name in base["input_paths"]:
            selected = select_checks_for_changes(
                self.checks, [input_member(ROOT, name, check=self.by_id[self.base])]
            )
            self.assertIn(self.base, {c["check_id"] for c in selected}, name)

    def test_daemon_fixture_is_selected_and_frozen(self):
        """独立 daemon 脚本是实际 fixture 读取输入，两个 scope 均须绑定。"""
        name = "ops/release/tests/fake-docker-daemon.mjs"
        for scope in ("repository-baseline", "change-targeted"):
            check = self.by_scope[scope][self.base]
            self.assertIn(name, input_closure(ROOT, check))
            self.assertTrue(trigger_covers(self.by_id[check["check_id"]], name))
        selected = select_checks_for_changes(
            self.checks, [input_member(ROOT, name, check=self.by_id[self.base])]
        )
        self.assertIn(self.base, {c["check_id"] for c in selected})

    def test_runtime_consumer_support_is_frozen_for_both_scopes(self):
        """真实消费者及来源支持的修改须进入两种运行检查快照。"""
        paths = (
            "scripts/checks/release/source/frozen_source_fixture.py",
            "tests/verification/test_release_source_bridge.py",
            "tests/verification/test_release_runtime_check.py",
            "tests/verification/test_release_runtime_artifacts.py",
            "tests/verification/test_release_runtime_lifecycle.py",
            "tests/verification/test_environment.py",
        )
        for scope in ("repository-baseline", "change-targeted"):
            check = self.by_scope[scope]["eng.release.lifecycle-runtime"]
            self.assertIn("scripts/environment", input_closure(ROOT, check))
            for path in paths:
                with self.subTest(scope=scope, path=path):
                    self.assertTrue(
                        any(
                            path == item or path.startswith(item.rstrip("/") + "/")
                            for item in check["input_paths"]
                        ),
                        path,
                    )
                    self.assertTrue(
                        any(
                            path == item["path"]
                            or path.startswith(item["path"].rstrip("/") + "/")
                            for item in self.by_id[check["check_id"]]["triggers"]
                        ),
                        path,
                    )

    def test_each_input_is_hash_bound(self):
        """每项制品工具输入变更都改变冻结哈希，包括许可说明。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            previous = freeze_inputs(root)
            self.assertEqual("PASS", previous["result"], previous)
            for name in self.by_id[self.base]["input_paths"]:
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
        """结果须完整；同一规范 Check 的多场景资格只执行一次。"""
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
                    self.assertEqual(
                        {"schema_version", "run_id", "check_id", "snapshot"},
                        set(envelope),
                    )
                    self.assertEqual(
                        "lexiflow.verify-child-input.v2", envelope["schema_version"]
                    )
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
                        "scripts.verification.execution.check_plan_runner.resolve_check_environment",
                        return_value=CheckEnvironment(
                            {}, {"status": "PASS", "missing": [], "details": {}}
                        ),
                    ),
                ):
                    result = verify_repository(
                        root,
                        required_check_ids=(self.base,),
                        runner=runner,
                    )
                self.assertEqual(expected, result["result"], result)
                self.assertEqual(1, len(calls))


class LifecycleInstallCheckTest(LifecycleCheckTest):
    """安装适配与更新流程保持独立 Check，不以一项结果替代另一项。"""

    base = "eng.release.lifecycle-install"

    def test_scopes_and_required_inputs_match(self):
        """安装测试命令与冻结的实际适配文件被独立选择。"""
        first = self.by_scope["repository-baseline"][self.base]
        second = self.by_scope["change-targeted"][self.base]
        self.assertEqual(
            first["command"],
            [
                "node",
                "ops/release/test_tools/run-release-tool-tests.mjs",
                "--suite",
                "lifecycle-install",
            ],
        )
        self.assertEqual(first["input_paths"], second["input_paths"])
        self.assertEqual(
            ["node", "git", "sh", "bash", "mktemp", "sha256-tool"],
            first["required_environment"],
        )
        self.assertEqual(first["required_environment"], second["required_environment"])
        first_closure = input_closure(ROOT, first)
        self.assertIn(
            "ops/release/build/release-runtime-entry-generator.mjs", first_closure
        )
        self.assertIn(
            "ops/release/runtime/release-installation-lifecycle.sh", first_closure
        )
        for name in (
            "ops/release/runtime/release-installation-state.sh",
            "ops/release/runtime/release-installation-docker.sh",
            "ops/release/tests/lifecycle-install.test.mjs",
            "ops/docker/tests/update-recovery.sh",
        ):
            self.assertIn(name, first_closure)
            selected = select_checks_for_changes(
                self.checks, [input_member(ROOT, name, check=self.by_id[self.base])]
            )
            self.assertIn(self.base, {item["check_id"] for item in selected})


class LifecycleRuntimeCheckTest(LifecycleCheckTest):
    """真实容器检查不能替换成 Node stub，也不能把环境缺失视为通过。"""

    def test_build_support_closure_is_selected_and_preserved_by_source_bridge(self):
        """生产者支持文件进入场景冻结，桥接保留完整来源闭包。"""
        from scripts.checks.release.source import frozen_source_fixture as bridge

        support = (
            "ops/release/integrity/candidate-source-proof.mjs",
            "ops/release/tests/candidate-proof.test.mjs",
            "ops/release/workflow/run-release-workflow.mjs",
            "ops/release/tests/workflow.test.mjs",
            "ops/release/tests/verified-candidate-fixture.mjs",
        )
        canonical = self.by_id[bridge.CHECK_ID]
        synthetic_files = {path for path in support} | set(bridge.FIXED_FILES)
        synthetic_files.update(
            folder + "/src/synthetic.txt" for folder in bridge.FIXED_DIRS
        )
        closure = bridge._closure(
            {"files": [{"locator": name} for name in sorted(synthetic_files)]}
        )
        self.assertEqual(sorted(synthetic_files), [item["locator"] for item in closure])
        self.assertEqual(bridge.CHECK_ID, canonical["check_id"])
        for path in support:
            with self.subTest(path=path):
                for scope in ("repository-baseline", "change-targeted"):
                    check = self.by_scope[scope][bridge.CHECK_ID]
                    self.assertTrue(
                        any(
                            path == item or path.startswith(item.rstrip("/") + "/")
                            for item in check["input_paths"]
                        ),
                        path,
                    )
                    self.assertTrue(
                        any(
                            path == item["path"]
                            or path.startswith(item["path"].rstrip("/") + "/")
                            for item in self.by_id[check["check_id"]]["triggers"]
                        ),
                        path,
                    )

    def test_delivery_stops_before_next_check_after_runtime_failure(self):
        """运行检查失败后不启动后续检查，未执行结果明确阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            runtime = copy.deepcopy(self.by_id[self.base])
            following = copy.deepcopy(self.by_id["eng.release.version"])
            following["check_id"] = "fixture.after-runtime"
            following["input_paths"] = []
            following["module_dependencies"] = []
            following["required_environment"] = []
            following["transaction_reuse"] = False
            write_declarations(
                root / "config/engineering/module-checks.yaml", [runtime, following]
            )
            calls = []

            def runner(argv, *_args, **_kwargs):
                calls.append(argv)
                return {
                    "exit_code": 1,
                    "exit_reason": "exited",
                    "timed_out": False,
                    "stdout": "",
                    "stderr": "fixture failure",
                    "executed_argv": argv,
                }

            with (
                patch(
                    "scripts.verification.execution.check_plan_runner.resolve_check_environment",
                    return_value=CheckEnvironment(
                        {}, {"status": "PASS", "missing": [], "details": {}}
                    ),
                ),
            ):
                report = verify_repository(
                    root,
                    required_check_ids=(self.base,),
                    runner=runner,
                )
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            [item["status"] for item in report["checks"]], ["FAIL", "BLOCKED"]
        )
        self.assertEqual(report["checks"][1]["process"]["exit_reason"], "not-run")

    base = "eng.release.lifecycle-runtime"

    def test_release_source_policy_files_are_selected_and_frozen(self):
        """忽略规则与许可材料始终冻结，但各自只选择真实语义消费者。"""
        closures = {
            scope: input_closure(ROOT, self.by_scope[scope][self.base])
            for scope in ("repository-baseline", "change-targeted")
        }
        for name in (".gitignore", "ops/release/third-party-notices.md"):
            for scope in ("repository-baseline", "change-targeted"):
                self.assertIn(name, closures[scope])
            consumer = self.base if name == ".gitignore" else "eng.release.manifest"
            self.assertTrue(trigger_covers(self.by_id[consumer], name))
            selected = select_checks_for_changes(
                self.checks, [input_member(ROOT, name, check=self.by_id[consumer])]
            )
            self.assertIn(consumer, {item["check_id"] for item in selected})
            if name != ".gitignore":
                self.assertNotIn(self.base, {item["check_id"] for item in selected})

    def test_actual_entry_reports_structured_blocked_without_docker(self):
        """实际消费者拒绝缺失 Verify 管道输入，不接触 Docker 或构建。"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.checks.release.source.check_synthetic_release_lifecycle",
                ],
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
        first = self.by_scope["repository-baseline"][self.base]
        second = self.by_scope["change-targeted"][self.base]
        self.assertEqual(
            first["command"],
            [
                "python3",
                "-m",
                "scripts.checks.release.source.check_synthetic_release_lifecycle",
            ],
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
        self.assertIn(
            "scripts/environment/services/isolated_postgres_redis.py",
            input_closure(ROOT, first),
        )
        self.assertIn("ops/docker/tests/update-recovery.sh", input_closure(ROOT, first))

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
