"""发行入口 Check 的选择、冻结输入和失败关闭合同回归。"""

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
from scripts.verification.scope import select_checks_for_changes

ROOT = Path(__file__).resolve().parents[2]
BASE = "eng.release.runtime-entry"


class RuntimeEntryCheckTest(unittest.TestCase):
    """证明真实发行入口代码及测试进入标准 Verify，而非仅执行文档检查。"""

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
        self.assertEqual(["node", "ops/release/runtime-entry-check.mjs"], base["command"])
        self.assertEqual(["node", "git", "sh", "mktemp", "sha256-tool"], base["required_environment"])
        self.assertEqual([], base["module_dependencies"])
        for name in (
            "manifest.mjs",
            "package.mjs",
            "runtime-verification.sh",
            "runtime-entry-check.mjs",
            "tests/runtime-entry.test.mjs",
            "runtime-entry.mjs",
            "version.mjs",
            "version.txt",
            "check.mjs",
        ):
            self.assertIn("ops/release/" + name, base["input_paths"])
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
