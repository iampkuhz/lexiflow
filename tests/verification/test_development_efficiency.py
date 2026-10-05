"""开发验证效率优化的直接回归测试。

覆盖：
- kernel probe_python_package_yaml 在安全子环境中验证 yaml
- 真实 venv 无 yaml 时 probe 失败
- 父环境 PYTHONPATH 可导入的合成 yaml 不渗入安全子环境
- kernel 的 python3 执行器替换为 sys.executable
- ops/verification adapter 阶段计时和逐用例计时
- module-checks.yaml 的 build-contract 前置顺序和集合保持
- 输入漂移仍使证据失效（真实文件变动）
- select_checks_for_changes 依赖顺序
"""

from __future__ import annotations

import importlib.util
import io
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import venv
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]


class TestKernelYamlProbe(unittest.TestCase):
    """在与 Check 一致的解释器和安全环境中实际导入 YAML。"""

    def test_probe_uses_current_interpreter_and_cleans_parent_pythonpath(self):
        from scripts.verification.kernel import (
            build_child_environment,
            probe_python_package_yaml,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            fake_path = root / "alternate"
            fake_path.mkdir()
            fake_python = fake_path / "python3"
            fake_python.write_text("#!/bin/sh\nexit 91\n")
            fake_python.chmod(0o755)
            venv_dir = root / "venv"
            venv.create(venv_dir, with_pip=False, clear=True)
            venv_python = venv_dir / "bin" / "python"
            synthetic = root / "synthetic"
            synthetic.mkdir()
            (synthetic / "yaml.py").write_text("__file__ = 'synthetic-yaml'\n")
            # 父解释器可以看见合成模块，build_child_environment 必须剔除该注入。
            with patch.dict(os.environ, {"PYTHONPATH": str(synthetic), "PATH": str(fake_path)}):
                visible = subprocess.run(
                    [str(venv_python), "-c", "import yaml; print(yaml.__file__)"],
                    cwd=root, env=dict(os.environ), capture_output=True, text=True,
                    timeout=5, check=False,
                )
                self.assertEqual(visible.returncode, 0)
                self.assertIn("synthetic-yaml", visible.stdout)
                child_env = build_child_environment()
                self.assertNotIn("PYTHONPATH", child_env)
                with patch("scripts.verification.kernel.sys.executable", str(venv_python)):
                    result = probe_python_package_yaml(child_env, root)
            self.assertFalse(result["available"])

    def test_probe_succeeds_in_actual_interpreter(self):
        from scripts.verification.kernel import (
            build_child_environment,
            probe_python_package_yaml,
        )
        result = probe_python_package_yaml(build_child_environment(), ROOT)
        self.assertTrue(result["available"])
        self.assertTrue(result["origin"])


class TestKernelExecutorReplacement(unittest.TestCase):
    """kernel 的 run_check_process 将 python3 替换为 sys.executable。"""

    def test_python3_replaced_with_sys_executable(self):
        """声明 python3 的 Check 子进程 argv[0] 应为 sys.executable。"""
        from scripts.verification.kernel import run_check_process

        captured_argv = []

        def fake_popen(argv, **kwargs):
            captured_argv.extend(argv)
            proc = MagicMock()
            proc.pid = 99999
            proc.communicate.return_value = (b"", b"")
            proc.returncode = 0
            return proc

        with patch("scripts.verification.kernel.subprocess.Popen", side_effect=fake_popen):
            run_check_process(
                ["python3", "-c", "pass"],
                str(ROOT),
                dict(os.environ),
                10,
                "python3",
            )
        self.assertEqual(captured_argv[0], sys.executable)
        self.assertNotEqual(captured_argv[0], "python3")

    def test_non_python_executable_not_replaced(self):
        """非 python3 的执行器不应被替换。"""
        from scripts.verification.kernel import run_check_process

        captured_argv = []

        def fake_popen(argv, **kwargs):
            captured_argv.extend(argv)
            proc = MagicMock()
            proc.pid = 99999
            proc.communicate.return_value = (b"", b"")
            proc.returncode = 0
            return proc

        with patch("scripts.verification.kernel.subprocess.Popen", side_effect=fake_popen):
            run_check_process(
                ["node", "-e", "1"],
                str(ROOT),
                dict(os.environ),
                10,
                "node",
            )
        self.assertEqual(captured_argv[0], "node")


class TestKernelYamlProbeIntegration(unittest.TestCase):
    """真实 Check 子进程只在 YAML 探针通过后启动。"""

    def test_probe_blocks_runner_and_marker_when_yaml_unavailable(self):
        from scripts.verification.kernel import (
            build_child_environment,
            execute_single_check,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_dir = root / "venv"
            venv.create(venv_dir, with_pip=False, clear=True)
            venv_python = venv_dir / "bin" / "python"
            marker = root / "marker"
            check = {
                "check_id":"test.yaml.check", "module":"test",
                "command":["python3", "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                "executable":"python3", "cwd":".", "timeout_seconds":10,
                "required_environment":["python-package-yaml"], "input_paths":[],
                "consumed_inputs":[], "result_contract":{"type":"exit-code", "completeness_guarantee":"test"},
            }
            env = build_child_environment({"PATH":str(venv_dir / "bin")})
            with patch("scripts.verification.kernel.sys.executable", str(venv_python)):
                result = execute_single_check(check, root, env)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertFalse(marker.exists())

    def test_real_check_runner_creates_marker_after_successful_probe(self):
        from scripts.verification.kernel import (
            build_child_environment,
            execute_single_check,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            marker = root / "marker"
            check = {
                "check_id":"test.yaml.check", "module":"test",
                "command":["python3", "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                "executable":"python3", "cwd":".", "timeout_seconds":10,
                "required_environment":["python-package-yaml"], "input_paths":[],
                "consumed_inputs":[], "result_contract":{"type":"exit-code", "completeness_guarantee":"test"},
            }
            result = execute_single_check(check, root, build_child_environment())
            self.assertEqual(result["status"], "PASS")
            self.assertTrue(marker.exists())


def _load_ops_adapter():
    """用 importlib 加载 ops adapter 模块，绕过缺少 __init__.py 的问题。"""
    adapter_path = ROOT / "ops" / "docker" / "tests" / "module_check_adapter.py"
    spec = importlib.util.spec_from_file_location(
        "_test_ops_adapter", str(adapter_path)
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestOpsAdapterTiming(unittest.TestCase):
    """ops/docker/tests/module_check_adapter.py 的阶段和逐用例计时。"""

    def test_timing_fields_with_synthetic_suite(self):
        """用合成 suite 验证阶段计时和逐用例计时字段。"""
        mod = _load_ops_adapter()

        # 创建合成测试
        class FastCase(unittest.TestCase):
            def test_fast(self):
                pass

        class SlowCase(unittest.TestCase):
            def test_slow(self):
                time.sleep(0.02)

        suite = unittest.TestSuite()
        suite.addTest(FastCase("test_fast"))
        suite.addTest(SlowCase("test_slow"))

        fake_node = MagicMock()
        fake_node.stdout = json.dumps({"status": "PASS", "checks_run": 3})
        fake_node.stderr = ""
        fake_node.returncode = 0

        with patch.object(mod, "subprocess") as mock_sub, patch.object(
            mod.unittest.defaultTestLoader, "discover", return_value=suite
        ):
            mock_sub.run.return_value = fake_node
            mock_sub.TimeoutExpired = subprocess.TimeoutExpired
            report = mod.run_tests()

        self.assertIn("python_unittest_duration_seconds", report)
        self.assertIn("node_suite_duration_seconds", report)
        self.assertIn("total_duration_seconds", report)
        self.assertIn("per_test_timing", report)
        self.assertEqual(len(report["per_test_timing"]), 2)
        self.assertIn("top_slow_tests", report)
        self.assertEqual(report["top_slow_tests"][0]["test"].split(".")[-1], "test_slow")

    def test_failure_still_reports_timing(self):
        """测试失败时仍应报告计时字段。"""
        mod = _load_ops_adapter()

        class FailingCase(unittest.TestCase):
            def test_fail(self):
                self.fail("intentional")

        suite = unittest.TestSuite()
        suite.addTest(FailingCase("test_fail"))

        fake_node = MagicMock()
        fake_node.stdout = json.dumps({"status": "PASS", "checks_run": 0})
        fake_node.stderr = ""
        fake_node.returncode = 0

        with contextlib.redirect_stderr(io.StringIO()), patch.object(
            mod, "subprocess"
        ) as mock_sub, patch.object(
            mod.unittest.defaultTestLoader, "discover", return_value=suite
        ):
            mock_sub.run.return_value = fake_node
            mock_sub.TimeoutExpired = subprocess.TimeoutExpired
            report = mod.run_tests()

        self.assertEqual(report["status"], "FAIL")
        self.assertIn("python_unittest_duration_seconds", report)
        self.assertIn("per_test_timing", report)
        self.assertNotIn("top_slow_tests", report)  # 失败时不输出 top slow


class TestVerificationAdapterTiming(unittest.TestCase):
    """tests/verification/module_check_adapter.py 的阶段和逐用例计时。"""

    def test_timed_result_records_per_test(self):
        """_TimedResult 应记录每个用例的耗时。"""
        from tests.verification.module_check_adapter import _TimedResult

        class SlowCase(unittest.TestCase):
            def test_a(self):
                time.sleep(0.02)

            def test_b(self):
                time.sleep(0.01)

        suite = unittest.TestSuite()
        suite.addTest(SlowCase("test_a"))
        suite.addTest(SlowCase("test_b"))
        stream = io.StringIO()
        timed = unittest.TextTestRunner(
            stream=stream, verbosity=0, resultclass=_TimedResult
        ).run(suite)
        self.assertEqual(len(timed.per_test), 2)
        for seconds in timed.per_test.values():
            self.assertGreaterEqual(seconds, 0)
            self.assertIsInstance(seconds, float)

    def test_top_slow_ordering(self):
        """_top_slow 应按耗时降序排列。"""
        from tests.verification.module_check_adapter import _top_slow

        per_test = {
            "fast": 0.001,
            "medium": 0.05,
            "slow": 0.2,
        }
        top = _top_slow(per_test, limit=2)
        self.assertEqual(len(top), 2)
        self.assertEqual(top[0]["test"], "slow")
        self.assertEqual(top[1]["test"], "medium")


class TestAdapterCompleteness(unittest.TestCase):
    """计时不得改变真实 adapter 的完整性判定。"""

    def test_both_adapters_preserve_success_failure_skip_and_empty(self):
        from tests.verification import module_check_adapter as verification
        ops = _load_ops_adapter()

        class Case(unittest.TestCase):
            def test_ok(self):
                pass
            def test_failure(self):
                self.fail("synthetic failure")
            def test_skip(self):
                self.skipTest("synthetic skip must make adapter FAIL")

        node = subprocess.CompletedProcess([], 0, json.dumps({"status":"PASS", "checks_run":1}), "")
        for module in [verification, ops]:
            for name, expected in [("test_ok", "PASS"), ("test_failure", "FAIL"), ("test_skip", "FAIL"), (None, "FAIL")]:
                with self.subTest(adapter=module.__name__, case=name):
                    suite = unittest.TestSuite([Case(name)] if name else [])
                    with contextlib.redirect_stderr(io.StringIO()), patch.object(
                        unittest.defaultTestLoader, "discover", return_value=suite
                    ), patch.object(ops.subprocess, "run", return_value=node):
                        report = verification.verification_tests(ROOT) if module is verification else ops.run_tests()
                    self.assertEqual(report["status"], expected)
                    self.assertGreaterEqual(report["python_unittest_duration_seconds"], 0)
                    if name:
                        self.assertEqual(len(report["per_test_timing"]), 1)
                    if expected == "PASS":
                        self.assertLessEqual(len(report["top_slow_tests"]), 10)


class TestModuleChecksBuildContractOrder(unittest.TestCase):
    """module-checks.yaml 的 build-contract 前置顺序和集合保持。"""

    def test_build_contract_before_verification(self):
        """build-contract 应在 verification 之前声明。"""
        import yaml

        data = yaml.safe_load((ROOT / "harness" / "module-checks.yaml").read_text())
        ids = [c["check_id"] for c in data["checks"]]
        bc_baseline = ids.index("eng.backend.build-contract")
        bc_change = ids.index("eng.backend.build-contract-on-change")
        ver_baseline = ids.index("eng.verification.module-tests-on-change")
        self.assertLess(bc_baseline, ver_baseline)
        self.assertLess(bc_change, ver_baseline)



class TestInputDriftWithRealFileChange(unittest.TestCase):
    """输入漂移仍使证据失效，使用真实文件变动。"""

    def test_real_file_modification_detected_as_drift(self):
        """真实文件修改应被检测为 input-drift。"""
        from scripts.verification.kernel import (
            execute_single_check,
            snapshot_check_inputs,
        )

        check = {
            "check_id": "test.drift.check",
            "module": "test",
            "command": ["python3", "-c", "import sys; sys.exit(0)"],
            "executable": "python3",
            "cwd": ".",
            "timeout_seconds": 10,
            "required_environment": [],
            "input_paths": ["input.txt"],
            "consumed_inputs": [],
            "result_contract": {
                "type": "exit-code",
                "completeness_guarantee": "test",
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_file = root / "input.txt"
            input_file.write_text("original content")

            # 捕获初始快照
            pre = snapshot_check_inputs(root, check)

            # 修改文件
            input_file.write_text("modified content")

            def pass_runner(argv, cwd, env, timeout, executable=None, **kw):
                return {
                    "status": "PASS",
                    "exit_code": 0,
                    "exit_reason": "exited",
                    "stdout": "",
                    "stderr": "",
                    "duration_seconds": 0.01,
                    "started_at": "",
                    "finished_at": "",
                    "timed_out": False,
                    "executable": sys.executable,
                    "executed_argv": argv,
                }

            result = execute_single_check(
                check, root, dict(os.environ),
                runner=pass_runner,
                frozen_pre=pre,
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "input-drift")


class TestSelectChecksOrdering(unittest.TestCase):
    """验证真实选择、依赖拓扑与路径精确匹配。"""
    def test_selection_and_dependency_order(self):
        import yaml

        from scripts.verification.scope import (
            resolve_module_dependencies,
            select_checks_for_changes,
        )
        checks = yaml.safe_load((ROOT / "harness/module-checks.yaml").read_text())["checks"]
        selected = select_checks_for_changes(checks, ["backend/settings.gradle.kts", "scripts/verification/kernel.py"])
        ordered = resolve_module_dependencies(selected, checks)
        ids = [item["check_id"] for item in ordered]
        self.assertIn("eng.verification.module-tests-on-change", ids)
        self.assertLess(ids.index("eng.backend.build-contract-on-change"), ids.index("eng.verification.module-tests-on-change"))
        # 前缀相似但不是同一模块的候选不得被当成依赖。
        dependent = [{"check_id":"consumer", "module":"consumer", "module_dependencies":["backend"]}]
        all_checks = [
            {"check_id":"wrong", "module":"backend-build-contract", "module_dependencies":[]},
            {"check_id":"right", "module":"backend", "module_dependencies":[]},
            dependent[0],
        ]
        self.assertEqual([x["check_id"] for x in resolve_module_dependencies(dependent, all_checks)], ["right", "consumer"])


if __name__ == "__main__":
    unittest.main()
