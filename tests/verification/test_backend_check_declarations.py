"""使用实际 backend 声明验证同轮去重；runner 是 fixture，不执行或扫描 Java。"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.verification import verify_repository


class BackendCheckDeclarationTest(unittest.TestCase):
    def test_scope_variants_execute_delivery_once(self):
        repository = Path(__file__).resolve().parents[2]
        declarations = yaml.safe_load(
            (repository / "harness/module-checks.yaml").read_text()
        )
        ids = {"eng.backend.delivery", "eng.backend.delivery-on-change"}
        checks = [
            copy.deepcopy(c) for c in declarations["checks"] if c["check_id"] in ids
        ]
        self.assertEqual(2, len(checks))
        # 替换物理资源为有界 fixture；真实 command/result contract 保持，暴露说明漂移。
        for check in checks:
            check["input_paths"] = ["input.txt"]
            check["required_environment"] = []
            check["module_dependencies"] = []
        calls = []

        def runner(argv, *_):
            calls.append(argv)
            return {
                "exit_code": 0,
                "exit_reason": "exited",
                "timed_out": False,
                "stdout": "",
                "stderr": "",
                "executed_argv": argv,
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            (root / "input.txt").write_text("fixture")
            (root / "harness/module-checks.yaml").write_text(
                yaml.safe_dump(
                    {
                        "schema_version": "lexiflow.module-checks.v1",
                        "checks": checks,
                    }
                )
            )
            report = verify_repository(
                root,
                required_check_ids=("eng.backend.delivery-on-change",),
                runner=runner,
            )
        self.assertEqual("PASS", report["result"], report)
        self.assertEqual(1, len(calls))
        self.assertEqual(ids, {item["check_id"] for item in report["checks"]})
        self.assertEqual(
            1,
            sum(
                item["process"]["exit_reason"] == "deduplicated"
                for item in report["checks"]
            ),
        )
