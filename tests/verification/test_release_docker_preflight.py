"""发布运行器 Docker 预检的合成边界测试；不连接用户 Docker。"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.environment import release_docker_preflight as preflight_module


class ReleaseDockerPreflightTest(unittest.TestCase):
    """验证只读 Docker 预检、固定输出及身份漂移。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="lexiflow-preflight-test-")).resolve()
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
            "Architecture": "x86_64",
            "ServerVersion": "27.0",
        }
        self.mode = "ok"
        self._write_cli()
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
elif args[:2] == ["context", "inspect"]:
    print(json.dumps("unix://{self.socket_path}"))
elif "compose" in args:
    if mode == "compose-fail": sys.exit(1)
    print("Docker Compose version v2")
"""
        path = self.bin / "docker"
        path.write_text(source, encoding="utf-8")
        path.chmod(0o755)

    def _preflight(self):
        return preflight_module.preflight(self.root, "linux/amd64", self.environment)

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

    def test_environment_conflict_and_remote_host_are_fixed_failures(self) -> None:
        conflicted = dict(self.environment, DOCKER_CONTEXT="default")
        remote = dict(self.environment, DOCKER_HOST="tcp://example.invalid:2376")
        self.assertEqual(
            "host-context-conflict",
            preflight_module.preflight(self.root, "linux/amd64", conflicted).reason,
        )
        self.assertEqual(
            "invalid-host",
            preflight_module.preflight(self.root, "linux/amd64", remote).reason,
        )

    def test_environment_is_filtered_and_values_are_not_returned(self) -> None:
        secret = "do-not-leak-secret"
        result = preflight_module.preflight(
            self.root, "linux/amd64", dict(self.environment, API_TOKEN=secret)
        )
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

    def test_context_name_validation_and_remote_context_rejection(self) -> None:
        self.environment.pop("DOCKER_HOST")
        self.environment.pop("DOCKER_CONTEXT", None)
        self.assertEqual("PASS", self._preflight().status)
        commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(
            ["context", "inspect", "--format", preflight_module._CONTEXT_FORMAT],
            commands[0],
        )
        self.log.write_text("", encoding="utf-8")
        self.environment["DOCKER_CONTEXT"] = "work"
        self.assertEqual("PASS", self._preflight().status)
        commands = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(
            [
                "context",
                "inspect",
                "work",
                "--format",
                preflight_module._CONTEXT_FORMAT,
            ],
            commands[0],
        )
        self.log.write_text("", encoding="utf-8")
        self.environment["DOCKER_CONTEXT"] = "bad context"
        self.assertEqual("invalid-context", self._preflight().reason)
        self.environment["DOCKER_CONTEXT"] = "work"
        self.mode = "remote-context"
        # 直接替换 runner 输出，避免真实 context 查询。
        self._write_cli()
        with patch.object(
            preflight_module,
            "_run_bounded",
            return_value=preflight_module._CommandResult(
                "exited", 0, b'"tcp://remote:2376"'
            ),
        ):
            self.assertEqual("docker-context-remote", self._preflight().reason)

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
                    result = preflight_module.preflight(
                        self.root, "linux/amd64", environment
                    )
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

    def test_platform_mapping_and_mismatch(self) -> None:
        self.info["Architecture"] = "aarch64"
        self._write_cli()
        self.assertEqual("BLOCKED", self._preflight().status)
        self.assertEqual(
            "PASS",
            preflight_module.preflight(
                self.root, "linux/arm64", self.environment
            ).status,
        )
        self.assertEqual(
            "invalid-target-platform",
            preflight_module.preflight(
                self.root, "linux/ppc64", self.environment
            ).reason,
        )

    def test_compose_failure_is_blocked(self) -> None:
        self.mode = "compose-fail"
        self._write_cli()
        self.assertEqual("compose-unavailable", self._preflight().reason)

    def test_cleanup_failure_is_a_fixed_fail_not_environment_blocked(self) -> None:
        info = b'"daemon-123"|"linux"|"x86_64"|"27.0"\n'
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

        self.environment.pop("DOCKER_HOST")
        with patch.object(
            preflight_module,
            "_run_bounded",
            return_value=preflight_module._CommandResult("cleanup-failed", None, b""),
        ):
            context_failure = self._preflight()
        self.assertEqual(
            ("FAIL", "docker-process-cleanup-failed"),
            (context_failure.status, context_failure.reason),
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


if __name__ == "__main__":
    unittest.main()
