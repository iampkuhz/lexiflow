"""以真实选择、冻结与有界执行核对工程配置的职责边界。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.verification.planning.check_definitions import REGISTRY_PATH, load_declarations
from scripts.verification.execution.check_runner import snapshot_check_inputs
from scripts.verification.execution.check_plan_runner import freeze_inputs, verify_profile
from scripts.verification.planning.check_scope_selection import select_checks_for_changes, uncovered_changed_paths
from tests.verification.test_fixture_e2e import _init_git_repo

ROOT = Path(__file__).resolve().parents[2]


class RegistryResponsibilityTest(unittest.TestCase):
    """不注入虚假策略，用实际实现源与配置输入验证最新 Registry。"""

    def test_rules_and_mutable_configuration_select_only_actual_consumers(self):
        """人规则触发文档，配置触发对应实现测试，不扩大到全部产品模块。"""
        checks = load_declarations(ROOT)["checks"]
        expected = {
            "harness/agent-policy.md": {"eng.repository.docs"},
            "harness/documentation-policy.md": {"eng.repository.docs"},
            "config/engineering/risk.yaml": {
                "eng.verification.module-tests", "eng.agents.module-tests",
                "eng.delivery-gate.module-tests", "eng.repository.module-tests",
            },
            "config/engineering/python-quality.toml": {
                "eng.repository.python-quality", "eng.repository.module-tests",
            },
        }
        for path, identifiers in expected.items():
            with self.subTest(path=path):
                selected = select_checks_for_changes(checks, [path])
                self.assertEqual(identifiers, {c["check_id"] for c in selected})
                self.assertEqual([], uncovered_changed_paths(checks, [path]))
                for check in selected:
                    snapshot = snapshot_check_inputs(ROOT, check)
                    self.assertEqual([], snapshot["missing"])
                    self.assertIn(path, {f["locator"] for f in snapshot["files"]})
        self.assertEqual(
            ["config/engineering/unknown.yaml"],
            uncovered_changed_paths(checks, ["config/engineering/unknown.yaml"]),
        )

    def test_input_defaults_execute_and_detect_real_set_and_content_drift(self):
        """无重复触发声明仍执行真实 Check，并拒绝输入增删或内容漂移。"""
        payload = {"status": "PASS", "checks_run": 1, "failures": 0,
                   "errors": 0, "skipped": 0, "reason": ""}
        document = {
            "checks": [{"check_id": "fixture.config-consumer",
                        "command": ["python3", "-c", f"print({json.dumps(payload)!r})"],
                        "input_paths": ["config/engineering/data", "harness"],
                        "scenarios": ["repository-baseline", "change-targeted"]}],
            "scenario_policy": {"ci_quick": {
                "always_required_check_ids": [], "formal_only_check_ids": []}},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _init_git_repo(root)
            config = root / "config/engineering/data/values.yaml"
            config.parent.mkdir(parents=True)
            config.write_text("value: bounded\n")
            rule = root / "harness/rule.md"
            rule.parent.mkdir()
            rule.write_text("# 1. 合成规则\n")
            (root / REGISTRY_PATH).write_text(yaml.safe_dump(document))
            freeze = freeze_inputs(root, verification_scope="development-change",
                                   base="HEAD", _subject_changed_files=[
                                       "config/engineering/data/values.yaml"])
            self.assertEqual("PASS", freeze["result"], freeze)
            self.assertEqual("PASS", verify_profile(root, frozen_inputs=freeze)["result"])
            fresh = root / "config/engineering/data/new.yaml"
            fresh.write_text("new: value\n")
            self.assertEqual("FAIL", verify_profile(root, frozen_inputs=freeze)["result"])
            fresh.unlink()
            config.write_text("value: changed\n")
            self.assertEqual("FAIL", verify_profile(root, frozen_inputs=freeze)["result"])
            config.write_text("value: bounded\n")
            rule.unlink()
            self.assertEqual("FAIL", verify_profile(root, frozen_inputs=freeze)["result"])

    def test_registry_change_selects_all_eligible_checks_without_config_repetition(self):
        """固定 Registry 消费边由代码提供，资格边界和正式专属保持独立。"""
        checks = load_declarations(ROOT)["checks"]
        selected = select_checks_for_changes(checks, [REGISTRY_PATH])
        self.assertEqual(
            {c["check_id"] for c in checks if "change-targeted" in c["scenarios"]},
            {c["check_id"] for c in selected},
        )
        self.assertEqual(22, len(selected))

