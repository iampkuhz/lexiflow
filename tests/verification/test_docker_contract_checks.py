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
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification import freeze_inputs, verify_repository
from scripts.verification import ci
from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.scope import select_checks_for_changes
from tests.verification.declaration_fixtures import write_declarations

ROOT = Path(__file__).resolve().parents[2]
BASE = "eng.release.docker-contract"
GROUPS = tuple(f"{BASE.rsplit('-', 1)[0]}-{name}-on-change" for name in ("surface", "shell", "local", "node"))


class DockerContractCheckTest(unittest.TestCase):
    """证明真实Docker 封装合同代码及测试进入标准 Verify，而非仅执行文档检查。"""

    @classmethod
    def setUpClass(cls):
        cls.checks = load_declarations_snapshot(ROOT)[0]["checks"]
        cls.by_id = {check["check_id"]: check for check in cls.checks}

    def fixture(self, root):
        """按真实声明复制最小检查配置和合成输入，不读取发行资料。"""
        checks = [copy.deepcopy(self.by_id[k]) for k in (BASE, *GROUPS)]
        (root / "harness").mkdir()
        for name in checks[0]["input_paths"]:
            path = root / name
            if name.endswith("/") or (ROOT / name).is_dir():
                path.mkdir(exist_ok=True)
                (path / "synthetic.txt").write_text("synthetic input\n")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic input\n")
        write_declarations(root / "harness/module-checks.yaml", checks)

    def test_scopes_and_required_inputs_match(self):
        """两种范围共享命令、输入和结果合同，封装测试不能替代运行验收。"""
        base = self.by_id[BASE]
        self.assertEqual(base["timeout_seconds"], 600)
        self.assertEqual(base["command"], ["python3", "ops/docker/tests/module_check_adapter.py"])
        self.assertEqual(
            ["python3", "ops/docker/tests/module_check_adapter.py"], base["command"]
        )
        self.assertEqual(["python3", "node", "git", "sh", "/bin/sh", "python-package-yaml", "posix-lock-tool", "sha256-tool", "mktemp", "mkfifo"], base["required_environment"])
        self.assertEqual([], base["module_dependencies"])
        targeted = [self.by_id[item] for item in GROUPS]
        self.assertEqual({"change-targeted"}, {item["scope"] for item in targeted})
        self.assertEqual({"eng.release.docker-surface-on-change", "eng.release.docker-shell-on-change", "eng.release.docker-local-on-change", "eng.release.docker-node-on-change"}, {item["check_id"] for item in targeted})
        self.assertTrue(all(item["module_dependencies"] == [] for item in targeted))
        self.assertIn("infra/local/compose.yaml", base["input_paths"])
        for item in targeted:
            self.assertEqual(600, item["timeout_seconds"])
            self.assertEqual(base["result_contract"], item["result_contract"])
            self.assertEqual("--group", item["command"][2])
            self.assertIn(item["command"][3], ("surface", "shell", "local", "node"))
            self.assertNotIn("ops/docker", item["input_paths"] if item["command"][3] != "surface" else [])
        for name in (
            "ops/docker",
            "ops/release/candidate.mjs",
            "ops/release/verified-candidate.mjs",
            "ops/release/manifest.mjs",
            "ops/release/package.mjs",
            "ops/release/archive-identity.mjs",
            "ops/release/embedded-identity.mjs",
            "ops/release/lifecycle.mjs",
            "ops/release/runtime-entry.mjs",
            "ops/release/runtime-verification.sh",
            "ops/release/tests/zip-fixture.mjs",
            "ops/release/lifecycle-docker.sh",
            "ops/release/lifecycle-state.sh",
            "ops/release/lifecycle.sh",
            "backend/product/api/src/main/java/io/lexiflow/api/release/RuntimeHealthCommand.java",
            "backend/product/api/src/test/java/io/lexiflow/api/release/RuntimeHealthCommandTest.java",
            "requirements-dev.txt",
        ):
            self.assertIn(name, base["input_paths"])
        for name in base["input_paths"]:
            selected = select_checks_for_changes(
                self.checks,
                [name + "/entrypoint.sh" if (ROOT / name).is_dir() else name],
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
                    "scripts.environment.runtime.detect_python",
                    return_value={"available": False},
                ):
                    result = verify_repository(
                        root, runner=lambda *_: self.fail("unexpected execution")
                    )
                self.assertEqual("BLOCKED", result["result"])

    def test_ci_quick_uses_complete_aggregate_not_daily_groups(self):
        with patch.object(ci, "_base", return_value="a" * 40), patch.object(
            ci, "changed_paths", return_value=["ops/docker/tests/test_contract.py"]
        ):
            plan = ci.make_plan(ROOT)
        selected = set(plan["selected_check_ids"])
        self.assertIn(BASE, selected)
        self.assertFalse(selected.intersection(GROUPS))

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
                        root, required_check_ids=(BASE,), runner=runner
                    )
                self.assertEqual(expected, result["result"], result)
                self.assertEqual(1, len(calls))


class DockerAdapterDiagnosticTest(unittest.TestCase):
    """分组运行保持即时 stderr 与最终摘要哈希一致。"""

    def adapter(self):
        spec = importlib.util.spec_from_file_location("synthetic_docker_adapter", ROOT / "ops/docker/tests/module_check_adapter.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_live_output_and_final_digest(self):
        stream = io.StringIO()
        module = self.adapter()
        parent = self
        class SyntheticCase(unittest.TestCase):
            def test_first(self): pass
            def test_second(self):
                parent.assertIn("test_first", stream.getvalue())
                parent.assertIn("ok", stream.getvalue())
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticCase)
        node_output = json.dumps({"status": "PASS", "checks_run": 1, "failures": 0, "errors": 0, "skipped": 0}) + "\n"
        with redirect_stderr(stream), patch.object(module, "_preflight"), patch.object(module, "_load_python", return_value=suite), patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, node_output, "")):
            report = module.run_tests()
        self.assertEqual("PASS", report["status"])
        self.assertTrue(report["node_transaction_no_skips"])
        self.assertEqual(3, report["checks_run"])
        self.assertTrue(stream.getvalue().endswith(node_output))
        self.assertEqual(1, sum(line.startswith("test_first (") for line in stream.getvalue().splitlines()))
        self.assertEqual(stream.getvalue().count("Ran 2 tests"), 1)
        self.assertEqual(report["tool_output_sha256"], hashlib.sha256(stream.getvalue().encode()).hexdigest())

    def test_empty_and_skipped_suites_remain_fail_closed(self):
        class SkippedCase(unittest.TestCase):
            @unittest.skip("synthetic skip")
            def test_skipped(self): pass
        for suite in (unittest.TestSuite(), unittest.defaultTestLoader.loadTestsFromTestCase(SkippedCase)):
            module = self.adapter()
            node_report = {"status": "PASS", "checks_run": 1, "failures": 0, "errors": 0, "skipped": 0}
            with self.subTest(), redirect_stderr(io.StringIO()), patch.object(module, "_preflight"), patch.object(module, "_load_python", return_value=suite), patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(node_report), "")):
                report = module.run_tests()
            self.assertEqual("FAIL", report["status"])

    def test_node_failure_or_malformed_result_cannot_pass_python_success(self):
        class SyntheticCase(unittest.TestCase):
            def test_one(self): pass
        for output, code in ((json.dumps({"status": "FAIL", "checks_run": 1}), 1), ("not-json", 0)):
            module = self.adapter()
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticCase)
            with self.subTest(output=output), redirect_stderr(io.StringIO()), patch.object(module, "_preflight"), patch.object(module, "_load_python", return_value=suite), patch.object(module.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, "")):
                report = module.run_tests()
            self.assertEqual("FAIL", report["status"])
            self.assertFalse(report["node_transaction_no_skips"])
