"""发布运行器 Docker 预检的合成边界测试；不连接用户 Docker。"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import patch

from ops.release.host_resources import release_docker_preflight as preflight_module


class _UnixHttpServer(socketserver.UnixStreamServer):
    daemon_threads = True


class ReleaseDockerPreflightTest(unittest.TestCase):
    """验证只读 Docker 预检、固定输出及身份漂移。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="lfpf-")).resolve()
        self._cleanup_diagnostic: str | None = None
        self.addCleanup(self._cleanup_fixture)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.socket_path = self.root / "docker.sock"
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.socket_path))
        self.log = self.root / "argv.jsonl"
        self.info = {
            "ID": "daemon-123",
            "OSType": "linux",
            "Architecture": "arm64",
            "ServerVersion": "27.0",
        }
        self.mode = "ok"
        self._write_cli()
        # The legacy fake CLI fixture is an explicitly classified synthetic Docker
        # daemon; real Unix HTTP API classification is covered by focused tests.
        self._engine_classification = patch.object(
            preflight_module, "_engine_identity", return_value=("docker", None)
        )
        self._engine_classification.start()
        self.addCleanup(self._engine_classification.stop)
        self.environment = {
            "PATH": os.pathsep.join(
                (str(self.bin), str(Path(sys.executable).parent), "/usr/bin", "/bin")
            ),
            "HOME": str(self.root),
            "DOCKER_HOST": f"unix://{self.socket_path}",
        }
        self.addCleanup(self._cleanup_recorded_descendant)

    def tearDown(self) -> None:
        self.socket.close()

    def _cleanup_fixture(self) -> None:
        if self._cleanup_diagnostic is None:
            shutil.rmtree(self.root, ignore_errors=True)

    def _cleanup_recorded_descendant(self) -> None:
        """只清理 fake CLI 明确记录的 synthetic child。"""
        record_path = self.root / "child.pid"
        if not record_path.exists():
            return
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            pid = int(record["child_pid"])
            pgid = int(record["process_group"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._preserve_cleanup_diagnostic("invalid-child-record")
            return
        try:
            if os.getpgid(pid) != pgid:
                self._preserve_cleanup_diagnostic("child-process-group-changed")
                return
        except ProcessLookupError:
            return
        except (PermissionError, OSError):
            self._preserve_cleanup_diagnostic("child-process-group-unavailable")
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except (PermissionError, OSError):
            self._preserve_cleanup_diagnostic("child-terminate-denied")
            return
        if self._wait_pid_gone(pid, 0.5):
            return
        try:
            if os.getpgid(pid) != pgid:
                self._preserve_cleanup_diagnostic("child-process-group-changed")
                return
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except (PermissionError, OSError):
            self._preserve_cleanup_diagnostic("child-kill-denied")
            return
        if not self._wait_pid_gone(pid, 1):
            self._preserve_cleanup_diagnostic("child-still-live-after-kill")

    @staticmethod
    def _wait_pid_gone(pid: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            except (PermissionError, OSError):
                return False
            process_stat = Path(f"/proc/{pid}/stat")
            try:
                if process_stat.exists() and process_stat.read_text().split()[2] == "Z":
                    return True
            except OSError:
                pass
            time.sleep(0.02)
        return False

    def _preserve_cleanup_diagnostic(self, reason: str) -> None:
        self._cleanup_diagnostic = reason
        diagnostic = self.root / "cleanup-diagnostic.json"
        diagnostic.write_text(
            json.dumps(
                {
                    "reason": reason,
                    "child_record": str(self.root / "child.pid"),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.fail(f"synthetic child cleanup unresolved; evidence retained: {self.root}")

    def _write_cli(self) -> None:
        source = f"""#!{sys.executable}
import json, os, sys, time, subprocess
args = sys.argv[1:]
with open({str(self.log)!r}, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(args) + "\\n")
mode = {self.mode!r}
if "API_TOKEN" in os.environ:
    open({str(self.root / "token-leaked")!r}, "w").write("unexpected")
if mode == "hang":
    child = subprocess.Popen(["sleep", "60"])
    with open({str(self.root / "child.pid")!r}, "w") as stream:
        json.dump({{"child_pid": child.pid, "process_group": os.getpgrp()}}, stream)
    time.sleep(60)
elif mode == "orphan":
    child = subprocess.Popen(
        ["sleep", "60"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    with open({str(self.root / "child.pid")!r}, "w") as stream:
        json.dump({{"child_pid": child.pid, "process_group": os.getpgrp()}}, stream)
elif mode == "large":
    sys.stdout.write("x" * (256 * 1024 + 1))
elif mode == "fail-secret":
    sys.stderr.write("do-not-leak-secret")
    sys.exit(1)
elif "info" in args:
    info = {self.info!r}
    print("|".join(
        json.dumps(info.get(key))
        for key in ("ID", "OSType", "Architecture", "ServerVersion")
    ))
elif "compose" in args:
    if mode == "compose-fail": sys.exit(1)
    print("Docker Compose version v2")
"""
        path = self.bin / "docker"
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def _preflight(self):
        return preflight_module.preflight(self.environment)

    def _assert_process_gone(self, pid: int) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            process_stat = Path(f"/proc/{pid}/stat")
            if process_stat.exists() and process_stat.read_text().split()[2] == "Z":
                return
            time.sleep(0.02)
        self.fail("timed-out or orphaned child process remained alive")

    def test_pass_binds_socket_daemon_and_uses_only_readonly_argv(self) -> None:
        result = self._preflight()
        self.assertEqual(
            ("PASS", "docker-readonly-preflight-succeeded"),
            (result.status, result.reason),
        )
        self.assertIsNotNone(result.binding)
        commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(commands)
        self.assertTrue(
            all(
                command[:2] == ["--host", f"unix://{self.socket_path}"]
                for command in commands
            )
        )
        self.assertEqual(
            [
                "--host",
                f"unix://{self.socket_path}",
                "info",
                "--format",
                preflight_module._INFO_FORMAT,
            ],
            commands[0],
        )
        self.assertEqual(
            ["--host", f"unix://{self.socket_path}", "compose", "version"],
            commands[1],
        )
        self.assertTrue(
            all(
                not any(
                    token in command for token in ("load", "up", "stop", "rm", "config")
                )
                for command in commands
            )
        )
        self.assertEqual("PASS", preflight_module.recheck(result.binding).status)

    def test_explicit_unix_host_is_required(self) -> None:
        for endpoint in (
            "",
            "tcp://example.invalid:2376",
            "unix:///socket?query=x",
            "unix:///socket#fragment",
        ):
            with self.subTest(endpoint=endpoint):
                invalid = dict(self.environment, DOCKER_HOST=endpoint)
                self.assertEqual(
                    "invalid-host", preflight_module.preflight(invalid).reason
                )
        self.assertFalse(self.log.exists())

    def test_environment_is_filtered_and_values_are_not_returned(self) -> None:
        secret = "do-not-leak-secret"
        result = preflight_module.preflight(dict(self.environment, API_TOKEN=secret))
        self.assertEqual("PASS", result.status)
        rendered = repr(result)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(str(self.socket_path), rendered)
        self.assertFalse((self.root / "token-leaked").exists())
        self.mode = "fail-secret"
        self._write_cli()
        failed = self._preflight()
        self.assertEqual("docker-daemon-unavailable", failed.reason)
        self.assertNotIn(secret, repr(failed))

    def test_context_and_user_config_are_not_discovery_inputs(self) -> None:
        self.environment.update(
            DOCKER_CONTEXT="bad context", DOCKER_CONFIG="/untrusted/config"
        )
        result = self._preflight()
        self.assertEqual("PASS", result.status)
        self.assertEqual({key for key, _ in result.binding.environment}, {"PATH"})
        commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(all("context" not in argv for argv in commands))
        self.environment.pop("DOCKER_HOST")
        with patch.object(preflight_module, "_run_bounded") as run:
            self.assertEqual("invalid-host", self._preflight().reason)
            run.assert_not_called()

    def test_empty_or_missing_path_uses_fixed_search_path(self) -> None:
        for path_entry in ({"PATH": ""}, {}):
            environment = {
                **path_entry,
                "HOME": str(self.root),
                "DOCKER_HOST": f"unix://{self.socket_path}",
            }
            with self.subTest(path_entry=path_entry):
                with patch.object(
                    preflight_module.shutil, "which", return_value=None
                ) as find_cli:
                    result = preflight_module.preflight(environment)
                self.assertEqual("docker-cli-missing", result.reason)
                find_cli.assert_called_once_with("docker", path="/usr/bin:/bin")

    def test_legal_socket_symlink_is_bound_to_canonical_target(self) -> None:
        alias = self.root / "docker-link.sock"
        alias.symlink_to(self.socket_path)
        self.environment["DOCKER_HOST"] = f"unix://{alias}"
        result = self._preflight()
        self.assertEqual("PASS", result.status)
        self.assertEqual(str(self.socket_path), result.binding.socket_path)
        second = self.root / "replacement.sock"
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(replacement.close)
        second.unlink(missing_ok=True)
        replacement.bind(str(second))
        alias.unlink()
        alias.symlink_to(second)
        self.assertEqual(
            "socket-identity-changed",
            preflight_module.recheck(result.binding).reason,
        )
        replacement.close()

    def test_missing_or_non_socket_endpoint_is_blocked(self) -> None:
        self.environment["DOCKER_HOST"] = "unix:///missing/socket"
        self.assertEqual("local-socket-unavailable", self._preflight().reason)

    def test_invalid_daemon_identity_or_missing_fields_is_sanitized(self) -> None:
        self.info["ID"] = "bad id"
        self._write_cli()
        invalid = self._preflight()
        self.assertEqual("docker-info-invalid", invalid.reason)
        self.assertNotIn("bad id", repr(invalid))
        self.info.pop("ServerVersion")
        self._write_cli()
        self.assertEqual("docker-info-invalid", self._preflight().reason)

    def test_only_arm64_daemon_is_supported(self) -> None:
        for arch, expected in (
            ("arm64", "PASS"),
            ("aarch64", "PASS"),
            ("x86_64", "BLOCKED"),
        ):
            self.info["Architecture"] = arch
            self._write_cli()
            self.assertEqual(expected, self._preflight().status)

    def test_compose_failure_is_blocked(self) -> None:
        self.mode = "compose-fail"
        self._write_cli()
        self.assertEqual("compose-unavailable", self._preflight().reason)

    def test_cleanup_failure_is_a_fixed_fail_not_environment_blocked(self) -> None:
        info = b'"daemon-123"|"linux"|"arm64"|"27.0"\n'
        with patch.object(
            preflight_module,
            "_run_bounded",
            side_effect=(
                preflight_module._CommandResult("exited", 0, info),
                preflight_module._CommandResult("cleanup-failed", None, b""),
            ),
        ):
            result = self._preflight()
        self.assertEqual(
            ("FAIL", "docker-process-cleanup-failed"),
            (result.status, result.reason),
        )

        with patch.object(
            preflight_module,
            "_run_bounded",
            return_value=preflight_module._CommandResult("cleanup-failed", None, b""),
        ):
            info_failure = self._preflight()
        self.assertEqual(
            ("FAIL", "docker-process-cleanup-failed"),
            (info_failure.status, info_failure.reason),
        )

        self.environment["DOCKER_HOST"] = f"unix://{self.socket_path}"
        with patch.object(
            preflight_module,
            "_run_bounded",
            side_effect=(
                preflight_module._CommandResult("exited", 0, info),
                preflight_module._CommandResult("cleanup-failed", None, b""),
            ),
        ):
            compose_failure = self._preflight()
        self.assertEqual(
            ("FAIL", "docker-process-cleanup-failed"),
            (compose_failure.status, compose_failure.reason),
        )

    def test_preflight_rejects_socket_identity_drift_during_queries(self) -> None:
        info = os.fstat(self.socket.fileno())
        identity = (str(self.socket_path), info.st_dev, info.st_ino)
        changed = (identity[0], identity[1], identity[2] + 1)
        with patch.object(
            preflight_module,
            "_inspect_socket",
            side_effect=(identity, identity, changed),
        ):
            result = self._preflight()
        self.assertEqual(
            ("FAIL", "socket-identity-changed"), (result.status, result.reason)
        )

    def test_bounded_runner_reaps_timed_out_group_and_hides_output(self) -> None:
        self.mode = "hang"
        self._write_cli()
        # Allow the real Python fake CLI to start and record its descendant;
        # this remains a test-only limit, below the production 30-second budget.
        with patch.object(preflight_module, "_TIMEOUT", 1.0):
            result = self._preflight()
        self.assertEqual("docker-info-unavailable", result.reason)
        child_pid = json.loads((self.root / "child.pid").read_text())["child_pid"]
        self._assert_process_gone(child_pid)
        self.mode = "large"
        self._write_cli()
        bounded = preflight_module._run_bounded(
            [str(self.bin / "docker"), "flood"],
            {"PATH": self.environment["PATH"]},
        )
        self.assertEqual("output-limit", bounded.status)
        self.assertEqual(b"", bounded.stdout)

    def test_successful_cli_exit_reaps_pipe_detached_descendant(self) -> None:
        self.mode = "orphan"
        self._write_cli()
        result = preflight_module._run_bounded(
            [str(self.bin / "docker"), "synthetic"],
            {"PATH": self.environment["PATH"]},
        )
        self.assertEqual("exited", result.status)
        child_pid = json.loads((self.root / "child.pid").read_text())["child_pid"]
        self._assert_process_gone(child_pid)

    def test_recheck_detects_daemon_id_drift(self) -> None:
        binding = self._preflight().binding
        self.info["ID"] = "different-daemon"
        self._write_cli()
        self.assertEqual(
            "daemon-identity-changed",
            preflight_module.recheck(binding).reason,
        )

    def _podman_http_server(self):
        self._engine_classification.stop()
        version = {
            "Components": [
                {"Name": "Podman Engine", "Details": {"APIVersion": "5.0.0"}}
            ],
            "ID": "random-compat-id-one",
        }
        info = {
            "host": {
                "os": "linux",
                "arch": "arm64",
                "hostname": "local",
                "security": {"rootless": True},
                "idMappings": {
                    "uidmap": [{"container_id": 0, "host_id": 1000, "size": 1}],
                    "gidmap": [{"container_id": 0, "host_id": 1000, "size": 1}],
                },
            },
            "store": {
                "graphDriverName": "overlay",
                "graphRoot": "/var/lib/containers/storage",
                "runRoot": "/run/user/1/containers",
                "volumePath": "/var/lib/containers/storage/volumes",
                "transientStore": False,
            },
            "version": {"Version": "5.6.2"},
        }
        responses = {"/version": version, "/v5.0.0/libpod/info": info}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps(responses[self.path]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        api_path = self.root / "podman-api.sock"
        server = _UnixHttpServer(str(api_path), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"unix://{api_path}", version, info

    def test_podman_identity_ignores_compat_id_but_binds_stable_store_facts(
        self,
    ) -> None:
        endpoint, version, info = self._podman_http_server()
        first, error = preflight_module._engine_identity(endpoint)
        version["ID"] = "random-compat-id-two"
        second, second_error = preflight_module._engine_identity(endpoint)
        self.assertIsNone(error)
        self.assertIsNone(second_error)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("podman:"))
        info["host"]["security"]["rootless"] = False
        changed, changed_error = preflight_module._engine_identity(endpoint)
        self.assertIsNone(changed_error)
        self.assertNotEqual(first, changed)

    def test_podman_api_identity_matches_real_posix_product_encoding(self) -> None:
        """同一原生事实与真实产品函数严格绑定同一固定字节摘要。"""
        endpoint, _version, info = self._podman_http_server()
        source_root = Path(__file__).resolve().parents[2]
        for rootless, map_field in ((True, "0,1000,1;"), (False, "-")):
            with self.subTest(rootless=rootless):
                info["host"]["security"]["rootless"] = rootless
                if not rootless:
                    info["host"]["idMappings"] = {"uidmap": [], "gidmap": []}
                record = (
                    f"linux|arm64|local|{str(rootless).lower()}|present|"
                    f"{map_field}|{map_field}|overlay|/var/lib/containers/storage|"
                    "/run/user/1/containers|/var/lib/containers/storage/volumes|"
                    "false|5.6.2\n"
                )
                (self.bin / "podman").write_text(
                    '#!/bin/sh\n[ "$1" = --url ] && [ "$3" = info ] && '
                    '[ "$4" = --format ] && [ "$#" = 5 ] || exit 1\n'
                    f"printf '%s\\n' '{record.rstrip()}'\n",
                    encoding="ascii",
                )
                (self.bin / "podman").chmod(0o700)
                expected = (
                    "podman:" + hashlib.sha256(record.encode("ascii")).hexdigest()
                )
                identity, error = preflight_module._engine_identity(endpoint)
                self.assertIsNone(error)
                self.assertEqual(expected, identity)
                actual = subprocess.run(
                    [
                        "sh",
                        "-c",
                        '. "$1"; . "$2"; lf_podman_identity_query "$3"',
                        "product-identity",
                        str(
                            source_root
                            / "ops/release/runtime/release-installation-state.sh"
                        ),
                        str(
                            source_root
                            / "ops/release/runtime/release-installation-docker.sh"
                        ),
                        endpoint,
                    ],
                    env={"PATH": self.environment["PATH"], "LC_ALL": "C"},
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=10,
                    check=True,
                )
                self.assertEqual((expected + "\n").encode("ascii"), actual.stdout)

    def test_real_unix_http_fixture_preflight_and_recheck_bind_stable_facts(
        self,
    ) -> None:
        endpoint, version, info = self._podman_http_server()
        environment = dict(self.environment, DOCKER_HOST=endpoint)
        info_output = b'"cli-compat-random-id"|"linux"|"arm64"|"5.6.2"\n'

        def docker_result(_docker, _endpoint, args, _env):
            if args[0] == "info":
                return preflight_module._CommandResult("exited", 0, info_output)
            return preflight_module._CommandResult(
                "exited", 0, b"Docker Compose version\n"
            )

        with patch.object(preflight_module, "_run_docker", side_effect=docker_result):
            result = preflight_module.preflight(environment)
            self.assertEqual(
                ("PASS", "docker-readonly-preflight-succeeded"),
                (result.status, result.reason),
            )
            version["ID"] = "a-different-random-compat-id"
            self.assertEqual("PASS", preflight_module.recheck(result.binding).status)
            info["store"]["graphRoot"] = "/different/store"
            self.assertEqual(
                "daemon-identity-changed",
                preflight_module.recheck(result.binding).reason,
            )

    def test_real_unix_http_docker_classification_keeps_strict_cli_id(self) -> None:
        endpoint, version, _info = self._podman_http_server()
        version["Components"] = [{"Name": "Engine", "Version": "27.0"}]
        environment = dict(self.environment, DOCKER_HOST=endpoint)
        daemon_id = ["docker-id-one"]

        def docker_result(_docker, _endpoint, args, _env):
            if args[0] == "info":
                payload = "|".join(
                    json.dumps(value)
                    for value in (daemon_id[0], "linux", "arm64", "27.0")
                )
                return preflight_module._CommandResult(
                    "exited", 0, payload.encode() + b"\n"
                )
            return preflight_module._CommandResult(
                "exited", 0, b"Docker Compose version\n"
            )

        with patch.object(preflight_module, "_run_docker", side_effect=docker_result):
            result = preflight_module.preflight(environment)
            self.assertEqual("PASS", result.status)
            self.assertEqual("docker-id-one", result.binding.daemon_id)
            daemon_id[0] = "docker-id-two"
            self.assertEqual(
                "daemon-identity-changed",
                preflight_module.recheck(result.binding).reason,
            )

    def test_engine_component_classification_fails_closed(self) -> None:
        self._engine_classification.stop()
        cases = (
            ([], "engine-unrecognized"),
            (
                [{"Name": "Podman Engine"}, {"Name": "Podman Engine"}],
                "engine-component-invalid",
            ),
            ([{"Name": "Mystery Engine"}], "engine-unrecognized"),
            (
                [{"Name": "Podman Engine", "Details": {"APIVersion": "v5"}}],
                "podman-api-invalid",
            ),
        )
        for components, expected in cases:
            with self.subTest(components=components):
                with patch.object(
                    preflight_module,
                    "_unix_api_json",
                    side_effect=({"Components": components}, None),
                ):
                    _identity, error = preflight_module._engine_identity(
                        "unix:///unused"
                    )
                self.assertEqual(expected, error)
        with patch.object(
            preflight_module,
            "_unix_api_json",
            side_effect=({"Components": [{"Name": "Engine"}]},),
        ):
            self.assertEqual(
                ("docker", None), preflight_module._engine_identity("unix:///unused")
            )
        with patch.object(
            preflight_module,
            "_unix_api_json",
            side_effect=(
                {
                    "Components": [
                        {"Name": "Podman Engine", "Details": {"APIVersion": "5.0.0"}}
                    ]
                },
                None,
            ),
        ):
            self.assertEqual(
                "podman-info-unavailable",
                preflight_module._engine_identity("unix:///unused")[1],
            )

    def test_podman_fact_validation_rejects_bad_platform_maps_and_store_facts(
        self,
    ) -> None:
        self._engine_classification.stop()
        endpoint, _version, valid_info = self._podman_http_server()
        api_version = {
            "Components": [
                {"Name": "Podman Engine", "Details": {"APIVersion": "5.0.0"}}
            ]
        }
        cases = (
            ("host.os", lambda data: data["host"].update(os="darwin")),
            ("host.arch", lambda data: data["host"].update(arch=7)),
            ("hostname", lambda data: data["host"].update(hostname=4)),
            (
                "hostname delimiter",
                lambda data: data["host"].update(hostname="bad|host"),
            ),
            (
                "hostname newline",
                lambda data: data["host"].update(hostname="bad\nhost"),
            ),
            ("hostname unicode", lambda data: data["host"].update(hostname="本机")),
            ("rootless", lambda data: data["host"]["security"].update(rootless="true")),
            (
                "idMappings key",
                lambda data: data["host"].update(idMappings={"uids": [], "gids": []}),
            ),
            (
                "mapping size",
                lambda data: data["host"]["idMappings"]["uidmap"][0].update(size=True),
            ),
            ("store path missing", lambda data: data["store"].update(runRoot="")),
            (
                "store path relative",
                lambda data: data["store"].update(graphRoot="relative"),
            ),
            ("store path type", lambda data: data["store"].update(volumePath=[])),
            ("driver type", lambda data: data["store"].update(graphDriverName=1)),
            (
                "driver delimiter",
                lambda data: data["store"].update(graphDriverName="a|b"),
            ),
            ("store delimiter", lambda data: data["store"].update(graphRoot="/a|b")),
            (
                "empty rootless mappings",
                lambda data: data["host"].update(
                    idMappings={"uidmap": [], "gidmap": []}
                ),
            ),
            ("transient type", lambda data: data["store"].update(transientStore=1)),
            ("version type", lambda data: data["version"].update(Version=5)),
        )
        for name, mutate in cases:
            with self.subTest(fact=name):
                info = copy.deepcopy(valid_info)
                mutate(info)
                with patch.object(
                    preflight_module,
                    "_unix_api_json",
                    side_effect=(api_version, info),
                ):
                    _identity, error = preflight_module._engine_identity(endpoint)
                self.assertIn(
                    error, {"podman-info-invalid", "podman-platform-mismatch"}
                )

    def test_unix_api_response_overflow_is_rejected(self) -> None:
        endpoint, _version, _info = self._podman_http_server()
        with patch.object(preflight_module, "_MAX_API_RESPONSE", 16):
            self.assertIsNone(preflight_module._unix_api_json(endpoint, "/version"))

    def test_common_supervision_cleanup_failure_maps_to_fail(self):
        from ops.release.host_resources.release_process_control import ProcessError

        with patch.object(
            preflight_module.release_process,
            "run_owned",
            side_effect=ProcessError("FAIL", "release-process-group-signal-denied"),
        ):
            self.assertEqual("FAIL", self._preflight().status)


if __name__ == "__main__":
    unittest.main()
