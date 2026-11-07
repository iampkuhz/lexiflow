"""Redis lease 的隔离、所有权、清理及客户端超时回归；不启动真实容器。"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.environment.services import isolated_redis as services
from scripts.verification import freeze_inputs, persist_report, read_report
from scripts.verification.reports.verification_report_store import validate_report
from tests.verification.declaration_fixtures import write_declarations


class FakePodman:
    def __init__(self, port=56379):
        self.calls = []
        self.resources = {}
        self.foreign_label = False
        self.fail_remove = False
        self.not_ready = False
        self.public_port = False
        self.port = port

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
            output = "b" * 64
        elif args[0] == "create":
            name = args[args.index("--name") + 1]
            lease = args[args.index("--label") + 1].split("=", 1)[1]
            ident = "2" * 64
            self.resources[name] = {
                "Id": ident,
                "Config": {
                    "Labels": {
                        services.OWNER_LABEL: "foreign" if self.foreign_label else lease
                    }
                },
            }
            output = ident
        elif args[0] == "start":
            output = args[-1]
        elif args[0] == "port":
            output = ("0.0.0.0" if self.public_port else "127.0.0.1") + f":{self.port}"
        elif args[0] == "exec":
            code, output = (1, "") if self.not_ready else (0, "PONG")
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

    def context(self, required={services.SERVICE}):
        return services.isolated_test_services(self.root, set(required), lambda _: None)

    def receipt(self):
        paths = list((self.root / "tmp/quality/test-services").glob("*/cleanup.json"))
        self.assertEqual(1, len(paths))
        return json.loads(paths[0].read_text())

    def test_redis_lease_is_random_loopback_no_volume_and_cleans(self):
        binding = None
        with self.context() as env:
            self.assertEqual("127.0.0.1:56379", env[services.ENV_KEY])
            binding = services.active_binding(self.root, env[services.ENV_KEY])
            self.assertIsNotNone(binding)
            self.assertEqual(
                services._digest(env[services.ENV_KEY]), binding["endpoint_sha256"]
            )
            self.assertTrue(services.verify_binding_record(self.root, binding))
            self.assertFalse(services.verify_binding_receipts(self.root, binding))
            args = next(args for args, _ in self.fake.calls if args[0] == "create")
            self.assertIn("--pull=never", args)
            self.assertIn("--image-volume=ignore", args)
            self.assertTrue(args[args.index("--publish") + 1].startswith("127.0.0.1::"))
            self.assertNotIn("--volume", args)
        self.assertTrue(self.receipt()["cleanup_verified"])
        self.assertFalse(self.fake.resources)
        self.assertTrue(services.verify_binding_receipts(self.root, binding))
        self.assertIsNone(services.active_binding(self.root, "127.0.0.1:56379"))
        binding_file = self.root / binding["directory"] / "binding.json"
        self.assertNotIn("127.0.0.1:56379", binding_file.read_text())

    def test_cleanup_receipt_tampering_is_rejected(self):
        with self.context() as env:
            binding = services.active_binding(self.root, env[services.ENV_KEY])
        receipt = self.root / binding["directory"] / "cleanup-receipt.json"
        os.chmod(receipt, 0o600)
        payload = json.loads(receipt.read_text())
        payload["removed"] = False
        receipt.write_text(json.dumps(payload))
        self.assertFalse(services.verify_binding_receipts(self.root, binding))

    def test_symlinked_receipt_is_rejected(self):
        with self.context() as env:
            binding = services.active_binding(self.root, env[services.ENV_KEY])
        receipt = self.root / binding["directory"] / "cleanup-receipt.json"
        original = receipt.read_text()
        receipt.unlink()
        target = self.root / "other.json"
        target.write_text(original)
        receipt.symlink_to(target)
        self.assertFalse(services.verify_binding_receipts(self.root, binding))

    def test_receipt_reader_rejects_fifo_parent_symlink_and_path_escape(self):
        with self.context() as env:
            binding = services.active_binding(self.root, env[services.ENV_KEY])
        escaped = dict(binding, directory="tmp/quality/test-services/../outside")
        self.assertFalse(services.verify_binding_record(self.root, escaped))
        bind_path = self.root / binding["directory"] / "binding.json"
        bind_path.unlink()
        os.mkfifo(bind_path)
        self.assertFalse(services.verify_binding_record(self.root, binding))

    def test_receipt_reader_rejects_writable_and_hardlinked_binding(self):
        with self.context() as env:
            binding = services.active_binding(self.root, env[services.ENV_KEY])
        bind_path = self.root / binding["directory"] / "binding.json"
        os.chmod(bind_path, 0o600)
        self.assertFalse(services.verify_binding_record(self.root, binding))
        os.chmod(bind_path, 0o400)
        os.link(bind_path, bind_path.with_name("binding-copy.json"))
        self.assertFalse(services.verify_binding_record(self.root, binding))

    def test_receipt_reader_rejects_symlinked_parent(self):
        with self.context() as env:
            binding = services.active_binding(self.root, env[services.ENV_KEY])
        quality = self.root / "tmp/quality"
        moved = self.root / "tmp/quality-moved"
        quality.rename(moved)
        quality.symlink_to(moved, target_is_directory=True)
        self.assertFalse(services.verify_binding_record(self.root, binding))

    def test_report_reads_after_cleanup_and_with_a_distinct_owned_lease_only(self):
        (self.root / "harness").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src/input.txt").write_text("fixture")
        check = {
            "check_id": "fixture.redis-check",
            "command": [
                "python3",
                "-c",
                "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
            ],
            "executable": "python3",
            "cwd": ".",
            "timeout_seconds": 5,
            "scope": "repository-baseline",
            "triggers": [{"path": "src/"}],
            "module_dependencies": [],
            "required_environment": [services.SERVICE],
            "input_paths": ["src"],
            "result_contract": {
                "type": "json-stdout",
                "required_fields": [
                    "status",
                    "checks_run",
                    "failures",
                    "errors",
                    "skipped",
                    "reason",
                ],
                "allowed_statuses": ["PASS"],
            },
        }
        write_declarations(self.root / "config/engineering/module-checks.yaml", [check])
        freeze = freeze_inputs(self.root)
        with (
            mock.patch.dict(os.environ, {services.ENV_KEY: ""}),
            mock.patch.object(services, "_local_engine", lambda: None),
        ):
            with self.context() as env:
                report = __import__(
                    "scripts.verification", fromlist=["verify_profile"]
                ).verify_profile(self.root, frozen_inputs=freeze)
                self.assertEqual("PASS", report["result"], report)
                self.assertIn("environment_binding", report["checks"][0])
                forged = copy.deepcopy(report)
                forged["checks"][0]["environment_components"][services.ENV_KEY] = (
                    "0" * 64
                )
                with self.assertRaisesRegex(ValueError, "check invalid"):
                    persist_report(self.root, forged)
                forged = copy.deepcopy(report)
                forged["checks"][0]["environment_binding"]["resource_id"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "check invalid"):
                    persist_report(self.root, forged)
                persist_report(self.root, report)
                first_endpoint = env[services.ENV_KEY]
            self.assertEqual(report, read_report(self.root, report["run_id"])[0])
            with self.assertRaisesRegex(ValueError, "check invalid"):
                validate_report(self.root, report, publication=True)
            with (
                mock.patch.dict(os.environ, {services.ENV_KEY: "127.0.0.1:59999"}),
                self.assertRaisesRegex(ValueError, "check invalid"),
            ):
                read_report(self.root, report["run_id"])
            second = FakePodman(port=56380)
            with (
                mock.patch.object(services, "_command", second),
                self.context() as second_env,
            ):
                self.assertNotEqual(first_endpoint, second_env[services.ENV_KEY])
                self.assertEqual(report, read_report(self.root, report["run_id"])[0])
            cleanup = (
                self.root
                / report["checks"][0]["environment_binding"]["directory"]
                / "cleanup-receipt.json"
            )
            os.chmod(cleanup, 0o600)
            payload = json.loads(cleanup.read_text())
            payload["resource_id"] = "0" * 64
            cleanup.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "check invalid"):
                read_report(self.root, report["run_id"])

    def test_cross_view_reports_remain_readable_after_owned_cleanup(self):
        (self.root / "harness").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src/input.txt").write_text("fixture")
        check = {
            "check_id": "fixture.reusable",
            "command": [
                "python3",
                "-c",
                "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
            ],
            "executable": "python3",
            "cwd": ".",
            "timeout_seconds": 5,
            "scope": "repository-baseline",
            "triggers": [{"path": "src/"}],
            "module_dependencies": [],
            "required_environment": [],
            "input_paths": ["src"],
            "result_contract": {
                "type": "json-stdout",
                "required_fields": [
                    "status",
                    "checks_run",
                    "failures",
                    "errors",
                    "skipped",
                    "reason",
                ],
                "allowed_statuses": ["PASS"],
            },
            "transaction_reuse": True,
        }
        redis_check = {
            **check,
            "check_id": "fixture.redis-check",
            "required_environment": [],
        }
        redis_check.pop("transaction_reuse")
        redis_check["scenarios"] = ["repository-baseline"]
        # Required transient service path is executed in each profile; only the
        # independent ordinary check participates in exact transaction reuse.
        redis_check["required_environment"] = [services.SERVICE]
        check["scenarios"] = ["repository-baseline"]
        write_declarations(
            self.root / "config/engineering/module-checks.yaml", [check, redis_check]
        )
        first = freeze_inputs(self.root)
        second = freeze_inputs(self.root)
        from scripts.verification.execution.check_plan_runner import verify_profiles

        with (
            mock.patch.dict(os.environ, {services.ENV_KEY: ""}),
            mock.patch.object(services, "_local_engine", lambda: None),
        ):
            with self.context():
                reports = verify_profiles(self.root, frozen_profiles=[first, second])
                self.assertTrue(
                    all(report["result"] == "PASS" for report in reports), reports
                )
                self.assertEqual(
                    "deduplicated",
                    next(
                        x
                        for x in reports[1]["checks"]
                        if x["check_id"] == "fixture.reusable"
                    )["process"]["exit_reason"],
                )
                ids = [report["run_id"] for report in reports]
            for report_id in ids:
                loaded, _ = read_report(self.root, report_id)
                self.assertEqual("PASS", loaded["result"])

    def test_same_transaction_does_not_reuse_after_environment_change(self):
        (self.root / "harness").mkdir()
        (self.root / "src").mkdir()
        (self.root / "src/input.txt").write_text("fixture")
        check = {
            "check_id": "fixture.environment-sensitive",
            "command": [
                "python3",
                "-c",
                "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
            ],
            "executable": "python3",
            "cwd": ".",
            "timeout_seconds": 5,
            "scope": "repository-baseline",
            "scenarios": ["repository-baseline"],
            "triggers": [{"path": "src/"}],
            "module_dependencies": [],
            "required_environment": [],
            "input_paths": ["src"],
            "result_contract": {
                "type": "json-stdout",
                "required_fields": [
                    "status",
                    "checks_run",
                    "failures",
                    "errors",
                    "skipped",
                    "reason",
                ],
                "allowed_statuses": ["PASS"],
            },
            "transaction_reuse": True,
        }
        write_declarations(self.root / "config/engineering/module-checks.yaml", [check])
        profiles = [freeze_inputs(self.root), freeze_inputs(self.root)]
        from scripts.verification.execution.check_plan_runner import verify_profiles

        old_path = os.environ.get("PATH", "")
        with mock.patch.dict(os.environ, {"PATH": old_path}):

            def change_environment(index, _profile):
                if index == 1:
                    os.environ["PATH"] = (
                        old_path + os.pathsep + "/lexiflow-different-environment"
                    )
                return True

            reports = verify_profiles(
                self.root, frozen_profiles=profiles, before_profile=change_environment
            )
        self.assertTrue(all(report["result"] == "PASS" for report in reports), reports)
        self.assertEqual("exited", reports[0]["checks"][0]["process"]["exit_reason"])
        self.assertEqual("exited", reports[1]["checks"][0]["process"]["exit_reason"])

    def test_unneeded_services_do_not_touch_podman(self):
        with self.context(set()) as env:
            self.assertEqual({}, env)
        self.assertFalse(self.fake.calls)

    def test_inherited_endpoint_is_overridden_then_restored(self):
        with mock.patch.dict(os.environ, {services.ENV_KEY: "private-canary"}):
            with self.context() as env:
                self.assertEqual(env[services.ENV_KEY], os.environ[services.ENV_KEY])
            self.assertEqual("private-canary", os.environ[services.ENV_KEY])
        self.assertNotIn("private-canary", json.dumps(self.receipt()))

    def test_foreign_lease_is_never_deleted(self):
        self.fake.foreign_label = True
        with self.assertRaisesRegex(services.TestServicesError, "标签不匹配"):
            with self.context():
                pass
        self.assertFalse(any(args[0] == "rm" for args, _ in self.fake.calls))
        self.assertFalse(self.receipt()["cleanup_verified"])

    def test_failed_cleanup_cannot_pass(self):
        self.fake.fail_remove = True
        with self.assertRaisesRegex(services.TestServicesError, "未确认清理"):
            with self.context():
                pass
        self.assertFalse(self.receipt()["cleanup_verified"])

    def test_remote_engine_is_rejected_before_create(self):
        with mock.patch.dict(
            os.environ, {"CONTAINER_HOST": "ssh://user@remote.invalid/podman.sock"}
        ):
            with self.assertRaisesRegex(services.TestServicesError, "拒绝在远端"):
                with self.context():
                    pass
        self.assertFalse(any(args[0] == "create" for args, _ in self.fake.calls))

    def test_missing_image_does_not_create_or_pull(self):
        def command(args, *rest, **kwargs):
            if args[:2] == ["image", "inspect"]:
                return subprocess.CompletedProcess(args, 1, "", "missing")
            return self.fake(args, *rest, **kwargs)

        with mock.patch.object(services, "_command", side_effect=command):
            with self.assertRaisesRegex(services.TestServicesError, "不自动 pull"):
                with self.context():
                    pass
        self.assertFalse(any(args[0] == "create" for args, _ in self.fake.calls))


class TestPodmanClientTimeout(unittest.TestCase):
    def test_timeout_reaps_owned_process(self):
        original, processes = subprocess.Popen, []

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
