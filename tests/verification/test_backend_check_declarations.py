"""实际 backend 规范声明的场景资格及单次执行合同，不运行 Java。"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.verification.planning.check_definitions import filter_checks_by_scope, load_declarations
from scripts.verification.execution.check_plan_runner import (
    verify_repository as production_verify_repository,
)
from tests.verification.declaration_fixtures import write_declarations
from tests.verification.test_scenarios import verify_repository


class BackendCheckDeclarationTest(unittest.TestCase):
    """后端单一规范 Check 保留场景资格且拒绝撤销入口。"""

    def fixture(self, root):
        """保留真实命令与结果合同，用合成资源隔离环境依赖。"""
        repository = Path(__file__).resolve().parents[2]
        declarations = load_declarations(repository)
        checks = [
            copy.deepcopy(c)
            for c in declarations["checks"]
            if c["check_id"] == "eng.backend.delivery"
        ]
        self.assertEqual(1, len(checks))
        check = checks[0]
        for scope in ("repository-baseline", "change-targeted"):
            views = filter_checks_by_scope(checks, scope)
            self.assertEqual(["eng.backend.delivery"], [c["check_id"] for c in views])
            self.assertEqual(check["command"], views[0]["command"])
        check["input_paths"] = ["input.txt"]
        check["required_environment"] = []
        check["module_dependencies"] = []
        (root / "input.txt").write_text("fixture")
        write_declarations(root / "config/engineering/module-checks.yaml", checks)

    def test_scene_membership_executes_one_canonical_delivery(self):
        """同一 Check 的两场景资格不产生重复执行或去重副本。"""
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
            self.fixture(root)
            report = verify_repository(
                root, required_check_ids=("eng.backend.delivery",), runner=runner
            )
        self.assertEqual("PASS", report["result"], report)
        self.assertEqual(1, len(calls))
        self.assertEqual(
            ["eng.backend.delivery"], [item["check_id"] for item in report["checks"]]
        )
        self.assertEqual("exited", report["checks"][0]["process"]["exit_reason"])

    def test_removed_scene_alias_fails_closed_without_execution(self):
        """撤销的场景别名被显式拒绝，不调用执行器。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            report = verify_repository(
                root,
                required_check_ids=("eng.backend.delivery-on-change",),
                runner=lambda *_: self.fail("removed alias executed"),
            )
        self.assertEqual("FAIL", report["result"], report)
        self.assertEqual("unknown-required-check-id", report["reason"])

    def test_bootjar_generation_manifest_reads_are_frozen_and_trigger_actual_build_checks(
        self,
    ):
        """真实 Kotlin 固定生成器与 manifest 展开源均绑定两个实际 build Check。"""
        from scripts.verification.execution.check_runner import snapshot_check_inputs
        from scripts.verification.planning.check_scope_selection import select_checks_for_changes

        root = Path(__file__).resolve().parents[2]
        checks = load_declarations(root)["checks"]
        manifest = json.loads(
            (root / "ops/dataset/generation-inputs.json").read_bytes()
        )
        reads = {
            "ops/dataset/dataset-generation-identity.mjs",
            "ops/dataset/generation-inputs.json",
        }
        for pattern in manifest["include"]:
            if pattern.endswith("/**"):
                directory = root / pattern[:-3]
                self.assertTrue(directory.is_dir(), pattern)
                reads.update(
                    path.relative_to(root).as_posix()
                    for path in directory.rglob("*")
                    if path.is_file()
                )
            else:
                self.assertTrue((root / pattern).is_file(), pattern)
                reads.add(pattern)
        for check_id in ("eng.backend.delivery", "eng.release.lifecycle-runtime"):
            check = next(check for check in checks if check["check_id"] == check_id)
            frozen = snapshot_check_inputs(root, check)
            files = {item["locator"] for item in frozen["files"]}
            self.assertEqual(frozen["missing"], [])
            self.assertTrue(reads.issubset(files), sorted(reads - files))
            for path in sorted(reads):
                with self.subTest(check=check_id, read=path):
                    self.assertIn(
                        check_id,
                        {
                            item["check_id"]
                            for item in select_checks_for_changes(checks, [path])
                        },
                    )

    def test_removed_public_runner_and_execution_mode_are_rejected(self):
        """生产公开 API 拒绝 runner 注入和诊断模式参数。"""
        for kwargs in ({"runner": lambda *_: None}, {"execution_mode": "diagnostic"}):
            with self.subTest(kwargs=tuple(kwargs)), self.assertRaises(TypeError):
                production_verify_repository(".", **kwargs)
