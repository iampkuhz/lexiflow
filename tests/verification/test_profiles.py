from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.verification import (
    freeze_inputs,
    persist_report,
    read_report,
    verify_frozen_inputs,
    verify_profile,
)


def _check(check_id: str, scope: str = "repository-baseline") -> dict:
    return {
        "check_id": check_id,
        "module": check_id,
        "command": ["python3", "-c", "pass"],
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
            (root / "harness/module-checks.yaml").write_text(
                yaml.safe_dump(
                    {"schema_version": "lexiflow.module-checks.v1", "checks": checks}
                )
            )
            (root / "harness/ci-policy.yaml").write_text(
                yaml.safe_dump(
                    {
                        "formal_only_check_ids": [
                            "eng.release.lifecycle-runtime",
                            "eng.release.candidate-runtime",
                        ]
                    }
                )
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
            (root / "harness/module-checks.yaml").write_text(
                yaml.safe_dump(
                    {
                        "schema_version": "lexiflow.module-checks.v1",
                        "checks": [_check("eng.release.lifecycle-runtime")],
                    }
                )
            )
            (root / "harness/ci-policy.yaml").write_text(
                yaml.safe_dump(
                    {"formal_only_check_ids": ["eng.release.lifecycle-runtime"]}
                )
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
            (root / "harness/module-checks.yaml").write_text(
                yaml.safe_dump(
                    {
                        "schema_version": "lexiflow.module-checks.v1",
                        "checks": [_check("src.check", "change-targeted")],
                    }
                )
            )
            (root / "harness/ci-policy.yaml").write_text(
                yaml.safe_dump({"formal_only_check_ids": ["release.runtime"]})
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
            (root / "harness/ci-policy.yaml").write_text(
                yaml.safe_dump({"formal_only_check_ids": ["other.runtime"]})
            )
            self.assertFalse(verify_frozen_inputs(root, freeze))
            (root / "harness/ci-policy.yaml").write_text(
                yaml.safe_dump({"formal_only_check_ids": ["release.runtime"]})
            )
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
            (root / "harness/module-checks.yaml").write_text(
                yaml.safe_dump(
                    {"schema_version": "lexiflow.module-checks.v1", "checks": checks}
                )
            )
            freeze = freeze_inputs(root)
            report = verify_profile(
                root, frozen_inputs=freeze, execution_mode="diagnostic"
            )
            self.assertEqual(report["result"], "PASS")
            self.assertEqual(report["scope"], "repository-baseline")
            self.assertEqual(
                {x["check_id"] for x in report["checks"]},
                {x["check_id"] for x in checks},
            )
            persist_report(root, report)
            loaded, _ = read_report(root, report["run_id"])
            self.assertEqual(loaded, report)


if __name__ == "__main__":
    unittest.main()
