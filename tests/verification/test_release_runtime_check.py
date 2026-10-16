"""发布runtime子进程 envelope 的严格边界测试。"""

from __future__ import annotations

import ctypes
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks.release.source.check_synthetic_release_lifecycle import (
    ENVELOPE_FIELDS,
    MAX_ENVELOPE,
    ConsumerError,
    _read_envelope,
)


def envelope() -> dict[str, object]:
    return {
        "schema_version": "lexiflow.verify-child-input.v2",
        "run_id": "12345678-1234-4234-8234-123456789012",
        "check_id": "eng.release.lifecycle-runtime",
        "snapshot": {"fingerprint": "a" * 64, "files": [], "missing": []},
    }


class BufferedInput:
    def __init__(self, data: bytes) -> None:
        self.buffer = io.BytesIO(data)


class TestReleaseRuntimeEnvelope(unittest.TestCase):
    def test_module_cli_preserves_canonical_helper_error_and_progress(self):
        """子进程复核 -m 同等入口与 helper 共用异常和计数，绝不运行引擎。"""
        for status in ("FAIL", "BLOCKED"):
            with self.subTest(status=status):
                source = f"""
import json
import runpy
from pathlib import Path
from unittest.mock import patch
from scripts.checks.release.source import check_synthetic_release_lifecycle as runtime
from scripts.checks.release.source.release_test_artifact_builder import _run

def consumer(*args, **kwargs):
    token = runtime._PROGRESS.set([0])
    try:
        with patch.object(runtime.release_process, 'run_bounded', return_value=(b'', b'')):
            _run(['mock-only-no-executable'], cwd=Path.cwd(), env={{}})
        raise runtime.ConsumerError({status!r}, 'mock-runtime-owner-error')
    finally:
        runtime._PROGRESS.reset(token)

with patch.object(runtime, '_read_envelope', return_value={envelope()!r}), \\
     patch.object(runtime, 'run_consumer', side_effect=consumer):
    runpy.run_module(runtime.__name__, run_name='__main__')
"""
                result = subprocess.run(
                    [sys.executable, "-c", source],
                    cwd=Path(__file__).resolve().parents[2],
                    input=json.dumps(envelope()),
                    text=True,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], status)
                self.assertEqual(report["reason"], "mock-runtime-owner-error")
                self.assertEqual(report["checks_run"], 1)
                self.assertEqual(report["failures"], int(status == "FAIL"))
                self.assertEqual(report["errors"], 0)
                self.assertEqual(report["skipped"], 0)
                self.assertEqual(report["run_id"], envelope()["run_id"])

    def test_supported_host_is_measured_not_environment_override(self):
        from types import SimpleNamespace

        from scripts.checks.release.source import (
            check_synthetic_release_lifecycle as runtime,
        )

        for system, machine, allowed in (
            ("darwin", "arm64", True),
            ("darwin", "aarch64", True),
            ("darwin", "x86_64", False),
            ("linux", "aarch64", False),
        ):
            with (
                self.subTest(system=system, machine=machine),
                patch.object(runtime.sys, "platform", system),
                patch.object(
                    runtime.os, "uname", return_value=SimpleNamespace(machine=machine)
                ),
                patch.dict(
                    os.environ, {"LEXIFLOW_RELEASE_HOST_PLATFORM": "darwin/arm64"}
                ),
            ):
                if allowed:
                    runtime._require_supported_host()
                else:
                    with self.assertRaisesRegex(
                        ConsumerError, "BLOCKED/release-host-platform-unsupported"
                    ):
                        runtime._require_supported_host()

    def test_release_bindings_require_only_arm64_and_block_when_missing(self):
        from types import SimpleNamespace

        from scripts.checks.release.source import (
            check_synthetic_release_lifecycle as runtime,
        )

        binding = SimpleNamespace(
            endpoint="unix:///private/docker.sock",
            daemon_id="daemon-arm64",
            target_platform="linux/arm64",
        )
        with (
            patch.object(runtime, "_require_supported_host"),
            patch.object(
                runtime.release_docker_preflight,
                "preflight",
                return_value=SimpleNamespace(
                    status="PASS", reason="OK", binding=binding
                ),
            ) as preflight,
        ):
            result = runtime._binding(
                {"LEXIFLOW_RELEASE_ARM64_DOCKER_HOST": "unix:///private/docker.sock"},
            )
        self.assertIs(result, binding)
        preflight.assert_called_once_with(
            {"DOCKER_HOST": "unix:///private/docker.sock", "PATH": ""}
        )
        with patch.object(runtime, "_require_supported_host"):
            with self.assertRaisesRegex(
                ConsumerError, "BLOCKED/missing-lexiflow-release-arm64-docker-host"
            ):
                runtime._binding({})

    def test_accepts_exact_schema_and_snapshot_without_check_id(self):
        value = _read_envelope(BufferedInput(json.dumps(envelope()).encode()))
        self.assertEqual(set(value), ENVELOPE_FIELDS)
        self.assertNotIn("check_id", value["snapshot"])

    def test_eof_is_blocked_and_bad_envelopes_fail(self):
        with self.assertRaisesRegex(
            ConsumerError, "BLOCKED/verify-context-unavailable"
        ):
            _read_envelope(BufferedInput(b""))
        for data in (
            b"{",
            b'{"a":1,"a":1}',
            json.dumps({**envelope(), "extra": True}).encode(),
            json.dumps(
                {
                    **envelope(),
                    "snapshot": {"fingerprint": "x", "files": [], "missing": ["x"]},
                }
            ).encode(),
        ):
            with self.subTest(data=data[:20]), self.assertRaises(ConsumerError):
                _read_envelope(BufferedInput(data))

    def test_oversize_and_wrong_check_rejected(self):
        with self.assertRaises(ConsumerError):
            _read_envelope(BufferedInput(b" " * (MAX_ENVELOPE + 1)))
        bad = envelope()
        bad["check_id"] = "other.check"
        with self.assertRaises(ConsumerError):
            _read_envelope(BufferedInput(json.dumps(bad).encode()))

    def test_bridge_cleanup_failure_is_fail_and_retains_scratch(self):
        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            run_consumer,
        )
        from scripts.checks.release.source.frozen_source_fixture import BridgeError

        with tempfile.TemporaryDirectory(prefix="runtime-cleanup-failure-") as parent:
            scratch = Path(parent) / "scratch"
            scratch.mkdir()
            value = envelope()
            with (
                patch(
                    "scripts.checks.release.source.check_synthetic_release_lifecycle.decode_child_request",
                    return_value=({}, value["snapshot"], value["run_id"]),
                ),
                patch(
                    "scripts.checks.release.source.check_synthetic_release_lifecycle.tempfile.mkdtemp",
                    return_value=str(scratch),
                ),
                patch(
                    "scripts.checks.release.source.check_synthetic_release_lifecycle.shutil.rmtree",
                    side_effect=OSError("cleanup denied"),
                ),
                patch(
                    "scripts.checks.release.source.check_synthetic_release_lifecycle.consume_verify_snapshot",
                    side_effect=BridgeError("BLOCKED", "source-unavailable"),
                ),
            ):
                with self.assertRaisesRegex(
                    ConsumerError, "FAIL/release-scratch-cleanup-failed"
                ):
                    run_consumer(
                        parent,
                        value,
                    )
            self.assertTrue(scratch.is_dir())
            self.assertTrue((scratch / "source-parent").is_dir())

    def test_fixed_bridge_consumes_complete_verify_context_and_cleans_failure(self):
        from scripts.checks.release.source import (
            check_synthetic_release_lifecycle as runtime,
        )
        from scripts.checks.release.source.frozen_source_fixture import BridgeError

        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory).resolve() / "scratch"
            scratch.mkdir()
            value = envelope()
            with (
                patch.object(
                    runtime,
                    "decode_child_request",
                    return_value=({}, value["snapshot"], value["run_id"]),
                ),
                patch.object(runtime.tempfile, "mkdtemp", return_value=str(scratch)),
                patch.object(
                    runtime,
                    "consume_verify_snapshot",
                    side_effect=BridgeError("BLOCKED", "source-unavailable"),
                ) as bridge,
            ):
                with self.assertRaisesRegex(
                    ConsumerError, "BLOCKED/source-unavailable"
                ):
                    runtime.run_consumer(directory, value, environ={})
            bridge.assert_called_once_with(
                Path(directory).resolve(),
                {key: value[key] for key in ("run_id", "check_id")},
                value["snapshot"],
                scratch / "source-parent",
            )
            self.assertFalse(scratch.exists())

    def test_public_bounded_process_boundary_preserves_fail_closed_facts(self):
        """共用 ops 边界只接受零退出及完整清理，不依赖 synthetic 检查。"""
        from ops.release.host_resources import release_process_control as process

        good = process.ProcessFacts(0, b"out", b"err", False, False, True)
        with patch.object(process, "run_owned", return_value=good):
            self.assertEqual(
                process.run_bounded(["unused"], Path.cwd(), {}, 1),
                (b"out", b"err"),
            )
        cases = (
            (
                process.ProcessFacts(0, b"", b"", False, True, True),
                "release-command-output-limit",
            ),
            (
                process.ProcessFacts(None, b"", b"", True, False, True),
                "release-command-timeout",
            ),
            (
                process.ProcessFacts(7, b"", b"", False, False, True),
                "release-command-failed",
            ),
            (
                process.ProcessFacts(0, b"", b"", False, False, False),
                "release-command-failed",
            ),
        )
        for facts, reason in cases:
            with (
                self.subTest(reason=reason),
                patch.object(process, "run_owned", return_value=facts),
            ):
                with self.assertRaisesRegex(process.ProcessError, reason):
                    process.run_bounded(["unused"], Path.cwd(), {}, 1)

    def test_bounded_runner_collects_and_reaps_each_process_path(self):
        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            _run_bounded,
        )

        with tempfile.TemporaryDirectory(prefix="bounded-process-test-") as tmp:
            root = Path(tmp)
            normal = root / "normal.py"
            normal.write_text("print('bounded-ok')\n", encoding="utf-8")
            output, _ = _run_bounded(
                [sys.executable, str(normal)], root, os.environ, 5, limit=128
            )
            self.assertEqual(output, b"bounded-ok\n")

            sleeper = root / "sleeper.py"
            sleeper.write_text("import time; time.sleep(30)\n", encoding="utf-8")
            started = time.monotonic()
            with self.assertRaisesRegex(ConsumerError, "release-command-timeout"):
                _run_bounded(
                    [sys.executable, str(sleeper)], root, os.environ, 1, limit=128
                )
            self.assertLess(time.monotonic() - started, 3)

            overflow = root / "overflow.py"
            overflow.write_text("print('x' * 4096)\n", encoding="utf-8")
            with self.assertRaisesRegex(ConsumerError, "release-command-output-limit"):
                _run_bounded(
                    [sys.executable, str(overflow)], root, os.environ, 5, limit=128
                )

            child_pid = root / "pipe-holder.pid"
            child = root / "pipe-holder.py"
            child.write_text(
                "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)\n",
                encoding="utf-8",
            )
            parent = root / "short-parent.py"
            parent.write_text(
                "import subprocess,sys; subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n",
                encoding="utf-8",
            )
            started = time.monotonic()
            with self.assertRaises(ConsumerError):
                _run_bounded(
                    [sys.executable, str(parent), str(child), str(child_pid)],
                    root,
                    os.environ,
                    5,
                    limit=128,
                )
            self.assertLess(time.monotonic() - started, 4)
            if child_pid.exists():
                self._assert_process_stops(int(child_pid.read_text(encoding="ascii")))

    def test_bounded_command_consumes_private_input_stream(self):
        from scripts.checks.release.source.check_synthetic_release_lifecycle import (
            _run_bounded,
        )

        with tempfile.TemporaryFile() as stream:
            stream.write(b'{"candidate":"fixed"}')
            stream.seek(0)
            stdout, stderr = _run_bounded(
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
                ],
                Path.cwd(),
                os.environ,
                5,
                input_stream=stream,
            )
        self.assertEqual(stdout, b'{"candidate":"fixed"}')
        self.assertEqual(stderr, b"")

    def test_common_supervisor_returns_actual_exit_timeout_and_overflow_facts(self):
        from ops.release.host_resources import (
            release_process_control as release_process,
        )

        facts = release_process.run_owned(
            [sys.executable, "-c", "import sys; print('exit'); sys.exit(7)"],
            Path.cwd(),
            os.environ,
            5,
        )
        self.assertEqual(facts.returncode, 7)
        self.assertEqual(facts.stdout, b"exit\n")
        self.assertTrue(facts.cleanup_succeeded)
        timeout = release_process.run_owned(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            Path.cwd(),
            os.environ,
            0.1,
        )
        self.assertTrue(timeout.timed_out)
        self.assertIsNotNone(timeout.returncode)
        self.assertNotEqual(timeout.returncode, 0)
        flood = release_process.run_owned(
            [sys.executable, "-c", "print('x' * 4096)"], Path.cwd(), os.environ, 5, 128
        )
        self.assertTrue(flood.overflow)
        self.assertTrue(flood.cleanup_succeeded)

    def test_signal_denial_never_returns_cleanup_success(self):
        from ops.release.host_resources import (
            release_process_control as release_process,
        )

        with (
            patch.object(release_process.sys, "platform", "linux"),
            patch.object(os, "killpg", side_effect=PermissionError),
        ):
            with self.assertRaisesRegex(
                release_process.ProcessError, "release-process-group-signal-denied"
            ):
                release_process.run_owned(
                    [sys.executable, "-c", "print('done')"], Path.cwd(), os.environ, 5
                )

    def test_process_group_signals_and_registry_removal_precede_reap(self):
        from ops.release.host_resources import release_process_control as runtime

        events: list[tuple[str, int]] = []
        real_wait = subprocess.Popen.wait
        real_killpg = os.killpg

        def tracked_wait(process, *args, **kwargs):
            events.append(("wait", process.pid))
            self.assertNotIn(process.pid, runtime._ACTIVE_PROCESS_GROUPS)
            return real_wait(process, *args, **kwargs)

        def tracked_killpg(pgid, sig):
            if sig == 0:
                events.append(("observe", pgid))
                self.assertNotIn(pgid, runtime._ACTIVE_PROCESS_GROUPS)
            else:
                events.append(("signal", pgid))
                self.assertIn(pgid, runtime._ACTIVE_PROCESS_GROUPS)
            return real_killpg(pgid, sig)

        with tempfile.TemporaryDirectory(prefix="bounded-lifetime-test-") as tmp:
            script = Path(tmp) / "short.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            with (
                patch.object(subprocess.Popen, "wait", tracked_wait),
                patch.object(os, "killpg", tracked_killpg),
            ):
                facts = runtime.run_owned(
                    [sys.executable, str(script)], Path(tmp), os.environ, 5
                )
            self.assertEqual(facts.stdout, b"ok\n")
        reap_index = next(i for i, event in enumerate(events) if event[0] == "wait")
        self.assertTrue(any(event[0] == "signal" for event in events[:reap_index]))
        self.assertFalse(any(event[0] == "signal" for event in events[reap_index:]))

    def test_post_reap_unknown_or_live_group_is_not_cleanup_success(self):
        from ops.release.host_resources import (
            release_process_control as release_process,
        )

        class Clock:
            now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, duration):
                self.now += duration

        for states, expected in (
            ([None, False], True),
            ([None], False),
            ([True], False),
        ):
            clock = Clock()
            observed = iter(states)
            last = states[-1]

            def probe(pgid, sig):
                nonlocal last
                self.assertEqual((pgid, sig), (4242, 0))
                last = next(observed, last)
                if last is False:
                    raise ProcessLookupError
                if last is None:
                    raise PermissionError

            with (
                patch.object(release_process.time, "monotonic", clock.monotonic),
                patch.object(release_process.time, "sleep", clock.sleep),
                patch.object(release_process.os, "killpg", side_effect=probe),
            ):
                self.assertEqual(
                    release_process._group_absent_after_reap(4242), expected
                )
                self.assertLessEqual(clock.now, 1.02)

    def test_post_reap_group_failure_is_a_process_fail(self):
        from ops.release.host_resources import (
            release_process_control as release_process,
        )

        with patch.object(
            release_process, "_group_absent_after_reap", return_value=False
        ):
            with self.assertRaisesRegex(
                release_process.ProcessError, "release-command-cleanup-failed"
            ):
                release_process.run_owned(
                    [sys.executable, "-c", "print('done')"], Path.cwd(), os.environ, 5
                )

    def test_unsupported_waitid_blocks_before_spawn(self):
        from ops.release.host_resources import release_process_control as runtime

        with tempfile.TemporaryDirectory(prefix="bounded-no-waitid-") as tmp:
            with (
                patch.object(runtime.os, "WNOWAIT", create=True, new=None),
                patch.object(subprocess, "Popen") as popen,
            ):
                with self.assertRaisesRegex(
                    runtime.ProcessError, "BLOCKED/release-process-lifetime-unsupported"
                ):
                    runtime.run_owned(["unused"], Path(tmp), {}, 1)
                popen.assert_not_called()

    def test_darwin_permission_error_requires_exact_zombie_group(self):
        from ops.release.host_resources import release_process_control as runtime

        class Group:
            def __init__(self, members):
                self.members = members

            def proc_listpgrppids(self, _pgid, buffer, _size):
                if buffer is None:
                    return len(self.members)
                for index, pid in enumerate(self.members):
                    buffer[index] = pid
                return len(self.members)

            def proc_pidinfo(self, pid, flavor, arg, buffer, _size):
                self_flavor = (flavor, arg)
                if self_flavor != (13, 1):
                    return 0
                state = 5 if pid == 41001 else 2
                ctypes.memmove(buffer, struct.pack("=IIII", pid, 1, 41001, state), 16)
                return 80

        with (
            patch.object(runtime.sys, "platform", "darwin"),
            patch.object(runtime.os, "killpg", side_effect=PermissionError),
            patch.object(runtime, "_DARWIN_LIBPROC", Group([41001])),
        ):
            runtime._signal_process_group(41001, 15)
            with patch.object(runtime, "_DARWIN_LIBPROC", Group([41001, 41002])):
                with self.assertRaisesRegex(
                    runtime.ProcessError, "FAIL/release-process-group-signal-denied"
                ):
                    runtime._signal_process_group(41001, 15)
            with patch.object(runtime, "_DARWIN_LIBPROC", Group([41002])):
                with self.assertRaises(runtime.ProcessError):
                    runtime._signal_process_group(41001, 15)

    def test_darwin_rechecks_reaped_member_before_accepting_permission_error(self):
        from ops.release.host_resources import release_process_control as runtime

        class ReapedDescendant:
            listings = 0

            def __init__(self, vanish: bool) -> None:
                self.vanish = vanish

            def proc_listpgrppids(self, _pgid, buffer, _size):
                if buffer is None:
                    self.listings += 1
                members = (
                    [41001, 41002] if self.listings == 1 or not self.vanish else [41001]
                )
                if buffer is not None:
                    for index, pid in enumerate(members):
                        buffer[index] = pid
                return len(members)

            def proc_pidinfo(self, pid, _flavor, _arg, buffer, _size):
                if pid == 41002:
                    return 0
                ctypes.memmove(buffer, struct.pack("=IIII", pid, 1, 41001, 5), 16)
                return 80

        group = ReapedDescendant(vanish=True)
        with (
            patch.object(runtime.sys, "platform", "darwin"),
            patch.object(runtime.os, "killpg", side_effect=PermissionError),
            patch.object(runtime, "_DARWIN_LIBPROC", group),
        ):
            runtime._signal_process_group(41001, 15)
        self.assertEqual(group.listings, 2)

        unknown = ReapedDescendant(vanish=False)
        with (
            patch.object(runtime.sys, "platform", "darwin"),
            patch.object(runtime.os, "killpg", side_effect=PermissionError),
            patch.object(runtime, "_DARWIN_LIBPROC", unknown),
        ):
            with self.assertRaisesRegex(
                runtime.ProcessError, "FAIL/release-process-group-signal-denied"
            ):
                runtime._signal_process_group(41001, 15)
        self.assertGreaterEqual(unknown.listings, 2)
        self.assertLessEqual(unknown.listings, 12)

    def test_darwin_waits_boundedly_for_live_member_to_become_zombie(self):
        from ops.release.host_resources import release_process_control as runtime

        class Clock:
            now = 0.0

            def monotonic(self):
                return self.now

            def sleep(self, seconds):
                self.now += seconds

        class Group:
            listings = 0

            def __init__(self, settle_after: int | None) -> None:
                self.settle_after = settle_after

            def proc_listpgrppids(self, _pgid, buffer, _size):
                if buffer is None:
                    self.listings += 1
                if buffer is not None:
                    buffer[0], buffer[1] = 41001, 41002
                return 2

            def proc_pidinfo(self, pid, _flavor, _arg, buffer, _size):
                state = (
                    5
                    if pid == 41001
                    or (
                        self.settle_after is not None
                        and self.listings >= self.settle_after
                    )
                    else 2
                )
                ctypes.memmove(buffer, struct.pack("=IIII", pid, 1, 41001, state), 16)
                return 80

        clock = Clock()
        settling = Group(settle_after=3)
        with (
            patch.object(runtime.sys, "platform", "darwin"),
            patch.object(runtime.os, "killpg", side_effect=PermissionError),
            patch.object(runtime, "_DARWIN_LIBPROC", settling),
            patch.object(runtime.time, "monotonic", clock.monotonic),
            patch.object(runtime.time, "sleep", clock.sleep),
        ):
            runtime._signal_process_group(41001, 15)
        self.assertEqual(settling.listings, 3)
        self.assertLessEqual(clock.now, 0.05)

        clock = Clock()
        persistent = Group(settle_after=None)
        with (
            patch.object(runtime.sys, "platform", "darwin"),
            patch.object(runtime.os, "killpg", side_effect=PermissionError),
            patch.object(runtime, "_DARWIN_LIBPROC", persistent),
            patch.object(runtime.time, "monotonic", clock.monotonic),
            patch.object(runtime.time, "sleep", clock.sleep),
        ):
            with self.assertRaisesRegex(
                runtime.ProcessError, "FAIL/release-process-group-signal-denied"
            ):
                runtime._signal_process_group(41001, 15)
        self.assertGreaterEqual(persistent.listings, 2)
        self.assertLessEqual(persistent.listings, 12)
        self.assertLessEqual(clock.now, 0.05)

    def test_outer_term_kills_registered_child_and_grandchild_group(self):
        with tempfile.TemporaryDirectory(prefix="outer-term-process-test-") as tmp:
            root = Path(tmp)
            child_pid = root / "grandchild.pid"
            runner_pid = root / "runner.pid"
            worker = root / "consumer.py"
            runner = root / "runner.py"
            grandchild = root / "grandchild.py"
            grandchild.write_text(
                "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(30)\n",
                encoding="utf-8",
            )
            runner.write_text(
                "import os,subprocess,sys,time; open(sys.argv[2],'w').write(str(os.getpid())); "
                "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[3]]); time.sleep(30)\n",
                encoding="utf-8",
            )
            worker.write_text(
                "import os,sys; from pathlib import Path; "
                "from scripts.checks.release.source.check_synthetic_release_lifecycle import _run_bounded; "
                "_run_bounded([sys.executable,sys.argv[1],sys.argv[2],sys.argv[4],sys.argv[3]], "
                "Path(sys.argv[5]), os.environ, 30)\n",
                encoding="utf-8",
            )
            repo = Path(__file__).resolve().parents[2]
            env = dict(os.environ)
            env["PYTHONPATH"] = str(repo)
            error_path = root / "worker-stderr.log"
            with error_path.open("wb") as worker_stderr:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(worker),
                        str(runner),
                        str(grandchild),
                        str(child_pid),
                        str(runner_pid),
                        str(root),
                    ],
                    cwd=repo,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=worker_stderr,
                    start_new_session=True,
                )
            group_id = None
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and not child_pid.exists():
                    time.sleep(0.02)
                self.assertTrue(child_pid.is_file())
                self.assertTrue(runner_pid.is_file())
                group_id = int(runner_pid.read_text(encoding="ascii"))
                started = time.monotonic()
                process.terminate()
                self.assertEqual(
                    process.wait(timeout=1),
                    128 + 15,
                    error_path.read_text(encoding="utf-8", errors="replace")[:16384],
                )
                self.assertLess(time.monotonic() - started, 0.5)
                self._assert_process_stops(int(child_pid.read_text(encoding="ascii")))
                self._assert_process_stops(group_id)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=1)
                if group_id is not None:
                    try:
                        os.killpg(group_id, 9)
                    except ProcessLookupError:
                        pass

    def _assert_process_stops(self, pid: int) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            proc_stat = Path(f"/proc/{pid}/stat")
            if proc_stat.is_file():
                fields = proc_stat.read_text(encoding="ascii").split()
                if len(fields) > 2 and fields[2] == "Z":
                    return
            time.sleep(0.02)
        self.fail(f"owned process {pid} survived process-group cleanup")

    def test_real_stdin_envelope_is_consumed_by_source_bridge(self):
        from scripts.checks.release.source.frozen_source_fixture import (
            consume_verify_snapshot,
        )
        from scripts.verification.execution.check_runner import (
            resolve_child_check,
            snapshot_check_inputs,
        )

        root = Path(__file__).resolve().parents[2]
        check = resolve_child_check(root, "eng.release.lifecycle-runtime")
        snapshot = snapshot_check_inputs(root, check)
        context = {
            "run_id": "12345678-1234-4234-8234-123456789012",
            "check_id": check["check_id"],
        }
        value = {
            "schema_version": "lexiflow.verify-child-input.v2",
            **context,
            "snapshot": snapshot,
        }
        got = _read_envelope(BufferedInput(json.dumps(value).encode()))
        with tempfile.TemporaryDirectory(
            prefix="release-runtime-bridge-test-"
        ) as directory:
            output = Path(directory).resolve() / "out"
            output.mkdir()
            result = consume_verify_snapshot(root, context, got["snapshot"], output)
            provenance = result["provenance"]
            self.assertEqual(
                provenance["verifyInputFingerprint"], snapshot["fingerprint"]
            )
            self.assertIs(provenance["formalReleaseEligible"], False)
