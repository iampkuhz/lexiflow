"""Docker 封装合同 Check 的选择、冻结输入和失败关闭合同回归。"""

from __future__ import annotations

import copy
import json
import hashlib
import importlib.util
import io
from contextlib import redirect_stderr
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verification import freeze_inputs
from tests.verification.test_scenarios import verify_repository
from scripts.triggers import run_ci_quick_checks as ci
from tests.verification.declaration_fixtures import input_closure, input_member
from scripts.verification.planning.check_definitions import (
    filter_checks_by_scope,
    load_declarations_snapshot,
)
from scripts.verification.planning.check_scope_selection import (
    select_checks_for_changes,
)
from tests.verification.declaration_fixtures import write_declarations

ROOT = Path(__file__).resolve().parents[2]
BASE = "eng.release.docker-contract"
GROUPS = tuple(
    f"{BASE.rsplit('-', 1)[0]}-{name}-on-change"
    for name in ("surface", "shell", "local", "node")
)


class DockerContractCheckTest(unittest.TestCase):
    """证明真实Docker 封装合同代码及测试进入标准 Verify，而非仅执行文档检查。"""

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
        checks = [copy.deepcopy(self.by_id[k]) for k in (BASE, *GROUPS)]
        (root / "harness").mkdir()
        for name in checks[0]["input_paths"]:
            path = root / name
            if name.endswith("/") or (ROOT / name).is_dir():
                path.mkdir(parents=True, exist_ok=True)
                (path / "synthetic.txt").write_text("synthetic input\n")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic input\n")
        write_declarations(root / "config/engineering/module-checks.yaml", checks)

    def test_scopes_and_required_inputs_match(self):
        """两种范围共享命令、输入和结果合同，封装测试不能替代运行验收。"""
        base = self.by_id[BASE]
        self.assertEqual(base["timeout_seconds"], 600)
        self.assertEqual(
            base["command"],
            ["python3", "scripts/checks/deployment/run_deployment_contract_tests.py"],
        )
        self.assertEqual(
            ["python3", "scripts/checks/deployment/run_deployment_contract_tests.py"],
            base["command"],
        )
        self.assertEqual(
            [
                "python3",
                "node",
                "git",
                "sh",
                "/bin/sh",
                "python-package-yaml",
                "posix-lock-tool",
                "sha256-tool",
                "mktemp",
                "mkfifo",
            ],
            base["required_environment"],
        )
        self.assertEqual([], base["module_dependencies"])
        targeted = [self.by_scope["change-targeted"][item] for item in GROUPS]
        self.assertEqual({"change-targeted"}, {item["scope"] for item in targeted})
        self.assertEqual(
            {
                "eng.release.docker-surface-on-change",
                "eng.release.docker-shell-on-change",
                "eng.release.docker-local-on-change",
                "eng.release.docker-node-on-change",
            },
            {item["check_id"] for item in targeted},
        )
        self.assertTrue(all(item["module_dependencies"] == [] for item in targeted))
        base_closure = input_closure(ROOT, base)
        self.assertIn("infra/local/compose.yaml", base_closure)
        for item in targeted:
            self.assertEqual(600, item["timeout_seconds"])
            self.assertEqual(base["result_contract"], item["result_contract"])
            self.assertEqual("--group", item["command"][2])
            self.assertIn(item["command"][3], ("surface", "shell", "local", "node"))
            self.assertIn("ops/docker", item["input_paths"])
        for name in (
            "ops/docker",
            "ops/release/build/release-candidate-assembler.mjs",
            "ops/release/integrity/release-candidate-verifier.mjs",
            "ops/release/integrity/release-artifact-manifest.mjs",
            "ops/release/build/package-release-artifacts.mjs",
            "ops/release/integrity/extension-archive-integrity.mjs",
            "ops/release/integrity/artifact-build-identity.mjs",
            "ops/release/build/release-runtime-entry-generator.mjs",
            "ops/release/runtime/release-artifact-verification.sh",
            "ops/release/tests/zip-fixture.mjs",
            "ops/release/runtime/release-installation-docker.sh",
            "ops/release/runtime/release-installation-state.sh",
            "ops/release/runtime/release-installation-lifecycle.sh",
            "backend/product/api/src/main/java/io/lexiflow/api/runtime/ContainerHealthController.java",
            "backend/product/api/src/test/java/io/lexiflow/api/runtime/ContainerHealthControllerTest.java",
            "requirements-dev.txt",
        ):
            self.assertIn(name, base_closure)
        for name in base["input_paths"]:
            selected = select_checks_for_changes(
                self.checks,
                [
                    input_member(
                        ROOT,
                        name,
                        check={
                            "triggers": [
                                trigger
                                for group in GROUPS
                                for trigger in self.by_id[group]["triggers"]
                            ]
                        },
                    )
                ],
            )
            self.assertTrue({c["check_id"] for c in selected} & set(GROUPS), name)

    def test_each_input_is_hash_bound(self):
        """每项制品工具输入变更都改变冻结哈希，包括 shell 入口和健康探针。"""
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

    def test_missing_tool_blocks_execution(self):
        """Python 缺失时不能跳过，也不能运行未满足环境的测试。"""
        for missing in ("python3",):
            with (
                self.subTest(missing=missing),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                self.fixture(root)
                with patch(
                    "scripts.environment.configuration.verification_environment._detect_python",
                    return_value={"available": False},
                ):
                    result = verify_repository(
                        root, runner=lambda *_: self.fail("unexpected execution")
                    )
                self.assertEqual("BLOCKED", result["result"])
                self.assertIn(missing, result["checks"][0]["environment"]["missing"])

    def test_ci_quick_uses_complete_aggregate_not_daily_groups(self):
        """CI quick 使用完整 Docker 合同，不拿日常分组替代。"""
        with (
            patch.object(ci, "_base", return_value="a" * 40),
            patch.object(
                ci, "changed_paths", return_value=["ops/docker/tests/test_contract.py"]
            ),
        ):
            plan = ci.make_plan(ROOT)
        selected = set(plan["selected_check_ids"])
        self.assertIn(BASE, selected)
        self.assertFalse(selected.intersection(GROUPS))

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
            probes = []

            def runner(argv, *_, calls=calls, payload=payload):
                if argv == [sys.executable, "-c", "import yaml; print(yaml.__file__)"]:
                    probes.append(argv)
                    return {
                        "status": "PASS",
                        "exit_code": 0,
                        "exit_reason": "exited",
                        "timed_out": False,
                        "stdout": "synthetic-yaml-origin",
                        "stderr": "",
                        "executed_argv": argv,
                    }
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
                self.assertEqual(1, len(probes))

    def test_safe_child_yaml_probe_failure_blocks_suite_execution(self):
        """母环境可见 yaml 不能覆盖安全子环境 import 失败。"""
        calls = []

        def runner(argv, *_):
            calls.append(argv)
            self.assertEqual(
                [sys.executable, "-c", "import yaml; print(yaml.__file__)"], argv
            )
            return {
                "status": "FAIL",
                "exit_code": 1,
                "exit_reason": "exited",
                "timed_out": False,
                "stdout": "",
                "stderr": "synthetic import failure",
                "executed_argv": argv,
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            with patch(
                "scripts.environment.configuration.verification_environment._detect_tool",
                return_value={"available": True},
            ):
                result = verify_repository(root, runner=runner)
        self.assertEqual("BLOCKED", result["result"], result)
        self.assertEqual(
            "python-package-yaml-unavailable-in-child-env", result["reason"]
        )
        self.assertEqual(1, len(calls))


class DockerAdapterDiagnosticTest(unittest.TestCase):
    """分组运行保持即时 stderr 与最终摘要哈希一致。"""

    def synthetic_groups(self):
        """只给聚合诊断提供合成分组，不替代完整固定库存测试。"""
        return {"surface": [
            "ops/docker/tests/test_synthetic.py",
            "ops/podman/tests/synthetic.test.mjs",
        ]}

    def adapter(self):
        """隔离加载 Docker 适配器，仅供合成执行器测试使用。"""
        spec = importlib.util.spec_from_file_location(
            "synthetic_docker_adapter",
            ROOT / "scripts/checks/deployment/run_deployment_contract_tests.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_live_output_and_final_digest(self):
        """即时输出只出现一次，最终摘要绑定完整输出哈希。"""
        stream = io.StringIO()
        module = self.adapter()
        parent = self

        class SyntheticCase(unittest.TestCase):
            def test_first(self):
                pass

            def test_second(self):
                parent.assertIn("test_first", stream.getvalue())
                parent.assertIn("ok", stream.getvalue())

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticCase)
        node_output = (
            json.dumps(
                {
                    "status": "PASS",
                    "checks_run": 1,
                    "failures": 0,
                    "errors": 0,
                    "skipped": 0,
                }
            )
            + "\n"
        )
        with (
            redirect_stderr(stream),
            patch.object(module, "_preflight"),
            patch.object(module, "_members", return_value=self.synthetic_groups()),
            patch.object(module, "_load_python", return_value=suite),
            patch.object(
                module.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, node_output, ""),
            ),
        ):
            report = module._run("surface")
        self.assertEqual("PASS", report["status"])
        self.assertTrue(report["node_transaction_no_skips"])
        self.assertEqual(3, report["checks_run"])
        self.assertTrue(stream.getvalue().endswith(node_output))
        self.assertEqual(
            1,
            sum(
                line.startswith("test_first (")
                for line in stream.getvalue().splitlines()
            ),
        )
        self.assertEqual(stream.getvalue().count("Ran 2 tests"), 1)
        self.assertEqual(
            report["tool_output_sha256"],
            hashlib.sha256(stream.getvalue().encode()).hexdigest(),
        )

    def test_empty_and_skipped_suites_remain_fail_closed(self):
        """空测试集和跳过测试不能产生通过结果。"""

        class SkippedCase(unittest.TestCase):
            @unittest.skip("synthetic skip")
            def test_skipped(self):
                pass

        for suite in (
            unittest.TestSuite(),
            unittest.defaultTestLoader.loadTestsFromTestCase(SkippedCase),
        ):
            module = self.adapter()
            node_report = {
                "status": "PASS",
                "checks_run": 1,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
            }
            with (
                self.subTest(),
                redirect_stderr(io.StringIO()),
                patch.object(module, "_preflight"),
                patch.object(module, "_members", return_value=self.synthetic_groups()),
                patch.object(module, "_load_python", return_value=suite),
                patch.object(
                    module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [], 0, json.dumps(node_report), ""
                    ),
                ),
            ):
                report = module._run("surface")
            self.assertEqual("FAIL", report["status"])

    def test_node_failure_or_malformed_result_cannot_pass_python_success(self):
        """Node 失败或结果畸形不能被 Python 成功覆盖。"""

        class SyntheticCase(unittest.TestCase):
            def test_one(self):
                pass

        for output, code in (
            (json.dumps({"status": "FAIL", "checks_run": 1}), 1),
            ("not-json", 0),
        ):
            module = self.adapter()
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticCase)
            with (
                self.subTest(output=output),
                redirect_stderr(io.StringIO()),
                patch.object(module, "_preflight"),
                patch.object(module, "_members", return_value=self.synthetic_groups()),
                patch.object(module, "_load_python", return_value=suite),
                patch.object(
                    module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], code, output, ""),
                ),
            ):
                report = module._run("surface")
            self.assertEqual("FAIL", report["status"])
            self.assertFalse(report["node_transaction_no_skips"])
