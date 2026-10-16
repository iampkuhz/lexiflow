"""发行 Docker 命令执行器的有界合成测试，不连接真实引擎。"""

import os
import pathlib
import sys
import subprocess
import shlex
import signal
import tempfile
import time
import unittest

SOURCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "release/runtime/release-installation-docker.sh"
)


class DockerBoundsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.tmp = self.root / "private-tmp"
        self.tmp.mkdir()
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "TMPDIR": str(self.tmp),
            "LF_DOCKER_ENDPOINT": "unix:///poisoned.sock",
            "LF_DOCKER_BUDGET": "1",
            "LF_DOCKER_PID": "1",
        }
        self.script = self.root / "entry.sh"
        self.script.write_text(
            '. "$1"\nlf_docker_endpoint() { LF_DOCKER_ENDPOINT=unix:///synthetic.sock; }\n'
        )

    def stub(self, name, body):
        file = self.bin / name
        file.write_text("#!/bin/sh\n" + body)
        file.chmod(0o755)
        return file

    def trace_helpers(self):
        directories = self.root / "created-directories"
        sleepers = self.root / "timer-processes"
        self.stub(
            "mktemp",
            'created=$(/usr/bin/mktemp "$@") || exit 1\n'
            f'printf "%s\\n" "$created" >> {shlex.quote(str(directories))}\n'
            'printf "%s\\n" "$created"\n',
        )
        self.stub(
            "sleep",
            f'printf "%s:%s\\n" "$$" "$1" >> {shlex.quote(str(sleepers))}\n'
            'exec /bin/sleep "$1"\n',
        )
        return directories, sleepers

    def assert_helpers_reaped(self, directories, sleepers):
        self.assertTrue(directories.exists())
        for line in directories.read_text().splitlines():
            self.assertFalse(pathlib.Path(line).exists(), line)
        if sleepers.exists():
            for line in sleepers.read_text().splitlines():
                with self.assertRaises(ProcessLookupError, msg=line):
                    os.kill(int(line.split(":", 1)[0]), 0)

    def write_shell(self, tail, real_endpoint=False):
        head = (
            SOURCE.with_name("release-installation-state.sh").read_text() + '\n. "$1"\n'
        )
        if not real_endpoint:
            head += (
                "lf_docker_endpoint() { LF_DOCKER_ENDPOINT=unix:///synthetic.sock; }\n"
            )
        self.script.write_text(head + tail + "\n")

    def run_shell(self, tail, timeout=5, env=None, real_endpoint=False):
        self.write_shell(tail, real_endpoint=real_endpoint)
        return subprocess.run(
            ["/bin/sh", str(self.script), str(SOURCE)],
            env={**self.env, **(env or {})},
            capture_output=True,
            timeout=timeout,
        )

    def test_success_and_failure_output(self):
        directories, sleepers = self.trace_helpers()
        self.stub(
            "docker", '/bin/sleep 0.1\nprintf "clean\\n"; printf "private\\n" >&2\n'
        )
        result = self.run_shell("lf_docker info --format fixed")
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"clean\n", b"")
        )
        self.stub(
            "docker",
            '/bin/sleep 0.1\nprintf "secret stdout\\n"; printf "secret stderr\\n" >&2; exit 17\n',
        )
        result = self.run_shell("lf_docker info --format fixed")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, b"")
        self.assert_helpers_reaped(directories, sleepers)

    def test_internal_tool_errors_are_not_exposed(self):
        directories, sleepers = self.trace_helpers()
        self.stub("docker", 'printf "safe\\n"\n')
        self.stub("rm", 'printf "private cleanup path\\n" >&2\nexec /bin/rm "$@"\n')
        self.stub(
            "rmdir", 'printf "private cleanup path\\n" >&2\nexec /bin/rmdir "$@"\n'
        )
        result = self.run_shell("lf_docker info --format fixed")
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"safe\n", b"")
        )
        self.assert_helpers_reaped(directories, sleepers)
        self.stub("mktemp", 'printf "private mktemp path\\n" >&2\nexit 1\n')
        result = self.run_shell("lf_docker info --format fixed")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, b"")

    def test_fast_completion_and_successful_exit_after_deadline(self):
        directories, sleepers = self.trace_helpers()
        self.stub("docker", "exit 0\n")
        result = self.run_shell(
            'i=0\nwhile [ "$i" -lt 20 ]; do lf_docker info --format fixed || exit 1; '
            "i=$((i + 1)); done",
            timeout=5,
        )
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"", b"")
        )
        self.stub("docker", 'trap "exit 0" TERM\nwhile :; do :; done\n')
        result = self.run_shell("lf_docker_run 1 docker info", timeout=4)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, b"")
        self.assert_helpers_reaped(directories, sleepers)

    def test_errexit_accepts_reaped_timer_term_status_after_successful_child(self):
        self.stub("docker", 'printf "safe\\n"\n')
        result = self.run_shell(
            "set -eu\n"
            "wait_calls=0\n"
            "wait() {\n"
            "  wait_calls=$((wait_calls + 1))\n"
            '  if command wait "$@"; then waited=0; else waited=$?; fi\n'
            '  if [ "$wait_calls" -eq 2 ]; then return 143; fi\n'
            '  return "$waited"\n'
            "}\n"
            "lf_docker_run 30 docker info\n"
        )
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr), (0, b"safe\n", b"")
        )

    def test_timer_registration_cancellation_reaps_owned_sleep_helper(self):
        # 从实际源码抽取 timer，在 & 与 $! 登记之间加入同步门；不靠调度概率。
        source = SOURCE.read_text()
        start = source.index("    sleeper= grace=")
        end = source.index("  ) &\n  timer=$!", start)
        timer_source = source[start:end]
        self.assertIn("trap cancel_timer HUP INT TERM", timer_source)
        for field, launch in (("sleeper", 'sleep "$budget"'), ("grace", "sleep 1")):
            for immediate_exit in (True, False):
                with self.subTest(helper=field, old_handler=immediate_exit):
                    work = self.root / f"registration-{field}-{immediate_exit}"
                    work.mkdir()

                    def q(name):
                        return shlex.quote(str(work / name))

                    marker = f"{launch} >/dev/null 2>&1 & {field}=$!"
                    self.assertEqual(timer_source.count(marker), 1)
                    # 仅私有探针用 30 秒 helper 保证存活可观察；产品预算未改。
                    gate = (
                        "/bin/sleep 30 >/dev/null 2>&1 &\n"
                        f"    printf '%s\\n' \"$!\" > {q('helper')}\n"
                        f"    while [ ! -e {q('release')} ]; do :; done\n"
                        f"    {field}=$!"
                    )
                    body = timer_source.replace(marker, gate)
                    acknowledge = f"printf '1\\n' > {q('signalled')}"
                    body = body.replace(
                        "cancel_timer() {", "cancel_timer() {\n      " + acknowledge
                    )
                    if immediate_exit:
                        body = body.replace(
                            "trap cancel_timer HUP INT TERM",
                            "trap "
                            + shlex.quote(acknowledge + "; exit 0")
                            + " HUP INT TERM",
                        )
                    script = work / "probe.sh"
                    # 外层保持存活，确保 finally 回收的是仍由本 Popen 持有的组。
                    script.write_text(
                        f"dir={shlex.quote(str(work))}\nbudget=0\n"
                        "/bin/sleep 30 & child=$!\n(\n" + body + "\n) & timer=$!\n"
                        f"printf '%s\\n' \"$timer\" > {q('timer')}\n"
                        'wait "$timer" 2>/dev/null; status=$?\n'
                        f"printf '%s\\n' \"$status\" > {q('done')}\n"
                        "while :; do /bin/sleep 1; done\n"
                    )
                    process = subprocess.Popen(
                        ["/bin/sh", str(script)],
                        start_new_session=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    try:

                        def wait_file(name):
                            deadline = time.monotonic() + 3
                            path = work / name
                            while (
                                not path.exists() or not path.read_text().strip()
                            ) and time.monotonic() < deadline:
                                self.assertIsNone(
                                    process.poll(),
                                    "probe exited before synchronization",
                                )
                                time.sleep(0.005)
                            self.assertTrue(
                                path.exists() and path.read_text().strip(), name
                            )
                            return int(path.read_text())

                        helper = wait_file("helper")
                        timer = wait_file("timer")
                        os.kill(timer, signal.SIGTERM)
                        self.assertEqual(wait_file("signalled"), 1)
                        (work / "release").touch()
                        self.assertEqual(wait_file("done"), 0)
                        if immediate_exit:
                            os.kill(helper, 0)  # 旧 handler 的反例：helper 仍存活。
                        else:
                            with self.assertRaises(ProcessLookupError):
                                os.kill(helper, 0)
                    finally:
                        if process.poll() is None:
                            # 未 wait/reap 组长前一次回收本探针组，不信号未知 PID。
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=3)

    def test_each_stream_bounded_during_capture(self):
        directories, sleepers = self.trace_helpers()
        for stream in ("stdout", "stderr"):
            ready = self.root / "ready"
            ready.unlink(missing_ok=True)
            target = 2 if stream == "stderr" else 1
            code = (
                "import os, signal, time; signal.signal(signal.SIGXFSZ, signal.SIG_IGN); "
                f"fd={target}; "
                '\ntry:\n os.write(fd, b"x" * 2097152)\nexcept OSError:\n pass\n'
                f'open({str(ready)!r}, "w").close(); time.sleep(0.5)'
            )
            self.stub(
                "docker", f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}\n"
            )
            self.write_shell("lf_docker info --format fixed")
            process = subprocess.Popen(
                ["/bin/sh", str(self.script), str(SOURCE)],
                env=self.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), stream)
                active_dir = pathlib.Path(directories.read_text().splitlines()[-1])
                self.assertEqual(active_dir.stat().st_mode & 0o777, 0o700)
                for name in ("stdout", "stderr"):
                    file = active_dir / name
                    self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                    self.assertLessEqual(file.stat().st_size, 1048576, (stream, name))
                stdout, stderr = process.communicate(timeout=3)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)
            self.assertNotEqual(process.returncode, 0, stream)
            self.assertEqual(stdout + stderr, b"")
        self.assert_helpers_reaped(directories, sleepers)

    def test_timeout_and_term_ignoring_child(self):
        directories, sleepers = self.trace_helpers()
        pidfile = self.root / "pid"
        self.stub(
            "docker",
            f'trap "" TERM\nprintf "%s\\n" "$$" > {shlex.quote(str(pidfile))}\nwhile :; do :; done\n',
        )
        started = time.monotonic()
        result = self.run_shell("lf_docker_run 1 docker info", timeout=5, env={})
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(time.monotonic() - started, 4)
        self.assertEqual(result.stdout + result.stderr, b"")
        pid = int(pidfile.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assert_helpers_reaped(directories, sleepers)

    def test_context_inspect_uses_runner_and_rejects_failure(self):
        log = self.root / "commands"
        state = self.root / "context-fixture.json"
        code = f'import json; print(json.load(open({str(state)!r}))["endpoint"])'
        self.stub(
            "docker",
            f'printf "context %s\\n" "$*" >> {shlex.quote(str(log))}\n'
            f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}\n",
        )
        state.write_text('{"endpoint":"unix:///synthetic.sock"}')
        result = self.run_shell(
            'unset LF_DOCKER_ENDPOINT\nlf_docker_endpoint && printf "%s\\n" "$LF_DOCKER_ENDPOINT"',
            env={"DOCKER_CONTEXT": "safe"},
            real_endpoint=True,
        )
        self.assertEqual(
            (result.returncode, result.stdout, result.stderr),
            (0, b"unix:///synthetic.sock\n", b""),
        )
        self.assertIn("context inspect", log.read_text())
        self.stub("docker", 'printf "secret\\n"; exit 7\n')
        result = self.run_shell(
            "unset LF_DOCKER_ENDPOINT\nlf_docker_endpoint",
            env={"DOCKER_CONTEXT": "safe"},
            real_endpoint=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, b"")

    def test_timer_failure_terminates_its_cli_instead_of_removing_deadline(self):
        directories, sleepers = self.trace_helpers()
        pidfile = self.root / "pid"
        observed = self.root / "timer-observed-pid"
        # 工具故障只能在 CLI 确已启动后注入；固定等待不能证明 PID 已登记。
        self.stub(
            "sleep",
            f'printf "%s:%s\\n" "$$" "$1" >> {shlex.quote(str(sleepers))}\n'
            "attempts=0\n"
            f'while [ ! -s {shlex.quote(str(pidfile))} ] && [ "$attempts" -lt 100 ]; do\n'
            "  /bin/sleep 0.005; attempts=$((attempts + 1))\ndone\n"
            f"[ -s {shlex.quote(str(pidfile))} ] || exit 9\n"
            f"read -r child_pid < {shlex.quote(str(pidfile))}\n"
            f'printf "%s\\n" "$child_pid" > {shlex.quote(str(observed))}\n'
            'printf "private timer failure\\n" >&2\nexit 7\n',
        )
        self.stub(
            "docker",
            f'trap "" TERM\n/bin/sleep 0.2\nprintf "%s\\n" "$$" > {shlex.quote(str(pidfile))}\n'
            "while :; do :; done\n",
        )
        result = self.run_shell("lf_docker_run 30 docker info", timeout=3, env={})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout + result.stderr, b"")
        self.assertTrue(
            observed.exists(), "timer failure injected before CLI handshake"
        )
        self.assertEqual(observed.read_text(), pidfile.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
        self.assert_helpers_reaped(directories, sleepers)

    def test_signal_cancels_runner_and_its_cli(self):
        directories, sleepers = self.trace_helpers()
        pidfile = self.root / "pid"
        for signal_name in ("HUP", "INT", "TERM"):
            pidfile.unlink(missing_ok=True)
            self.stub(
                "docker",
                f'printf "%s\\n" "$$" > {shlex.quote(str(pidfile))}\n'
                'trap "" TERM\n/bin/sleep 0.1\n'
                f'kill -s {shlex.quote(signal_name)} "$PPID"\nwhile :; do :; done\n',
            )
            result = self.run_shell(
                'lf_docker_run 30 docker info || exit $?\nprintf "continued\\n"',
                timeout=4,
                env={},
            )
            self.assertNotEqual(result.returncode, 0, signal_name)
            self.assertNotIn(b"continued", result.stdout)
            self.assertEqual(result.stdout + result.stderr, b"")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pidfile.read_text()), 0)
        self.assert_helpers_reaped(directories, sleepers)

    def test_fixed_budget_and_no_inherited_internal_controls(self):
        key = "c" * 64
        (self.root / "releases" / key).mkdir(parents=True)
        binding = self.root / "engine"
        binding.write_text(
            "schema=lexiflow-engine-v1\nendpoint=unix:///synthetic.sock\ndaemon_id=synthetic-daemon-id\n"
        )
        binding.chmod(0o600)
        budget_log = self.root / "budgets"
        result = self.run_shell(
            'lf_docker_run() { printf "%s\\n" "$1" >> "$BUDGET_LOG"; }\n'
            "lf_lock_check() { :; }\n"
            'lf_docker_server_kind() { printf "docker\\n"; }\n'
            "lf_docker_identity_check() { :; }\n"
            "lf_docker_identity_query() { printf 'synthetic-daemon-id\\n'; }\n"
            'lf_docker info --format fixed\nlf_compose run --rm --no-deps --name "${LF_PROJECT}-initialize-1" initialize\n'
            "lf_compose up -d api",
            env={
                "LF_ROOT": str(self.root),
                "LF_CURRENT_KEY": key,
                "LF_RELEASE_ROOT": str(self.root),
                "LF_COMPOSE_PATH": "compose.yaml",
                "LF_INSTALL_ID": "d" * 32,
                "LF_PLATFORM": "linux/amd64",
                "LF_API_IMAGE": "sha256:" + "e" * 64,
                "LF_POSTGRES_IMAGE": "sha256:" + "f" * 64,
                "LF_DATASET_SHA256": "1" * 64,
                "LF_PROJECT": "lf_" + "d" * 32 + "_" + "b" * 16,
                "BUDGET_LOG": str(budget_log),
                "DOCKER_HOST": "unix:///synthetic.sock",
                "DOCKER_CONTEXT": "",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(budget_log.read_bytes().splitlines(), [b"30", b"600", b"30"])
        self.assertFalse(list(self.tmp.iterdir()))


if __name__ == "__main__":
    unittest.main()
