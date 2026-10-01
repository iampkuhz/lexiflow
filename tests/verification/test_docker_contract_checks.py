"""Docker 封装合同 Check 的选择、冻结输入和失败关闭合同回归。"""

from __future__ import annotations

import copy
import json
import hashlib
import importlib.util
import io
from contextlib import redirect_stderr
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification import freeze_inputs, verify_repository
from scripts.verification.declarations import load_declarations_snapshot
from scripts.verification.scope import select_checks_for_changes

ROOT = Path(__file__).resolve().parents[2]
BASE = "eng.release.docker-contract"


class DockerContractCheckTest(unittest.TestCase):
    """证明真实Docker 封装合同代码及测试进入标准 Verify，而非仅执行文档检查。"""

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
            if name == "ops/docker":
                path.mkdir()
                (path / "synthetic.txt").write_text("synthetic input\n")
            else:
                path.write_text("synthetic input\n")
        (root / "harness/module-checks.yaml").write_text(
            yaml.safe_dump(
                {"schema_version": "lexiflow.module-checks.v1", "checks": checks}
            )
        )

    def test_scopes_and_required_inputs_match(self):
        """两种范围共享命令、输入和结果合同，封装测试不能替代运行验收。"""
        base, change = self.by_id[BASE], self.by_id[BASE + "-on-change"]
        self.assertEqual((base["timeout_seconds"], change["timeout_seconds"]), (240, 240))
        for key in (
            "command",
            "required_environment",
            "input_paths",
            "result_contract",
        ):
            self.assertEqual(base[key], change[key], key)
        self.assertEqual(["python3", "ops/docker/tests/module_check_adapter.py"], base["command"])
        self.assertEqual(["python3"], base["required_environment"])
        self.assertEqual([], base["module_dependencies"])
        for name in (
            "ops/docker",
            "ops/release/lifecycle-docker.sh",
            "ops/release/lifecycle-state.sh",
            "ops/release/lifecycle.sh",
            "backend/product/api/src/main/java/io/lexiflow/api/release/RuntimeHealthCommand.java",
            "backend/product/api/src/test/java/io/lexiflow/api/release/RuntimeHealthCommandTest.java",
            "requirements-dev.txt",
        ):
            self.assertIn(name, base["input_paths"])
        for name in base["input_paths"]:
            selected = select_checks_for_changes(self.checks, [name + "/entrypoint.sh" if name == "ops/docker" else name])
            self.assertIn(BASE + "-on-change", {c["check_id"] for c in selected}, name)

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


class DockerAdapterDiagnosticTest(unittest.TestCase):
    """即时日志不改变标准 adapter 的完整性与失败关闭语义。"""

    def adapter(self):
        """只导入入口定义，发现器由合成套件替代。"""
        spec = importlib.util.spec_from_file_location(
            "synthetic_docker_adapter", ROOT / "ops/docker/tests/module_check_adapter.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_live_output_and_final_digest(self):
        """第二例开始前第一例已输出，终态摘要绑定同一份非重复日志。"""
        stream = io.StringIO()
        parent = self

        class SyntheticCase(unittest.TestCase):
            def test_first(self):
                pass

            def test_second(self):
                parent.assertIn("test_first", stream.getvalue())
                parent.assertIn("ok", stream.getvalue())

        module = self.adapter()
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticCase)
        with redirect_stderr(stream), patch.object(unittest.defaultTestLoader, "discover", return_value=suite):
            report = module.run_tests()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["checks_run"], 2)
        self.assertEqual(sum(line.startswith("test_first (") for line in stream.getvalue().splitlines()), 1)
        self.assertEqual(stream.getvalue().count("Ran 2 tests"), 1)
        self.assertEqual(report["tool_output_sha256"], hashlib.sha256(stream.getvalue().encode()).hexdigest())

    def test_empty_and_skipped_suites_remain_fail_closed(self):
        """空套件与跳过不会因流式日志而被错误标记通过。"""
        class SkippedCase(unittest.TestCase):
            @unittest.skip("synthetic skip")
            def test_skipped(self):
                pass

        for suite in (unittest.TestSuite(), unittest.defaultTestLoader.loadTestsFromTestCase(SkippedCase)):
            with self.subTest(), redirect_stderr(io.StringIO()), patch.object(
                unittest.defaultTestLoader, "discover", return_value=suite
            ):
                report = self.adapter().run_tests()
            self.assertEqual(report["status"], "FAIL")
