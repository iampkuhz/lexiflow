"""覆盖多视图 Verification 单次事务共享与证据拒绝边界。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification import (
    freeze_inputs,
    persist_report,
    read_report,
    verify_profile,
    verify_profiles,
    verify_frozen_inputs,
)
from scripts.verification.reports import validate_report
from scripts.verification.kernel import RELEASE_RUNTIME_CHILD_COMMAND


def _check(check_id: str, scope: str, command=None, *, reuse=True) -> dict:
    return {
        "check_id": check_id,
        "module": "fixture",
        "command": command or ["python3", "-c", "pass"],
        "executable": "python3",
        "cwd": ".",
        "timeout_seconds": 5,
        "scope": scope,
        "triggers": [{"path": "src/"}],
        "module_dependencies": [],
        "required_environment": [],
        "input_paths": ["src"],
        "transaction_reuse": reuse,
        "result_contract": {
            "type": "exit-code",
            "completeness_guarantee": "fixture command owns completion",
        },
    }


def _runner_result(argv, cwd, env, timeout, executable=None):
    return {
        "status": "PASS",
        "exit_code": 0,
        "exit_reason": "exited",
        "stdout": "",
        "stderr": "",
        "duration_seconds": 0.01,
        "started_at": "2026-10-04T00:00:00Z",
        "finished_at": "2026-10-04T00:00:00Z",
        "timed_out": False,
        "executable": sys.executable,
    }


class ProfileTransactionTests(unittest.TestCase):
    def _fixture(self, *, transport: bool = False, reuse: bool = True):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "harness").mkdir()
        (root / "src").mkdir()
        (root / "src/input.txt").write_text("initial")
        (root / ".gitignore").write_text("harness/\ntmp/\n")
        for args in (
            ("init",),
            ("config", "user.email", "fixture@example.invalid"),
            ("config", "user.name", "Fixture"),
        ):
            subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True
        )
        (root / "src/input.txt").write_text("changed")
        (root / "harness/ci-policy.yaml").write_text(
            yaml.safe_dump({"formal_only_check_ids": ["fixture.formal-only"]})
        )
        command = list(RELEASE_RUNTIME_CHILD_COMMAND) if transport else None
        contract = (
            {
                "type": "json-stdout",
                "required_fields": ["status", "reason"],
                "allowed_statuses": ["PASS", "BLOCKED", "FAIL"],
            }
            if transport
            else None
        )
        baseline = _check(
            "fixture.baseline", "repository-baseline", command, reuse=reuse
        )
        change = _check("fixture.on-change", "change-targeted", command, reuse=reuse)
        if transport:
            baseline["check_id"] = "eng.release.lifecycle-runtime"
            change["check_id"] = "eng.release.lifecycle-runtime-on-change"
            baseline["result_contract"] = copy.deepcopy(contract)
            change["result_contract"] = copy.deepcopy(contract)
            baseline["transaction_reuse"] = False
            change["transaction_reuse"] = False
        declaration = {
            "schema_version": "lexiflow.module-checks.v1",
            "checks": [baseline, change],
        }
        (root / "harness/module-checks.yaml").write_text(
            yaml.safe_dump(declaration, sort_keys=False)
        )
        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
        baseline_freeze = freeze_inputs(root, verification_scope="development-baseline")
        change_freeze = freeze_inputs(
            root, verification_scope="development-change", base=base
        )
        self.assertEqual(baseline_freeze["result"], "PASS", baseline_freeze)
        self.assertEqual(change_freeze["result"], "PASS", change_freeze)
        return temp, root, baseline_freeze, change_freeze

    def test_two_views_share_only_in_call_and_reports_round_trip(self):
        temp, root, baseline, change = self._fixture()
        self.addCleanup(temp.cleanup)
        calls = []

        def runner(*args, **kwargs):
            calls.append(args[0])
            return _runner_result(*args)

        reports = verify_profiles(
            root, frozen_profiles=[baseline, change], runner=runner
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual([item["result"] for item in reports], ["PASS", "PASS"])
        cross = next(
            item
            for report in reports
            for item in report["checks"]
            if "cross_view_source" in item
        )
        self.assertEqual(cross["process"]["exit_reason"], "deduplicated")
        self.assertEqual(
            cross["cross_view_source"]["result"]["process"]["exit_reason"], "exited"
        )
        for report in reports:
            persist_report(root, report)
            loaded, _ = read_report(root, report["run_id"])
            self.assertEqual(loaded, report)

        # Separate public calls have no persisted or ambient cache.
        first = verify_profile(root, frozen_inputs=baseline, runner=runner)
        second = verify_profile(root, frozen_inputs=baseline, runner=runner)
        self.assertEqual(first["result"], "PASS")
        self.assertEqual(second["result"], "PASS")
        self.assertEqual(len(calls), 3)

    def test_write_separated_before_after_fixture_measurement(self):
        temp, root, baseline, change = self._fixture()
        self.addCleanup(temp.cleanup)
        before_calls = []

        def before_runner(*args, **kwargs):
            before_calls.append(args[0])
            time.sleep(0.01)
            return _runner_result(*args)

        before_started = time.perf_counter()
        before_reports = [
            verify_profile(root, frozen_inputs=profile, runner=before_runner)
            for profile in (baseline, change)
        ]
        before_wall = time.perf_counter() - before_started
        after_calls = []

        def after_runner(*args, **kwargs):
            after_calls.append(args[0])
            time.sleep(0.01)
            return _runner_result(*args)

        after_started = time.perf_counter()
        after_reports = verify_profiles(
            root, frozen_profiles=[baseline, change], runner=after_runner
        )
        after_wall = time.perf_counter() - after_started
        self.assertTrue(
            all(
                report["result"] == "PASS"
                for report in [*before_reports, *after_reports]
            )
        )
        self.assertEqual((len(before_calls), len(after_calls)), (2, 1))
        evidence = {
            "schema_version": "lexiflow.verification-transaction-measurement.v1",
            "fixture": "synthetic isolated two-profile verification; runner sleeps 10ms per executed check",
            "before_separate_profile_calls": {
                "runner_invocations": len(before_calls),
                "wall_seconds": round(before_wall, 6),
            },
            "after_single_transaction": {
                "runner_invocations": len(after_calls),
                "wall_seconds": round(after_wall, 6),
            },
            "preexisting_single_view_alias_savings_in_fixture": 0,
            "reports": {
                "before": [x["result"] for x in before_reports],
                "after": [x["result"] for x in after_reports],
            },
        }
        output = (
            Path(__file__).resolve().parents[2]
            / "tmp/quality/agent-quality-workflow/transaction"
        )
        output.mkdir(parents=True, exist_ok=True)
        (output / "before-after.json").write_text(
            json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )

    def test_cross_view_witness_and_source_log_are_mandatory(self):
        temp, root, baseline, change = self._fixture()
        self.addCleanup(temp.cleanup)
        reports = verify_profiles(
            root, frozen_profiles=[baseline, change], runner=_runner_result
        )
        target = next(
            report for report in reports if report["scope"] == "development-change"
        )
        cross = next(item for item in target["checks"] if "cross_view_source" in item)
        altered = copy.deepcopy(target)
        altered_cross = next(
            item for item in altered["checks"] if "cross_view_source" in item
        )
        del altered_cross["cross_view_source"]
        with self.assertRaises(ValueError):
            validate_report(root, altered)
        altered = copy.deepcopy(target)
        altered_cross = next(
            item for item in altered["checks"] if "cross_view_source" in item
        )
        altered_cross["cross_view_source"]["result"]["process"]["output_artifacts"][
            "stdout"
        ]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_report(root, altered)
        altered = copy.deepcopy(target)
        altered_cross = next(
            item for item in altered["checks"] if "cross_view_source" in item
        )
        altered_cross["cross_view_source"]["toolchain"]["executor"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_report(root, altered)
        # The original remains valid; mutations above were detached copies.
        self.assertEqual(validate_report(root, target), target)
        self.assertEqual(cross["cross_view_source"]["scope"], "development-baseline")

    def test_missing_transaction_reuse_qualification_never_cross_deduplicates(self):
        temp, root, baseline, change = self._fixture(reuse=False)
        self.addCleanup(temp.cleanup)
        calls = []

        def runner(*args, **kwargs):
            calls.append(args[0])
            return _runner_result(*args)

        reports = verify_profiles(
            root, frozen_profiles=[baseline, change], runner=runner
        )
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(report["result"] == "PASS" for report in reports))
        self.assertFalse(
            any(
                "cross_view_source" in check
                for report in reports
                for check in report["checks"]
            )
        )

    def test_input_directory_drift_prevents_reuse(self):
        for mutation in ("content", "new-file", "configuration"):
            with self.subTest(mutation=mutation):
                temp, root, baseline, change = self._fixture()
                try:
                    calls = []

                    def mutating_runner(*args, **kwargs):
                        calls.append(args[0])
                        result = _runner_result(*args)
                        if mutation == "content":
                            (root / "src/input.txt").write_text("drift")
                        elif mutation == "new-file":
                            (root / "src/new.txt").write_text("new")
                        else:
                            path = root / "harness/module-checks.yaml"
                            declaration = yaml.safe_load(path.read_text())
                            declaration["checks"][0]["timeout_seconds"] = 6
                            path.write_text(
                                yaml.safe_dump(declaration, sort_keys=False)
                            )
                        return result

                    reports = verify_profiles(
                        root, frozen_profiles=[baseline, change], runner=mutating_runner
                    )
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(
                        any(report["result"] == "FAIL" for report in reports)
                    )
                    self.assertFalse(
                        any(
                            "cross_view_source" in check
                            for report in reports
                            for check in report["checks"]
                        )
                    )
                finally:
                    temp.cleanup()

    def test_distinct_config_and_input_closures_execute_instead_of_reusing(self):
        temp, root, _baseline, _change = self._fixture()
        self.addCleanup(temp.cleanup)
        (root / "src/other-input.txt").write_text("separate closure")
        declaration_path = root / "harness/module-checks.yaml"
        declaration = yaml.safe_load(declaration_path.read_text())
        changed = next(
            check
            for check in declaration["checks"]
            if check["check_id"] == "fixture.on-change"
        )
        changed["command"] = ["python3", "-c", "pass; # changed config"]
        changed["input_paths"].append("src/other-input.txt")
        declaration_path.write_text(yaml.safe_dump(declaration, sort_keys=False))
        baseline = freeze_inputs(root, verification_scope="development-baseline")
        change = freeze_inputs(
            root, verification_scope="development-change", base="HEAD"
        )
        calls = []
        reports = verify_profiles(
            root,
            frozen_profiles=[baseline, change],
            runner=lambda *args, **kwargs: (
                calls.append(args[0]) or _runner_result(*args)
            ),
        )
        changed_check = next(
            check
            for check in reports[1]["checks"]
            if check["check_id"] == "fixture.on-change"
        )
        self.assertEqual(changed_check["process"]["exit_reason"], "exited")
        self.assertNotIn("cross_view_source", changed_check)
        self.assertEqual(reports[1]["result"], "PASS")
        self.assertGreaterEqual(len(calls), 2)

    def test_environment_configuration_drift_executes_without_reuse(self):
        temp, root, baseline, change = self._fixture()
        self.addCleanup(temp.cleanup)
        state = {"mode": "one"}
        calls = []

        def runner(*args, **kwargs):
            calls.append(args[0])
            return _runner_result(*args)

        with patch(
            "scripts.verification.scenarios.execution_environment",
            side_effect=lambda *_: {"FIXTURE_MODE": state["mode"]},
        ):
            reports = verify_profiles(
                root,
                frozen_profiles=[baseline, change],
                runner=runner,
                before_profile=lambda index, _: (
                    state.update(mode="two") or True if index else True
                ),
            )
        self.assertEqual(len(calls), sum(len(report["checks"]) for report in reports))
        self.assertEqual([report["result"] for report in reports], ["PASS", "PASS"])
        self.assertFalse(
            any(
                "cross_view_source" in check
                for report in reports
                for check in report["checks"]
            )
        )

    def test_path_tool_bytes_changed_between_views_cannot_reuse(self):
        temp, root, _baseline, _change = self._fixture()
        self.addCleanup(temp.cleanup)
        declaration_path = root / "harness/module-checks.yaml"
        declaration = yaml.safe_load(declaration_path.read_text())
        for check in declaration["checks"]:
            check["command"] = ["node", "-e", "pass"]
            check["executable"] = "node"
            check["required_environment"] = ["node"]
        declaration_path.write_text(yaml.safe_dump(declaration, sort_keys=False))
        baseline = freeze_inputs(root, verification_scope="development-baseline")
        change = freeze_inputs(
            root, verification_scope="development-change", base="HEAD"
        )
        with tempfile.TemporaryDirectory() as tools:
            binary = Path(tools) / "node"
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            calls = []

            def runner(*args, **kwargs):
                calls.append(args[0])
                return _runner_result(*args)

            def mutate(index, _profile, _report):
                if index == 0:
                    binary.write_text("#!/bin/sh\nexit 1\n")
                return True

            with patch.dict(os.environ, {"PATH": f"{tools}:{os.environ['PATH']}"}):
                reports = verify_profiles(
                    root,
                    frozen_profiles=[baseline, change],
                    runner=runner,
                    after_profile=mutate,
                )
                self.assertEqual(len(calls), 2)
                self.assertTrue(all(x["result"] == "PASS" for x in reports))
                self.assertFalse(
                    any(
                        "cross_view_source" in check
                        for report in reports
                        for check in report["checks"]
                    )
                )
                with self.assertRaises(ValueError):
                    validate_report(root, reports[0])
                self.assertEqual(validate_report(root, reports[1]), reports[1])

    def test_release_runtime_transport_executes_each_view(self):
        temp, root, baseline, change = self._fixture(transport=True)
        self.addCleanup(temp.cleanup)
        calls = []

        def runner(argv, cwd, env, timeout, executable=None, *, stdin_payload=None):
            envelope = json.loads(stdin_payload)
            calls.append(envelope["check_id"])
            payload = {
                "status": "PASS",
                "reason": "",
                "run_id": envelope["run_id"],
                "check_id": envelope["check_id"],
                "verify_input_fingerprint": envelope["snapshot"]["fingerprint"],
            }
            return {
                "status": "PASS",
                "exit_code": 0,
                "exit_reason": "exited",
                "stdout": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "stderr": "",
                "duration_seconds": 0.01,
                "started_at": "2026-10-04T00:00:00Z",
                "finished_at": "2026-10-04T00:00:00Z",
                "timed_out": False,
                "executable": sys.executable,
            }

        reports = verify_profiles(
            root, frozen_profiles=[baseline, change], runner=runner
        )
        self.assertEqual(
            calls,
            [
                "eng.release.lifecycle-runtime",
                "eng.release.lifecycle-runtime-on-change",
            ],
        )
        self.assertTrue(all(report["result"] == "PASS" for report in reports))
        self.assertFalse(
            any(
                "cross_view_source" in check
                for report in reports
                for check in report["checks"]
            )
        )

    def test_delivery_fail_fast_leaves_later_check_not_run(self):
        temp, root, baseline, change = self._fixture()
        self.addCleanup(temp.cleanup)
        # Add a second baseline check with a distinct operation before making freezes.
        declaration_path = root / "harness/module-checks.yaml"
        declaration = yaml.safe_load(declaration_path.read_text())
        extra = _check("fixture.expensive", "repository-baseline")
        extra["command"] = ["python3", "-c", "print('expensive')"]
        declaration["checks"].append(extra)
        declaration_path.write_text(yaml.safe_dump(declaration, sort_keys=False))
        baseline = freeze_inputs(root, verification_scope="development-baseline")
        calls = []

        def fail_runner(*args, **kwargs):
            calls.append(args[0])
            result = _runner_result(*args)
            result["status"] = "FAIL"
            result["exit_code"] = 1
            return result

        report = verify_profile(root, frozen_inputs=baseline, runner=fail_runner)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["result"], "FAIL")
        expensive = next(
            item for item in report["checks"] if item["check_id"] == "fixture.expensive"
        )
        self.assertEqual(expensive["process"]["exit_reason"], "not-run")

    def test_delivery_fail_fast_does_not_start_later_profile(self):
        temp, root, baseline, change = self._fixture()
        self.addCleanup(temp.cleanup)
        calls = []

        def fail_runner(*args, **kwargs):
            calls.append(args[0])
            result = _runner_result(*args)
            result["status"] = "FAIL"
            result["exit_code"] = 1
            return result

        reports = verify_profiles(
            root, frozen_profiles=[baseline, change], runner=fail_runner
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["result"], "FAIL")
        self.assertEqual(reports[0]["scope"], "development-baseline")

    def test_same_scope_profiles_keep_per_run_source_witness(self):
        temp, root, _baseline, change = self._fixture()
        self.addCleanup(temp.cleanup)
        calls = []
        reports = verify_profiles(
            root,
            frozen_profiles=[change, change],
            runner=lambda *a, **k: calls.append(a[0]) or _runner_result(*a),
        )
        self.assertEqual(len(reports), 2)
        self.assertNotEqual(reports[0]["run_id"], reports[1]["run_id"])
        self.assertEqual(len(calls), len(reports[0]["checks"]))
        reused = next(
            check
            for check in reports[1]["checks"]
            if check["process"]["exit_reason"] == "deduplicated"
        )
        self.assertEqual(reused["cross_view_source"]["scope"], reports[1]["scope"])
        self.assertEqual(reused["cross_view_source"]["run_id"], reports[0]["run_id"])
        self.assertTrue(validate_report(root, reports[1]))
        tampered = copy.deepcopy(reports[1])
        next(check for check in tampered["checks"] if "cross_view_source" in check)[
            "cross_view_source"
        ]["run_id"] = reports[1]["run_id"]
        with self.assertRaises(ValueError):
            validate_report(root, tampered)

    def test_pre_profile_rejection_preserves_repository_scope_without_running(self):
        temp, root, _baseline, _change = self._fixture()
        self.addCleanup(temp.cleanup)
        repository = freeze_inputs(root, verification_scope="repository-baseline")
        calls = []
        reports = verify_profiles(
            root,
            frozen_profiles=[repository],
            runner=lambda *args, **kwargs: (
                calls.append(args[0]) or _runner_result(*args)
            ),
            before_profile=lambda _index, _profile: False,
        )
        self.assertEqual(calls, [])
        self.assertEqual(reports[0]["scope"], "repository-baseline")
        self.assertEqual(reports[0]["reason"], "profile-preflight-changed")

    def test_second_profile_subject_drift_stops_before_its_command(self):
        temp, root, _baseline, _change = self._fixture()
        self.addCleanup(temp.cleanup)
        (root / "src/later.txt").write_text("before")
        declarations_path = root / "harness/module-checks.yaml"
        declarations = yaml.safe_load(declarations_path.read_text())
        later = _check("fixture.change-later", "change-targeted")
        later["input_paths"] = ["src/later.txt"]
        declarations["checks"].append(later)
        declarations_path.write_text(yaml.safe_dump(declarations, sort_keys=False))
        repository = freeze_inputs(root, verification_scope="repository-baseline")
        changed = freeze_inputs(
            root, verification_scope="development-change", base="HEAD"
        )
        calls = []

        def runner(argv, *args, **kwargs):
            calls.append(argv[0])
            return _runner_result(argv, *args)

        def after_profile(index, _profile, report):
            if index == 0:
                (root / "src/later.txt").write_text("drifted")
            return True

        reports = verify_profiles(
            root,
            frozen_profiles=[repository, changed],
            runner=runner,
            before_profile=lambda _index, profile: verify_frozen_inputs(root, profile),
            after_profile=after_profile,
        )
        self.assertEqual(reports[0]["result"], "PASS")
        self.assertEqual(reports[1]["result"], "FAIL")
        self.assertEqual(reports[1]["reason"], "profile-preflight-changed")
        self.assertEqual(
            len(calls),
            sum(
                check["process"]["exit_reason"] != "not-run"
                for check in reports[0]["checks"]
            ),
        )


if __name__ == "__main__":
    unittest.main()
