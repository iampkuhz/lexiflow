"""固定执行器与不可变 report 索引的同调用 Formal 批量复用回归。"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verification import (
    freeze_inputs,
    read_report,
    verify_profile,
    verify_profiles,
)
from scripts.verification.reports.verification_report_store import validate_report
from tests.verification.declaration_fixtures import write_declarations

OUTPUT = "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))"


class ProfileTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src/input.txt").write_text("initial")
        self.check = {
            "check_id": "fixture.shared",
            "command": ["python3", "-c", OUTPUT],
            "timeout_seconds": 5,
            "required_environment": [],
            "input_paths": ["src"],
            "transaction_reuse": True,
            "module_dependencies": [],
            "scenarios": ["repository-baseline", "change-targeted"],
            "triggers": [{"path": "src"}],
        }
        self.write()

    def write(self):
        write_declarations(self.root / "config/engineering/module-checks.yaml", [self.check])

    def profile(self, scope="development-baseline"):
        profile = freeze_inputs(self.root, verification_scope=scope)
        self.assertEqual(profile["result"], "PASS", profile)
        return profile

    def run_profiles(self, profiles, **kwargs):
        from scripts.verification.execution.check_runner import run_check_process

        with patch(
            "scripts.verification.execution.check_runner.run_check_process", wraps=run_check_process
        ) as process:
            reports = verify_profiles(self.root, frozen_profiles=profiles, **kwargs)
        return reports, process.call_count

    def test_same_canonical_check_is_reused_only_after_source_publication(self):
        profile = self.profile()
        seen = []

        def after(index, frozen, report):
            stored, descriptor = read_report(self.root, report["run_id"])
            self.assertEqual(descriptor, report["publication"])
            self.assertEqual(stored["result"], "PASS")
            seen.append(index)
            return True

        reports, count = self.run_profiles(
            [profile, copy.deepcopy(profile)], after_profile=after
        )
        self.assertEqual(count, 1)
        self.assertEqual(seen, [0, 1])
        self.assertEqual([r["result"] for r in reports], ["PASS", "PASS"])
        source, target = reports
        witness = target["checks"][0]["cross_view_source"]
        self.assertEqual(
            set(witness), {"source_report", "check_id", "transaction_nonce"}
        )
        self.assertEqual(witness["source_report"], source["publication"])
        self.assertEqual(target["checks"][0]["process"]["exit_reason"], "deduplicated")
        self.assertEqual(source["checks"][0]["process"]["exit_reason"], "exited")
        self.assertEqual(
            witness["transaction_nonce"], source["verification_transaction"]["nonce"]
        )

    def test_separate_calls_never_consume_prior_execution(self):
        profile = self.profile()
        from scripts.verification.execution.check_runner import run_check_process

        with patch(
            "scripts.verification.execution.check_runner.run_check_process", wraps=run_check_process
        ) as process:
            first = verify_profile(self.root, frozen_inputs=profile)
            second = verify_profile(self.root, frozen_inputs=profile)
        self.assertEqual([first["result"], second["result"]], ["PASS", "PASS"])
        self.assertEqual(process.call_count, 2)
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_unqualified_checks_execute_each_profile(self):
        self.check["transaction_reuse"] = False
        self.write()
        profile = self.profile()
        reports, count = self.run_profiles([profile, copy.deepcopy(profile)])
        self.assertEqual(count, 2)
        self.assertEqual([r["result"] for r in reports], ["PASS", "PASS"])

    def test_repository_profiles_also_share_qualified_execution(self):
        profile = self.profile("repository-baseline")
        reports, count = self.run_profiles([profile, copy.deepcopy(profile)])
        self.assertEqual(count, 1)
        self.assertEqual([r["result"] for r in reports], ["PASS", "PASS"])

    def test_stale_later_profile_blocks_before_any_execution(self):
        profile = self.profile()
        stale = copy.deepcopy(profile)
        stale["input_fingerprint"] = "0" * 64
        reports, count = self.run_profiles([profile, stale])
        self.assertEqual(count, 0)
        self.assertEqual([r["result"] for r in reports], ["BLOCKED", "FAIL"])

    def test_changed_input_between_profiles_prevents_reuse(self):
        profile = self.profile()

        def after(index, frozen, report):
            if index == 0:
                (self.root / "src/input.txt").write_text("drift")
            return True

        reports, count = self.run_profiles(
            [profile, copy.deepcopy(profile)], after_profile=after
        )
        self.assertEqual(count, 1)
        self.assertEqual(reports[-1]["result"], "FAIL")

    def test_pre_and_post_callbacks_remain_fail_closed(self):
        profile = self.profile()
        reports, count = self.run_profiles([profile], before_profile=lambda *_: False)
        self.assertEqual(count, 0)
        self.assertEqual(reports[0]["result"], "FAIL")
        reports, count = self.run_profiles([profile], after_profile=lambda *_: False)
        self.assertEqual(count, 1)
        self.assertEqual(reports[0]["result"], "FAIL")

    def test_missing_source_report_and_log_are_rejected(self):
        profile = self.profile()
        reports, _ = self.run_profiles([profile, copy.deepcopy(profile)])
        target = copy.deepcopy(reports[1])
        source_path = self.root / reports[0]["publication"]["locator"]
        raw = source_path.read_bytes()
        source_path.unlink()
        with self.assertRaises(ValueError):
            validate_report(self.root, target)
        source_path.write_bytes(raw)
        source_path.chmod(0o400)
        artifact = reports[0]["checks"][0]["process"]["output_artifacts"]["stdout"]
        (self.root / artifact["locator"]).unlink()
        with self.assertRaises(ValueError):
            validate_report(self.root, target)

    def test_forged_nonce_index_descriptor_or_reused_source_is_rejected(self):
        profile = self.profile()
        reports, _ = self.run_profiles([profile, copy.deepcopy(profile)])
        for mutate in (
            lambda r: r["checks"][0]["cross_view_source"].update(
                transaction_nonce="10000000-0000-4000-8000-000000000001"
            ),
            lambda r: r["verification_transaction"].update(profile_index=0),
            lambda r: r["checks"][0]["cross_view_source"]["source_report"].update(
                sha256="0" * 64
            ),
            lambda r: r["checks"][0]["cross_view_source"].update(check_id="unknown"),
        ):
            target = copy.deepcopy(reports[1])
            mutate(target)
            with self.assertRaises(ValueError):
                validate_report(self.root, target)

    def test_report_missing_environment_rejects_before_toolchain_probe(self):
        from scripts.environment import CheckEnvironment

        profile = self.profile()
        report = verify_profile(self.root, frozen_inputs=profile)
        blocked = CheckEnvironment(
            {}, {"status": "BLOCKED", "missing": ["fixture"], "details": {}}
        )
        with (
            patch(
                "scripts.environment.resolve_check_environment", return_value=blocked
            ),
            patch("scripts.environment.toolchains.toolchain_identity.resolve_toolchain") as toolchain,
        ):
            with self.assertRaises(ValueError):
                validate_report(self.root, report)
        toolchain.assert_not_called()

    def test_final_environment_loss_cannot_publish_source_pass(self):
        from scripts.environment import CheckEnvironment, resolve_check_environment

        profile = self.profile()
        ready = resolve_check_environment(self.root, profile["checks"][0])
        blocked = CheckEnvironment(
            {}, {"status": "BLOCKED", "missing": ["fixture"], "details": {}}
        )
        with patch(
            "scripts.verification.execution.check_plan_runner.resolve_check_environment",
            side_effect=[ready, blocked],
        ):
            reports = verify_profiles(self.root, frozen_profiles=[profile])
        self.assertEqual(reports[0]["result"], "FAIL")
        self.assertEqual(reports[0]["checks"][0]["reason"], "missing-environment")
        self.assertNotIn("publication", reports[0])

    def test_batch_bounds_are_one_to_sixteen_and_no_runner_override(self):
        profile = self.profile()
        for profiles in ([], [profile] * 17):
            with self.assertRaises(ValueError):
                verify_profiles(self.root, frozen_profiles=profiles)
        with self.assertRaises(TypeError):
            verify_profiles(self.root, frozen_profiles=[profile], runner=lambda *_: {})

    def test_failed_check_stops_later_profiles_without_success_publication(self):
        self.check["command"] = ["python3", "-c", "raise SystemExit(1)"]
        self.write()
        profile = self.profile()
        reports, count = self.run_profiles([profile, copy.deepcopy(profile)])
        self.assertEqual(count, 1)
        self.assertEqual(reports[0]["result"], "FAIL")
        self.assertNotIn("publication", reports[0])


if __name__ == "__main__":
    unittest.main()
