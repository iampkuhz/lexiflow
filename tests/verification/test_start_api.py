"""Direct safety tests for interactive local API startup."""
from __future__ import annotations

import contextlib
import io
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

from scripts.environment import start_api as api


class PortPreparationTest(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.pids = self.stack.enter_context(patch.object(api, "listener_pids", return_value={42}))
        self.identity = self.stack.enter_context(patch.object(api, "process_identity", return_value="start uid java"))
        self.kill = self.stack.enter_context(patch.object(api.os, "kill"))
        self.bindable = self.stack.enter_context(patch.object(api, "assert_bindable"))
        self.stack.enter_context(patch.object(api.sys.stdin, "isatty", return_value=True))
        self.answer = self.stack.enter_context(patch("builtins.input", return_value="y"))

    def test_free_port_does_not_prompt_or_kill(self):
        self.pids.return_value = set()
        api.prepare_port(18080)
        self.answer.assert_not_called()
        self.kill.assert_not_called()
        self.bindable.assert_called_once_with(18080)

    def test_yes_signals_only_confirmed_listeners_then_waits_for_release(self):
        self.pids.side_effect = [{42}, {42}, {42}, set()]
        api.prepare_port(18080)
        self.kill.assert_called_once_with(42, signal.SIGTERM)
        self.bindable.assert_called_once_with(18080)

    def test_multiple_listener_pids_are_all_confirmed(self):
        self.answer.return_value = " YES "
        self.pids.side_effect = [{42, 43}] * 4 + [set()]
        api.prepare_port(18080)
        self.assertEqual([call.args for call in self.kill.call_args_list], [(42, signal.SIGTERM), (43, signal.SIGTERM)])

    def test_no_blank_and_unrecognized_answers_never_kill(self):
        for answer in ["", "n", "no", "sure"]:
            with self.subTest(answer=answer):
                self.answer.return_value = answer
                with self.assertRaises(api.StartupBlocked):
                    api.prepare_port(18080)
        self.kill.assert_not_called()

    def test_eof_does_not_kill(self):
        self.answer.side_effect = EOFError
        with self.assertRaises(api.StartupBlocked):
            api.prepare_port(18080)
        self.kill.assert_not_called()

    def test_noninteractive_does_not_read_piped_yes_or_kill(self):
        with patch.object(api.sys.stdin, "isatty", return_value=False):
            with self.assertRaises(api.StartupBlocked):
                api.prepare_port(18080)
        self.answer.assert_not_called()
        self.kill.assert_not_called()

    def test_new_listener_during_confirmation_blocks(self):
        self.pids.side_effect = [{42}, {43}]
        with self.assertRaises(api.StartupBlocked):
            api.prepare_port(18080)
        self.kill.assert_not_called()

    def test_reused_pid_blocks(self):
        self.identity.side_effect = ["old", "new"]
        with self.assertRaises(api.StartupBlocked):
            api.prepare_port(18080)
        self.kill.assert_not_called()

    def test_listener_that_exits_during_confirmation_is_not_killed(self):
        self.pids.side_effect = [{42}, set(), set()]
        api.prepare_port(18080)
        self.kill.assert_not_called()

    def test_timeout_never_escalates_to_sigkill(self):
        with patch.object(api.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaisesRegex(api.StartupBlocked, "10 秒"):
                api.prepare_port(18080)
        self.kill.assert_called_once_with(42, signal.SIGTERM)
        self.bindable.assert_not_called()

    def test_permission_denied_does_not_start(self):
        self.kill.side_effect = PermissionError
        with self.assertRaises(api.StartupBlocked):
            api.prepare_port(18080)
        self.bindable.assert_not_called()

    def test_new_listener_after_signal_is_not_killed(self):
        self.pids.side_effect = [{42}, {42}, {42}, {43}]
        with self.assertRaises(api.StartupBlocked):
            api.prepare_port(18080)
        self.kill.assert_called_once_with(42, signal.SIGTERM)


class DiscoveryAndLaunchTest(unittest.TestCase):
    def test_startup_does_not_require_model_configuration(self):
        with patch.object(api, "command_environment", return_value={}), patch.object(api, "prepare_port"), patch.object(api.os, "execvpe"), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(api.main([]), 0)
        self.assertNotIn("模型", output.getvalue())

    def test_lsof_selects_only_tcp_listeners(self):
        with patch.object(api.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "42\n42\n43\n", "")) as run:
            self.assertEqual(api.listener_pids(18080), {42, 43})
        self.assertIn("-sTCP:LISTEN", run.call_args.args[0])
        self.assertNotIn("args", run.call_args.args[0])

    def test_discovery_errors_and_malformed_results_fail_closed(self):
        for code, output, err in [(1, "", "permission denied"), (2, "", ""), (0, "unknown\n", ""), (0, "0\n", "")]:
            with self.subTest(code=code, output=output):
                with patch.object(api.subprocess, "run", return_value=subprocess.CompletedProcess([], code, output, err)):
                    with self.assertRaises(api.StartupBlocked):
                        api.listener_pids(18080)

    def test_empty_lsof_result_is_not_an_error(self):
        with patch.object(api.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            self.assertEqual(api.listener_pids(18080), set())

    def test_unknown_occupied_port_cannot_be_treated_as_free(self):
        socket = Mock()
        socket.bind.side_effect = OSError("occupied")
        with patch.object(api.socket, "socket") as factory:
            factory.return_value.__enter__.return_value = socket
            with self.assertRaises(api.StartupBlocked):
                api.assert_bindable(18080)

    def test_launcher_uses_checked_port_and_passes_database_via_environment(self):
        with patch.object(api, "command_environment", return_value={"MODEL_SETTING": "retained"}), patch.object(api, "prepare_port") as prepare, patch.object(api.os, "execvpe") as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(api.main(["--port", "18081", "--database-url", "jdbc:test"]), 0)
        prepare.assert_called_once_with(18081)
        _, command, environment = execute.call_args.args
        self.assertIn("--args=--server.address=127.0.0.1 --server.port=18081", command)
        self.assertEqual(environment["SPRING_DATASOURCE_URL"], "jdbc:test")
        self.assertEqual(environment["MODEL_SETTING"], "retained")
        self.assertNotIn("jdbc:test", " ".join(command))

    def test_invalid_java_does_not_touch_existing_listener(self):
        with patch.object(api, "command_environment", side_effect=api.JavaRuntimeError("missing Java")), patch.object(api, "prepare_port") as prepare, patch.object(api.os, "execvpe") as execute, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(api.main([]), 2)
        prepare.assert_not_called()
        execute.assert_not_called()

    def test_port_blocked_or_cancelled_never_launches_gradle(self):
        for error, code in [(api.StartupBlocked("busy"), 2), (KeyboardInterrupt(), 130), (FileNotFoundError("lsof"), 2)]:
            with self.subTest(error=type(error).__name__):
                with patch.object(api, "command_environment", return_value={}), patch.object(api, "prepare_port", side_effect=error), patch.object(api.os, "execvpe") as execute, contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(api.main([]), code)
                execute.assert_not_called()
