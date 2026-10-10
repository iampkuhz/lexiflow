"""冻结开发和基线 profile 并校核发布报告与输入新鲜度。"""

from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path


from scripts.verification import (
    freeze_inputs,
    persist_report,
    read_report,
    verify_frozen_inputs,
    verify_profile,
)
from tests.verification.declaration_fixtures import write_declarations


def _check(check_id: str, scope: str = "repository-baseline") -> dict:
    return {
        "check_id": check_id,
        "command": [
            "python3",
            "-c",
            "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
        ],
        "executable": "python3",
        "cwd": ".",
        "timeout_seconds": 5,
        "scope": scope,
        "triggers": [{"path": "src/"}],
        "module_dependencies": [],
        "required_environment": [],
        "input_paths": ["src"],
        "result_contract": {"type": "exit-code", "completeness_guarantee": "fixture"},
    }


class DevelopmentProfileFreezeTests(unittest.TestCase):
    def test_development_baseline_omits_policy_formal_only_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness").mkdir()
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("fixture")
            checks = [
                _check("eng.release.lifecycle-runtime"),
                _check("eng.release.candidate-runtime"),
                _check("synthetic.release.lifecycle"),
            ]
            write_declarations(
                root / "config/engineering/module-checks.yaml",
                checks,
                ci_quick={
                    "always_required_check_ids": [],
                    "formal_only_check_ids": [
                        "eng.release.lifecycle-runtime",
                        "eng.release.candidate-runtime",
                    ],
                },
            )
            freeze = freeze_inputs(root, verification_scope="development-baseline")
            self.assertEqual(freeze["result"], "PASS")
            self.assertEqual(
                [x["check_id"] for x in freeze["checks"]],
                ["synthetic.release.lifecycle"],
            )
            self.assertTrue(verify_frozen_inputs(root, freeze))
            (root / "irrelevant.txt").write_text("not in selected closure")
            self.assertTrue(verify_frozen_inputs(root, freeze))
            (root / "src/new-dependent.py").write_text("new dependent input")
            self.assertFalse(verify_frozen_inputs(root, freeze))

    def test_development_required_formal_check_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness").mkdir()
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("fixture")
            write_declarations(
                root / "config/engineering/module-checks.yaml",
                [_check("eng.release.lifecycle-runtime")],
                ci_quick={
                    "always_required_check_ids": [],
                    "formal_only_check_ids": ["eng.release.lifecycle-runtime"],
                },
            )
            freeze = freeze_inputs(
                root,
                verification_scope="development-baseline",
                required_check_ids=["eng.release.lifecycle-runtime"],
            )
            self.assertEqual(freeze["result"], "FAIL")
            self.assertEqual(freeze["reason"], "formal-only-required-check")

    def test_development_change_freezes_real_git_subject_and_full_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness").mkdir()
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("seed")
            write_declarations(
                root / "config/engineering/module-checks.yaml",
                [_check("src.check", "change-targeted")],
            )
            for args in (
                ("init",),
                ("config", "user.email", "test@example.invalid"),
                ("config", "user.name", "Test"),
            ):
                subprocess.run(
                    ["git", *args],
                    cwd=root,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-m", "base"],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            base = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True
            ).strip()
            (root / "src/input.txt").write_text("changed")
            freeze = freeze_inputs(
                root, verification_scope="development-change", base="HEAD"
            )
            self.assertEqual(freeze["result"], "PASS")
            self.assertEqual(freeze["base"], base)
            self.assertEqual(freeze["changed_files"], ["src/input.txt"])
            self.assertEqual([x["check_id"] for x in freeze["checks"]], ["src.check"])
            (root / "unrelated.txt").write_text("outside selected closure")
            self.assertTrue(verify_frozen_inputs(root, freeze))
            report = verify_profile(root, frozen_inputs=freeze)
            self.assertEqual(report["result"], "PASS")
            self.assertEqual(report["scope"], "development-change")
            persist_report(root, report)
            loaded, _ = read_report(root, report["run_id"])
            self.assertEqual(loaded, report)
            declarations_path = root / "config/engineering/module-checks.yaml"
            original = declarations_path.read_bytes()
            write_declarations(
                declarations_path,
                [_check("src.check", "change-targeted")],
                ci_quick={
                    "always_required_check_ids": ["src.check"],
                    "formal_only_check_ids": [],
                },
            )
            self.assertFalse(verify_frozen_inputs(root, freeze))
            declarations_path.write_bytes(original)
            self.assertTrue(verify_frozen_inputs(root, freeze))
            forged = copy.deepcopy(freeze)
            forged["changed_files"] = []
            self.assertFalse(verify_frozen_inputs(root, forged))
            forged = copy.deepcopy(freeze)
            forged["checks"] = []
            self.assertFalse(verify_frozen_inputs(root, forged))
            forged = copy.deepcopy(freeze)
            forged["input_fingerprint"] = "0" * 64
            self.assertFalse(verify_frozen_inputs(root, forged))

    def test_profile_accepts_full_baseline_and_report_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "harness").mkdir()
            (root / "src").mkdir()
            (root / "src/input.txt").write_text("fixture")
            checks = [_check("baseline.one"), _check("eng.release.lifecycle-runtime")]
            write_declarations(root / "config/engineering/module-checks.yaml", checks)
            freeze = freeze_inputs(root)
            report = verify_profile(root, frozen_inputs=freeze)
            self.assertEqual(report["result"], "PASS")
            self.assertEqual(report["scope"], "repository-baseline")
            self.assertEqual(
                {x["check_id"] for x in report["checks"]},
                {x["check_id"] for x in checks},
            )
            persist_report(root, report)
            loaded, _ = read_report(root, report["run_id"])
            self.assertEqual(loaded, report)
            self.assertTrue(all("module" not in check for check in loaded["checks"]))
            forged = copy.deepcopy(report)
            forged["checks"][0]["module"] = "removed-presentation-label"
            with self.assertRaisesRegex(ValueError, "verification report check invalid"):
                persist_report(root, forged)


if __name__ == "__main__":
    unittest.main()
