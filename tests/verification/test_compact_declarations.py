"""验证单一 Check 身份、依赖缺省与封闭场景声明。"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml

from scripts.verification.planning.check_definitions import (
    DeclarationError,
    _parse_declarations,
    filter_checks_by_scope,
    load_declarations,
)
from scripts.verification.execution.check_plan_runner import (
    _not_run_after_stop,
    _not_run_input_drift,
    _runtime_checks,
)
from scripts.verification.execution.check_runner import _not_run_result, snapshot_check_inputs

ROOT = Path(__file__).resolve().parents[2]


class CompactDeclarationsTest(unittest.TestCase):
    """最新声明拒绝旧接口，省略空依赖不能削弱依赖校验。"""

    def setUp(self):
        """为每个负向用例独立加载原始声明。"""
        self.raw = yaml.safe_load((ROOT / "config/engineering/module-checks.yaml").read_text())

    def parse(self, document=None):
        """通过真实加载器解析独立字节流。"""
        return _parse_declarations(yaml.safe_dump(document or self.raw).encode())

    def test_only_real_dependency_edges_are_declared_and_absence_defaults_empty(self):
        """无依赖声明可以省略，非空依赖仍有唯一稳定 Check 身份。"""
        declared = [c for c in self.raw["checks"] if "module_dependencies" in c]
        self.assertEqual(2, len(declared))
        self.assertTrue(all(c["module_dependencies"] for c in declared))
        self.assertTrue(all("module" not in c for c in self.raw["checks"]))
        self.assertTrue(all("module" not in c for c in self.parse()["checks"]))
        first = self.parse()["checks"][0]
        self.assertEqual([], first["module_dependencies"])

    def test_removed_module_and_legacy_schema_are_rejected(self):
        """不得保留 module 名称镜像或旧 schema 兼容窗口。"""
        for field, value in (("module", "release-version"), ("execution", "alias")):
            document = copy.deepcopy(self.raw)
            document["checks"][0][field] = value
            with self.subTest(field=field), self.assertRaises(DeclarationError):
                self.parse(document)
        self.raw["schema_version"] = "lexiflow.module-checks.v4"
        with self.assertRaises(DeclarationError):
            self.parse()

    def test_registry_input_is_fixed_runtime_behavior_not_repeated_configuration(self):
        """每项实际冻结都包含 Registry，不再要求逐项复制固定输入。"""
        self.assertTrue(
            all(
                "config/engineering/module-checks.yaml" not in check["input_paths"]
                for check in self.raw["checks"]
            )
        )
        for check in _runtime_checks(self.parse()["checks"]):
            with self.subTest(check_id=check["check_id"]):
                self.assertEqual(
                    1, check["input_paths"].count("config/engineering/module-checks.yaml")
                )
                snapshot = snapshot_check_inputs(ROOT, check)
                self.assertIn(
                    "config/engineering/module-checks.yaml",
                    [item["locator"] for item in snapshot["files"]],
                )

    def test_dependencies_reject_missing_duplicate_wrong_shape_and_cycles(self):
        """缺项、重复、坏类型和依赖环都在加载时失败关闭。"""
        for dependencies in (["missing.check"], ["eng.repository.docs"] * 2, None, "x"):
            document = copy.deepcopy(self.raw)
            document["checks"][0]["module_dependencies"] = dependencies
            with (
                self.subTest(dependencies=dependencies),
                self.assertRaises(DeclarationError),
            ):
                self.parse(document)
        self.raw["checks"][0]["module_dependencies"] = ["eng.repository.docs"]
        self.raw["checks"][1]["module_dependencies"] = ["eng.release.version"]
        with self.assertRaisesRegex(DeclarationError, "module-dependency-cycle"):
            self.parse()

    def test_scenario_eligibility_rejects_override_duplicate_and_unknown_interfaces(
        self,
    ):
        """场景仅声明资格，不接受第二触发源或含糊资格。"""
        for scenarios in (
            {"repository-baseline": {"triggers": []}},
            ["repository-baseline", "repository-baseline"],
            ["unknown"],
            [],
            [None],
        ):
            document = copy.deepcopy(self.raw)
            document["checks"][0]["scenarios"] = scenarios
            with self.subTest(scenarios=scenarios), self.assertRaises(DeclarationError):
                self.parse(document)
        self.raw["checks"][0]["triggers"] = None
        with self.assertRaises(DeclarationError):
            self.parse()

    def test_full_baseline_uses_membership_not_targeted_trigger_view(self):
        """完整基线不依赖路径触发，变更视图保留唯一触发源。"""
        checks = load_declarations(ROOT)["checks"]
        baseline = filter_checks_by_scope(checks, "repository-baseline")
        targeted = filter_checks_by_scope(checks, "change-targeted")
        self.assertEqual(19, len(baseline))
        self.assertEqual(22, len(targeted))
        self.assertTrue(all(c["triggers"] == [] for c in baseline))
        by_id = {c["check_id"]: c for c in checks}
        self.assertTrue(
            all(c["triggers"] == by_id[c["check_id"]]["triggers"] for c in targeted)
        )

    def test_not_run_results_only_expose_stable_check_id(self):
        """失败、漂移和前置失败也不能重建 module 镜像字段。"""
        check = self.parse()["checks"][0]
        for result in (
            _not_run_result(check, "input-missing"),
            _not_run_input_drift(check, {}),
            _not_run_after_stop(check, {}, "prior-check-failed"),
        ):
            self.assertEqual(check["check_id"], result["check_id"])
            self.assertNotIn("module", result)

    def test_baseline_only_ci_trigger_has_one_validated_source(self):
        """Docker 全组的 CI 路径消费者仍需要唯一 root triggers。"""
        check = next(
            c
            for c in self.parse()["checks"]
            if c["check_id"] == "eng.release.docker-contract"
        )
        self.assertEqual(["repository-baseline"], check["scenarios"])
        self.assertTrue(check["triggers"])
        for triggers in (None, [{"path": "a/*"}], [{"path": "a", "extra": True}]):
            document = copy.deepcopy(self.raw)
            raw_check = next(
                c for c in document["checks"] if c["check_id"] == check["check_id"]
            )
            raw_check["triggers"] = triggers
            with self.subTest(triggers=triggers), self.assertRaises(DeclarationError):
                self.parse(document)
