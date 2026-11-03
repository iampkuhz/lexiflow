"""Redis lease 的隔离、所有权、清理及客户端超时回归；不启动真实容器。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.environment.services import isolated_redis as services


class FakePodman:
    def __init__(self):
        self.calls = []
        self.resources = {}
        self.foreign_label = False
        self.fail_remove = False
        self.not_ready = False
        self.public_port = False

    def __call__(self, args, timeout=10, *, check=True):
        self.calls.append((args, timeout))
        code, output, error = 0, "", ""
        if args[0] == "info":
            output = "fixture-host"
        elif args[:3] == ["system", "connection", "list"]:
            output = json.dumps([{"Name": "fixture", "URI": "ssh://user@127.0.0.1:1234/podman.sock", "Default": True}])
        elif args[:2] == ["image", "inspect"]:
            output = "b" * 64
        elif args[0] == "create":
            name = args[args.index("--name") + 1]
            lease = args[args.index("--label") + 1].split("=", 1)[1]
            ident = "2" * 64
            self.resources[name] = {"Id": ident, "Config": {"Labels": {services.OWNER_LABEL: "foreign" if self.foreign_label else lease}}}
            output = ident
        elif args[0] == "start":
            output = args[-1]
        elif args[0] == "port":
            output = ("0.0.0.0" if self.public_port else "127.0.0.1") + ":56379"
        elif args[0] == "exec":
            code, output = (1, "") if self.not_ready else (0, "PONG")
        elif args[:2] == ["container", "exists"]:
            code = 0 if any(args[-1] in (name, item["Id"]) for name, item in self.resources.items()) else 1
        elif args[:2] == ["container", "inspect"]:
            output = json.dumps([self.resources[args[-1]]])
        elif args[0] == "rm":
            if self.fail_remove:
                raise services.TestServicesError("fixture remove unavailable")
            self.resources = {name: item for name, item in self.resources.items() if item["Id"] != args[-1]}
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
        with self.context() as env:
            self.assertEqual("127.0.0.1:56379", env[services.ENV_KEY])
            args = next(args for args, _ in self.fake.calls if args[0] == "create")
            self.assertIn("--pull=never", args)
            self.assertIn("--image-volume=ignore", args)
            self.assertTrue(args[args.index("--publish") + 1].startswith("127.0.0.1::"))
            self.assertNotIn("--volume", args)
        self.assertTrue(self.receipt()["cleanup_verified"])
        self.assertFalse(self.fake.resources)

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
        with mock.patch.dict(os.environ, {"CONTAINER_HOST": "ssh://user@remote.invalid/podman.sock"}):
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
            process = original([sys.executable, "-c", "import time;time.sleep(60)"], **kwargs)
            processes.append(process)
            return process
        with mock.patch.object(services.subprocess, "Popen", side_effect=popen):
            with self.assertRaisesRegex(services.TestServicesError, "超过"):
                services._command(["info"], timeout=0.05)
        self.assertIsNotNone(processes[0].poll())


if __name__ == "__main__":
    unittest.main()
