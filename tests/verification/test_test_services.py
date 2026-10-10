"""临时测试服务的隔离、所有权、清理和默认 Hook 集成回归；不启动真实容器。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.environment.services import isolated_postgres_redis as services

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = set(services.SERVICES)


class FakePodman:
    def __init__(self):
        self.calls = []
        self.resources = {}
        self.foreign_label = False
        self.fail_remove = False
        self.fail_create = False
        self.not_ready = False
        self.public_port = False

    def __call__(self, args, timeout=10, *, check=True):
        self.calls.append((args, timeout))
        code, output, error = 0, "", ""
        if args[0] == "info":
            output = "fixture-host"
        elif args[:3] == ["system", "connection", "list"]:
            output = json.dumps(
                [
                    {
                        "Name": "fixture",
                        "URI": "ssh://user@127.0.0.1:1234/podman.sock",
                        "Default": True,
                    }
                ]
            )
        elif args[:2] == ["image", "inspect"]:
            output = ("a" if "postgres" in args[-1] else "b") * 64
        elif args[0] == "create":
            name = args[args.index("--name") + 1]
            lease = args[args.index("--label") + 1].split("=", 1)[1]
            ident = ("1" if "postgres" in name else "2") * 64
            self.resources[name] = {
                "Id": ident,
                "Config": {
                    "Labels": {
                        services.OWNER_LABEL: "foreign" if self.foreign_label else lease
                    }
                },
            }
            output = ident
            if self.fail_create:
                raise services.TestServicesError("create response interrupted")
        elif args[0] == "start":
            output = args[-1]
        elif args[0] == "port":
            output = ("0.0.0.0" if self.public_port else "127.0.0.1") + (
                ":55432" if args[-1] == "5432" else ":56379"
            )
        elif args[0] == "exec":
            code = 1 if self.not_ready else 0
            output = "PONG" if "redis-cli" in args else "accepting connections"
        elif args[:2] == ["container", "exists"]:
            code = (
                0
                if any(
                    args[-1] in (name, item["Id"])
                    for name, item in self.resources.items()
                )
                else 1
            )
        elif args[:2] == ["container", "inspect"]:
            output = json.dumps([self.resources[args[-1]]])
        elif args[0] == "rm":
            if self.fail_remove:
                raise services.TestServicesError("fixture remove unavailable")
            self.resources = {
                name: item
                for name, item in self.resources.items()
                if item["Id"] != args[-1]
            }
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(["podman", *args], code, output, error)


class TestManagedTestServices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fake = FakePodman()
        patch = mock.patch.object(services, "_command", self.fake)
        patch.start()
        self.addCleanup(patch.stop)
        self.messages = []

    def context(self, required=REQUIRED):
        return services.isolated_test_services(
            self.root, required, self.messages.append
        )

    def receipt(self):
        paths = list((self.root / "tmp/quality/test-services").glob("*/cleanup.json"))
        self.assertEqual(1, len(paths))
        return json.loads(paths[0].read_text())

    def test_postgres_uses_only_bounded_limits_and_records_actual_capacity(self):
        with self.context({"postgres-test-jdbc-url"}):
            create = next(args for args, _ in self.fake.calls if args[0] == "create")
            self.assertEqual("512m", create[create.index("--memory") + 1])
            self.assertIn("/var/lib/postgresql/data:rw,size=256m", create)
        resource = self.receipt()["resources"][0]
        self.assertEqual(
            resource["postgres_limits"], {"memory_mib": 512, "data_tmpfs_mib": 256}
        )
        self.assertNotIn("postgres_profile", resource)
        self.assertTrue(self.receipt()["cleanup_verified"])

    def test_unneeded_services_never_touch_podman_or_create_receipts(self):
        with self.context(set()) as env:
            self.assertEqual({}, env)
        self.assertFalse(self.fake.calls)
        self.assertFalse((self.root / "tmp").exists())

    def test_managed_endpoints_override_inherited_values_and_are_restored(self):
        inherited = {key: "private-canary" for key in services.ENV_KEYS.values()}
        with mock.patch.dict(os.environ, inherited):
            with self.context() as env:
                self.assertEqual("127.0.0.1:56379", env["LEXIFLOW_REDIS_TEST_ENDPOINT"])
                self.assertIn(
                    "127.0.0.1:55432/lexiflow_hook_test",
                    env["LEXIFLOW_POSTGRES_TEST_JDBC_URL"],
                )
                self.assertEqual(
                    env["LEXIFLOW_REDIS_TEST_ENDPOINT"],
                    os.environ["LEXIFLOW_REDIS_TEST_ENDPOINT"],
                )
            for key in inherited:
                self.assertEqual("private-canary", os.environ[key])
        receipt = self.receipt()
        self.assertTrue(receipt["cleanup_verified"])
        self.assertFalse(self.fake.resources)
        self.assertNotIn("private-canary", json.dumps(receipt) + str(self.messages))

    def test_commands_forbid_pull_volumes_fixed_ports_and_development_services(self):
        with self.context():
            pass
        creates = [args for args, _ in self.fake.calls if args[0] == "create"]
        self.assertEqual(2, len(creates))
        for args in creates:
            self.assertIn("--pull=never", args)
            self.assertIn("--image-volume=ignore", args)
            self.assertIn("--restart=no", args)
            self.assertIn("--cidfile", args)
            self.assertIn("--memory", args)
            self.assertTrue(args[args.index("--publish") + 1].startswith("127.0.0.1::"))
            self.assertNotIn("-v", args)
            self.assertNotIn("--volume", args)
            self.assertNotIn("compose", args)
        self.assertTrue(all(timeout <= 10 for _, timeout in self.fake.calls))

    def test_only_required_service_is_created(self):
        with self.context({"redis-test-endpoint"}):
            pass
        self.assertEqual(["redis"], [r["service"] for r in self.receipt()["resources"]])

    def test_check_failure_restores_environment_and_cleans(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "test failed"):
                with self.context():
                    raise RuntimeError("test failed")
            self.assertNotIn("LEXIFLOW_REDIS_TEST_ENDPOINT", os.environ)
        self.assertTrue(self.receipt()["cleanup_verified"])

    def test_interruption_during_create_still_cleans_label_owned_container(self):
        self.fake.fail_create = True
        with self.assertRaisesRegex(services.TestServicesError, "interrupted"):
            with self.context():
                self.fail("must not yield")
        self.assertFalse(self.fake.resources)
        self.assertTrue(self.receipt()["cleanup_verified"])

    def test_foreign_label_is_never_deleted_and_cannot_pass(self):
        self.fake.foreign_label = True
        with self.assertRaisesRegex(services.TestServicesError, "标签不匹配"):
            with self.context():
                pass
        self.assertFalse(any(args[0] == "rm" for args, _ in self.fake.calls))
        self.assertFalse(self.receipt()["cleanup_verified"])

    def test_cleanup_error_cannot_turn_into_pass(self):
        self.fake.fail_remove = True
        with self.assertRaisesRegex(services.TestServicesError, "未确认清理"):
            with self.context():
                pass
        self.assertFalse(self.receipt()["cleanup_verified"])
        self.assertEqual(2, len(self.receipt()["cleanup"]))

    def test_remote_engine_is_rejected_before_any_connection_or_create(self):
        with mock.patch.dict(
            os.environ, {"CONTAINER_HOST": "ssh://user@remote.invalid/podman.sock"}
        ):
            with self.assertRaisesRegex(services.TestServicesError, "拒绝在远端"):
                with self.context():
                    pass
        self.assertFalse(self.fake.calls)

    def test_missing_image_does_not_create_or_pull(self):
        def call(args, *rest, **kwargs):
            if args[:2] == ["image", "inspect"]:
                return subprocess.CompletedProcess(args, 1, "", "image not found")
            return self.fake(args, *rest, **kwargs)

        with mock.patch.object(services, "_command", side_effect=call):
            with self.assertRaisesRegex(services.TestServicesError, "不自动 pull"):
                with self.context():
                    pass
        self.assertFalse(any(args[0] == "create" for args, _ in self.fake.calls))

    def test_all_fixed_images_are_inspected_before_any_resource_is_created(self):
        with self.context():
            pass
        image_calls = [
            (index, args[-1])
            for index, (args, _) in enumerate(self.fake.calls)
            if args[:2] == ["image", "inspect"]
        ]
        self.assertEqual(
            ["docker.io/library/postgres:17", "docker.io/library/redis:7"],
            [tag for _, tag in image_calls],
        )
        first_create = next(
            index
            for index, (args, _) in enumerate(self.fake.calls)
            if args[0] == "create"
        )
        self.assertLess(max(index for index, _ in image_calls), first_create)

    def test_missing_second_image_still_creates_no_resource(self):
        def command(args, timeout=10, *, check=True):
            if args[:2] == ["image", "inspect"] and args[-1].endswith("redis:7"):
                return subprocess.CompletedProcess(args, 1, "", "missing redis")
            return self.fake(args, timeout, check=check)

        with mock.patch.object(services, "_command", side_effect=command):
            with self.assertRaisesRegex(services.TestServicesError, "不自动 pull"):
                with self.context():
                    self.fail("must not yield without both local images")
        self.assertFalse(any(args[0] == "create" for args, _ in self.fake.calls))
        self.assertFalse((self.root / "tmp").exists())

    def test_readiness_deadline_cleans_all_created_resources(self):
        self.fake.not_ready = True
        clock = [0]

        def sleep(_):
            clock[0] += 1

        with (
            mock.patch.object(services.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(services.time, "sleep", side_effect=sleep),
        ):
            with self.assertRaisesRegex(services.TestServicesError, "30 秒内未就绪"):
                with self.context():
                    pass
        self.assertFalse(self.fake.resources)

    def test_readiness_probe_and_sleep_share_the_thirty_second_deadline(self):
        clock = [0.0]
        probe_timeouts = []
        sleep_durations = []

        def command(args, timeout=10, *, check=True):
            if args[0] == "exec":
                probe_timeouts.append(timeout)
                clock[0] += 29.5 if len(probe_timeouts) == 1 else timeout
                return subprocess.CompletedProcess(args, 1, "", "not ready")
            return self.fake(args, timeout, check=check)

        def sleep(duration):
            sleep_durations.append(duration)
            clock[0] += duration

        with (
            mock.patch.object(services, "_command", side_effect=command),
            mock.patch.object(services.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(services.time, "sleep", side_effect=sleep),
        ):
            with self.assertRaisesRegex(services.TestServicesError, "30 秒内未就绪"):
                with self.context({"postgres-test-jdbc-url"}):
                    self.fail("deadline must not yield")
        self.assertEqual(30.0, clock[0])
        self.assertEqual(3, probe_timeouts[0])
        self.assertAlmostEqual(0.3, probe_timeouts[1])
        self.assertTrue(all(0 <= duration <= 0.2 for duration in sleep_durations))
        self.assertFalse(self.fake.resources)
        self.assertTrue(self.receipt()["cleanup_verified"])

    def test_non_loopback_mapping_is_rejected_and_cleaned(self):
        self.fake.public_port = True
        with self.assertRaisesRegex(services.TestServicesError, "loopback"):
            with self.context():
                pass
        self.assertFalse(self.fake.resources)

    def test_hook_runs_daily_profile_without_exported_test_addresses(self):
        from scripts.triggers import run_stop_checks as delivery_hook
        from tests.verification.test_fixture_e2e import _init_git_repo

        _init_git_repo(self.root)
        (self.root / ".gitignore").write_text("config/\nscripts/\ntmp/\nfixture/\n")
        (self.root / "fixture").mkdir()
        (self.root / "fixture/check.py").write_text(
            "import os\nassert os.environ['LEXIFLOW_REDIS_TEST_ENDPOINT']=='127.0.0.1:56379'\n"
            'print(\'{"status":"PASS","checks_run":1,"failures":0,"errors":0,"skipped":0,"reason":""}\')\n'
        )
        (self.root / "scripts/verification/planning").mkdir(parents=True)
        (self.root / "scripts/verification/planning/change_risk_assessment.py").write_bytes(
            (ROOT / "scripts/verification/planning/change_risk_assessment.py").read_bytes()
        )
        (self.root / "config/engineering").mkdir(parents=True)
        (self.root / "config/engineering/risk.yaml").write_text(
            "high_risk_paths: [config/**]\nlocal_owner_roots: {lexicon: backend/product/lexicon/}\n"
        )
        (self.root / "config/engineering/module-checks.yaml").write_text(
            json.dumps(
                {
                    "checks": [
                        {
                            "check_id": "fixture.services",
                            "command": ["python3", "fixture/check.py"],
                            "timeout_seconds": 5,
                            "input_paths": ["fixture/check.py"],
                            "required_environment": sorted(REQUIRED),
                            "scenarios": ["change-targeted", "repository-baseline"],
                            "triggers": [{"path": ".gitignore"}],
                            "module_dependencies": [],
                        },
                        {
                            "check_id": "fixture.formal",
                            "command": [
                                "python3",
                                "-c",
                                "raise SystemExit('formal must not execute')",
                            ],
                            "timeout_seconds": 5,
                            "input_paths": ["fixture/check.py"],
                            "required_environment": [],
                            "scenarios": ["change-targeted", "repository-baseline"],
                            "triggers": [{"path": "formal-input"}],
                            "module_dependencies": [],
                        },
                    ],
                    "scenario_policy": {
                        "ci_quick": {
                            "always_required_check_ids": [],
                            "formal_only_check_ids": ["fixture.formal"],
                        }
                    },
                }
            )
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in services.ENV_KEYS.values()
            and k != "LEXIFLOW_DELIVERY_HOOK_DISABLE"
        }
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            output = delivery_hook.evaluate(self.root, {"hook_event_name": "Stop"})
        self.assertIn("交付 PASS", output["systemMessage"])
        self.assertNotIn("decision", output)
        reports = list((self.root / "tmp/quality/verification-reports").glob("*.json"))
        self.assertEqual(1, len(reports))
        self.assertEqual(
            "development-change", json.loads(reports[0].read_text())["scope"]
        )
        self.assertTrue(self.receipt()["cleanup_verified"])

    def test_progress_distinguishes_gradle_pass_from_unexecuted_blocked(self):
        from scripts.triggers import run_stop_checks as delivery_hook

        for status, reason, environment in (
            ("PASS", "", {}),
            ("BLOCKED", "missing-environment", {"missing": ["postgres-test-jdbc-url"]}),
        ):
            stream = io.StringIO()
            report = {
                "checks": [
                    {
                        "check_id": "eng.backend.delivery",
                        "status": status,
                        "reason": reason,
                        "environment": environment,
                    }
                ]
            }
            with contextlib.redirect_stderr(stream):
                delivery_hook.report_progress(report)
            self.assertIn(
                f"Java/Gradle deliveryFull [eng.backend.delivery] {status}",
                stream.getvalue(),
            )
            if status == "BLOCKED":
                self.assertIn("未执行，缺少 postgres-test-jdbc-url", stream.getvalue())


class TestPodmanClientTimeout(unittest.TestCase):
    def test_timeout_reaps_owned_process(self):
        original = subprocess.Popen
        processes = []

        def popen(_argv, **kwargs):
            process = original(
                [sys.executable, "-c", "import time;time.sleep(60)"], **kwargs
            )
            processes.append(process)
            return process

        with mock.patch.object(services.subprocess, "Popen", side_effect=popen):
            with self.assertRaisesRegex(services.TestServicesError, "超过"):
                services._command(["info"], timeout=0.05)
        self.assertIsNotNone(processes[0].poll())


if __name__ == "__main__":
    unittest.main()
