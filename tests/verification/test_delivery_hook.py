"""交付 Hook 的真实 Verify 夹具、双客户端协议与资源安全回归。"""

from __future__ import annotations

import contextlib
import fcntl
import io
import json
import os
import pty
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.triggers import run_stop_checks as hook
from tests.verification.test_fixture_e2e import _init_git_repo
from tests.verification.declaration_fixtures import write_declarations

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "scripts/triggers/run_stop_checks.py"
EVENT = {"hook_event_name": "Stop", "stop_hook_active": False}
MARKER = "<!-- lexiflow:intermediate -->"
COMPLETE_OUTPUT = '\nprint(\'{"status":"PASS","checks_run":1,"failures":0,"errors":0,"skipped":0,"reason":""}\')\n'


class DeliveryHookTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in ("config/engineering/risk.yaml", "scripts/verification/planning/change_risk_assessment.py"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        patch = mock.patch.dict(os.environ, {"LEXIFLOW_DELIVERY_HOOK_DISABLE": "0"})
        patch.start()
        self.addCleanup(patch.stop)

    def evaluate(self, event=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return hook.evaluate(self.root, event or EVENT)

    def fixture(self, body="pass", environment=None):
        _init_git_repo(self.root)
        (self.root / ".gitignore").write_text("config/\nscripts/\ntmp/\nfixture/\n")
        (self.root / "fixture").mkdir()
        (self.root / "fixture/check.py").write_text(body + COMPLETE_OUTPUT)
        (self.root / "initial.txt").write_text("changed")
        checks = []
        for scope in ("change-targeted", "repository-baseline"):
            checks.append(
                {
                    "check_id": "fixture." + scope,
                    "command": ["python3", "fixture/check.py"],
                    "executable": "python3",
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "transaction_reuse": True,
                    "scope": scope,
                    "triggers": [{"path": "initial.txt"}, {"path": ".gitignore"}],
                    "input_paths": ["fixture/check.py", "initial.txt"],
                    "required_environment": environment or [],
                }
            )
        self.write_checks(checks)

    def write_checks(self, checks):
        formal = [
            {
                "check_id": identifier,
                "scope": "repository-baseline",
                "command": [
                    sys.executable,
                    "-c",
                    "raise SystemExit('formal must not execute')",
                ],
                "cwd": ".",
                "timeout_seconds": 10,
                "triggers": [{"path": "formal-input"}],
                "input_paths": ["initial.txt"],
            }
            for identifier in ("fixture.formal",)
        ]
        # 同一规范 Check 在两个场景间共享唯一操作，不产生 alias。
        formal = [formal[0]]
        formal[0]["scenarios"] = ["repository-baseline", "change-targeted"]
        formal[0]["triggers"] = [{"path": "formal-input"}]
        write_declarations(
            self.root / "config/engineering/module-checks.yaml",
            [*checks, *formal],
            ci_quick={
                "always_required_check_ids": [],
                "formal_only_check_ids": ["fixture.formal"],
            },
        )

    def reports(self):
        return [
            json.loads(p.read_text())
            for p in (self.root / "tmp/quality/verification-reports").glob("*.json")
        ]

    def test_real_checks_and_one_daily_report_are_required_for_pass(self):
        self.fixture(
            "from pathlib import Path\np=Path('tmp/count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
        )
        output = self.evaluate()
        self.assertNotIn("decision", output)
        self.assertIn("交付 PASS", output["systemMessage"])
        self.assertEqual("x", (self.root / "tmp/count").read_text())
        self.assertEqual(1, len(self.reports()))
        self.assertEqual("development-change", self.reports()[0]["scope"])
        self.assertTrue(all(r["result"] == "PASS" for r in self.reports()))

    def test_same_input_is_not_granted_an_old_cached_pass(self):
        self.fixture(
            "from pathlib import Path\np=Path('tmp/count'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"
        )
        self.evaluate()
        original = self.reports()[0]
        self.evaluate()
        self.assertEqual("xx", (self.root / "tmp/count").read_text())
        reports = self.reports()
        self.assertEqual(2, len(reports))
        self.assertEqual(2, len({report["run_id"] for report in reports}))
        self.assertTrue(
            all(report["scope"] == "development-change" for report in reports)
        )
        self.assertIn(original, reports)

    def test_failed_check_blocks_and_does_not_execute_repository(self):
        self.fixture("raise SystemExit(1)")
        output = self.evaluate()
        self.assertEqual("block", output["decision"])
        self.assertIn("fixture.change-targeted FAIL", output["reason"])
        self.assertIn("诊断日志", output["reason"])
        self.assertEqual(1, len(self.reports()))
        self.assertEqual("development-change", self.reports()[0]["scope"])

    def test_reentry_failure_ends_loop_without_granting_pass(self):
        self.fixture("raise SystemExit(1)")
        output = self.evaluate({**EVENT, "stop_hook_active": True})
        self.assertIs(False, output["continue"])
        self.assertNotIn("decision", output)
        self.assertIn("交付 FAIL", output["stopReason"])

    def test_reentry_repair_is_really_verified(self):
        self.fixture("raise SystemExit(1)")
        self.assertEqual("block", self.evaluate()["decision"])
        (self.root / "fixture/check.py").write_text("pass" + COMPLETE_OUTPUT)
        output = self.evaluate({**EVENT, "stop_hook_active": True})
        self.assertIn("交付 PASS", output["systemMessage"])
        self.assertEqual(2, len(self.reports()))
        self.assertEqual(
            {"FAIL", "PASS"}, {report["result"] for report in self.reports()}
        )
        self.assertTrue(
            all(report["scope"] == "development-change" for report in self.reports())
        )

    def test_missing_environment_is_blocked_not_pass(self):
        self.fixture(environment=["nonexistent-lexiflow-hook-test"])
        output = self.evaluate()
        self.assertIn("BLOCKED", output["reason"])
        self.assertIn("nonexistent-lexiflow-hook-test", output["reason"])
        self.assertEqual("block", output["decision"])

    def test_input_drift_cannot_pass(self):
        self.fixture(
            "from pathlib import Path\nPath('initial.txt').write_text('drift')"
        )
        output = self.evaluate()
        self.assertIn("input-drift", output["reason"])
        self.assertIn("执行后 initial.txt", output["reason"])
        self.assertIn("请停止源码/配置写入后重新验证", output["reason"])

    def test_drift_diagnostics_use_recorded_snapshots_not_live_files_or_pass_logs(self):
        def snapshot(files):
            return {"files": [{"locator": p, "sha256": h} for p, h in files.items()]}

        original = snapshot({"changed.py": "a" * 64, "removed.py": "b" * 64})
        final = snapshot({"changed.py": "c" * 64, "added.py": "d" * 64})
        report = {
            "reason": "input-drift",
            "checks": [
                {
                    "check_id": "fixture",
                    "status": "FAIL",
                    "reason": "input-drift",
                    "input_snapshot": {
                        "pre": original,
                        "post": original,
                        "final": final,
                    },
                    "result_contract": {"report": {"detail": "TEST PASSED"}},
                }
            ],
        }
        detail = hook.report_detail(report, self.root)
        self.assertIn("收尾复核 changed.py：aaaaaaaaaaaa → cccccccccccc", detail)
        self.assertIn("收尾复核 removed.py：bbbbbbbbbbbb → 不存在", detail)
        self.assertIn("收尾复核 added.py：不存在 → dddddddddddd", detail)
        self.assertNotIn("执行后", detail)
        self.assertNotIn("TEST PASSED", detail)

    @mock.patch.object(hook, "execute_delivery")
    def test_only_explicit_intermediate_tail_and_non_stop_skip_execution(self, execute):
        self.fixture()
        for event in (
            {"hook_event_name": "PostToolUse"},
            {**EVENT, "last_assistant_message": "等待确认\n" + MARKER},
            {**EVENT, "last_assistant_message": MARKER},
        ):
            self.evaluate(event)
        execute.assert_not_called()
        execute.return_value = ("PASS", "test")
        self.evaluate({**EVENT, "last_assistant_message": MARKER + "\n完成交付"})
        execute.assert_called_once()

    @mock.patch.object(hook, "execute_delivery")
    def test_explicit_disable_is_not_pass(self, execute):
        with mock.patch.dict(os.environ, {"LEXIFLOW_DELIVERY_HOOK_DISABLE": "1"}):
            self.assertIn("未验证", self.evaluate()["systemMessage"])
        execute.assert_not_called()

    @mock.patch.object(hook, "_execute_delivery")
    def test_busy_lock_never_waits_or_runs_twice(self, execute):
        self.fixture()
        state = self.root / "tmp/quality/verification-window"
        state.mkdir(parents=True)
        with (state / "execution.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            start = time.monotonic()
            output = self.evaluate()
        self.assertLess(time.monotonic() - start, 1)
        self.assertFalse(output["continue"])
        self.assertIn("verification-window-busy", output["stopReason"])
        execute.assert_not_called()

    def test_global_timeout_cleans_real_process_and_releases_lock(self):
        self.fixture(
            "import os,time\nfrom pathlib import Path\nPath('tmp/child.pid').write_text(str(os.getpid()))\ntime.sleep(60)"
        )
        # 给真实预检与进程启动留出余量；总预算仍短于单项 10 秒及子进程 60 秒。
        with mock.patch.object(hook, "DELIVERY_TIMEOUT_SECONDS", 5):
            output = self.evaluate()
        self.assertIn("总执行超过 5 秒", output["stopReason"])
        pid = int((self.root / "tmp/child.pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with (self.root / "tmp/quality/verification-window/execution.lock").open(
            "a"
        ) as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_stop_limits_are_fixed_without_configuration_input(self):
        self.assertEqual(1800, hook.DELIVERY_TIMEOUT_SECONDS)
        self.assertEqual(30, hook.HEARTBEAT_SECONDS)
        self.assertFalse(hasattr(hook, "read_policy"))
        self.assertFalse(hasattr(hook, "POLICY_PATH"))

    def test_heartbeat_uses_fixed_interval_without_wall_clock_wait(self):
        stream = io.StringIO()
        event = {
            "event": "heartbeat",
            "argv": ["python3", "fixture.py"],
            "elapsed_seconds": 0,
        }
        with (
            contextlib.redirect_stderr(stream),
            mock.patch.object(
                hook.time, "monotonic", side_effect=[100, 129, 130, 130, 159, 160, 160]
            ),
        ):
            observe = hook.progress_observer()
            observe({"event": "started", "argv": event["argv"], "timeout_seconds": 180})
            for elapsed in (29, 30, 59, 60):
                observe({**event, "elapsed_seconds": elapsed})
        self.assertEqual(2, stream.getvalue().count("仍在执行"))
        self.assertIn("已用 30s", stream.getvalue())
        self.assertIn("已用 60s", stream.getvalue())

    def test_heartbeat_is_stderr_and_immediate(self):
        stream = io.StringIO()
        with (
            contextlib.redirect_stderr(stream),
            mock.patch.object(hook, "HEARTBEAT_SECONDS", 1),
        ):
            from scripts.verification.execution.check_runner import run_check_process

            result = run_check_process(
                [sys.executable, "-c", "import time;time.sleep(1.1)"],
                str(self.root),
                {},
                5,
                progress_observer=hook.progress_observer(),
            )
        self.assertEqual(0, result["exit_code"])
        self.assertIn("开始", stream.getvalue())
        self.assertIn("仍在执行", stream.getvalue())
        self.assertIn("命令结束：", stream.getvalue())
        self.assertNotIn("PASS", stream.getvalue())

    def test_process_outcomes_are_explicit_and_do_not_grant_gate_pass(self):
        for code, reason in (
            (0, "exited"),
            (1, "exited"),
            (-15, "exited"),
            (-9, "timeout"),
            (None, "spawn-error"),
        ):
            with self.subTest(reason=reason, code=code):
                stream = io.StringIO()
                event = {
                    "event": "finished",
                    "argv": ["python3", "fixture.py"],
                    "exit_code": code,
                    "exit_reason": reason,
                }
                with contextlib.redirect_stderr(stream):
                    result = hook.progress_observer()(event)
                self.assertIsNone(result)
                self.assertIn(f"({reason}, exit={code})", stream.getvalue())
                self.assertIn("判定待校验", stream.getvalue())
                self.assertNotIn("PASS", stream.getvalue())
                self.assertEqual(
                    {"event", "argv", "exit_code", "exit_reason"}, set(event)
                )

    def test_terminal_never_waits_for_eof(self):
        master, slave = pty.openpty()
        try:
            p = subprocess.run(
                [sys.executable, str(ENTRY)],
                stdin=slave,
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(0, p.returncode)
            self.assertIn("交互终端", json.loads(p.stdout)["stopReason"])
        finally:
            os.close(master)
            os.close(slave)

    def test_open_pipe_without_eof_has_a_deadline(self):
        p = subprocess.Popen(
            [sys.executable, str(ENTRY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            p.stdin.write(json.dumps(EVENT))
            p.stdin.flush()
            p.wait(timeout=6)
            self.assertIn("3 秒未收到 EOF", json.loads(p.stdout.read())["stopReason"])
        finally:
            if p.poll() is None:
                p.kill()
                p.wait()
            p.stdin.close()
            p.stdout.close()
            p.stderr.close()

    def test_invalid_input_does_not_echo_payload(self):
        for raw in ("secret-canary", "[]", "x" * 65537):
            p = subprocess.run(
                [sys.executable, str(ENTRY)],
                input=raw,
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(0, p.returncode)
            self.assertNotIn("secret-canary", p.stdout)
            self.assertFalse(json.loads(p.stdout)["continue"])

    def test_clients_share_thin_entry_and_only_stop_event(self):
        configs = [
            json.loads((ROOT / name).read_text())
            for name in (".codex/hooks.json", ".qoder/settings.json")
        ]
        self.assertEqual(
            configs[0]["hooks"]["Stop"][0]["hooks"][0]["command"],
            configs[1]["hooks"]["Stop"][0]["hooks"][0]["command"].replace(
                " --client qoder", ""
            ),
        )
        for config in configs:
            self.assertEqual(["Stop"], list(config["hooks"]))
            handler = config["hooks"]["Stop"][0]["hooks"][0]
            self.assertEqual(1860, handler["timeout"])
            self.assertNotIn("rc=", handler["command"])
            for cwd in (ROOT, ROOT / "scripts"):
                p = subprocess.run(
                    handler["command"],
                    shell=True,
                    cwd=cwd,
                    input=json.dumps({**EVENT, "last_assistant_message": MARKER}),
                    text=True,
                    capture_output=True,
                    timeout=5,
                )
                self.assertEqual(0, p.returncode, p.stderr)
                self.assertIn("中间回合", json.loads(p.stdout)["systemMessage"])
            p = subprocess.run(
                handler["command"],
                shell=True,
                cwd=self.root,
                input="{}",
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(1, p.returncode)
            self.assertTrue(p.stderr)

    def test_qoder_protocol_uses_deny_not_codex_block(self):
        with (
            mock.patch.object(hook, "read_event", return_value=EVENT),
            mock.patch.object(
                hook, "evaluate", return_value=hook.decision("FAIL", "fixture")
            ),
            mock.patch.object(sys, "argv", ["hook", "--client", "qoder"]),
        ):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                hook.main()
            self.assertEqual("deny", json.loads(stream.getvalue())["decision"])

    def test_clean_checkout_does_not_start_checks(self):
        _init_git_repo(self.root)
        with mock.patch.object(hook, "execute_delivery") as execute:
            self.assertIn("无待检变更", self.evaluate()["systemMessage"])
            execute.assert_not_called()

    def test_unknown_client_is_a_terminal_diagnostic(self):
        p = subprocess.run(
            [sys.executable, str(ENTRY), "--client", "typo"],
            input="{}",
            text=True,
            capture_output=True,
            timeout=5,
        )
        output = json.loads(p.stdout)
        self.assertFalse(output["continue"])
        self.assertIn("--client codex|qoder", output["stopReason"])

    def test_real_ruff_failure_reaches_stop_block(self):
        # 在独立 Git 夹具运行真实已安装 Ruff，不改用户仓库、不替换执行内核。
        self.fixture()
        script = self.root / "fixture/check.py"
        script.write_text("import os\n")
        self.write_checks(
            [
                {
                    "check_id": "fixture.ruff." + scope,
                    "command": [
                        sys.executable,
                        "-m",
                        "ruff",
                        "check",
                        "--isolated",
                        "--select",
                        "F401",
                        "fixture/check.py",
                    ],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": scope,
                    "triggers": [{"path": "initial.txt"}, {"path": ".gitignore"}],
                    "input_paths": ["fixture/check.py"],
                }
                for scope in ("change-targeted", "repository-baseline")
            ],
        )
        output = self.evaluate()
        self.assertEqual("block", output["decision"])
        self.assertIn("F401", output["reason"])
        report = self.reports()[0]
        stdout = report["checks"][0]["process"]["output_artifacts"]["stdout"]["locator"]
        self.assertIn("F401", (self.root / stdout).read_text())


if __name__ == "__main__":
    unittest.main()
