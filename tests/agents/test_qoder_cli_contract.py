"""Qoder CLI 的证据参数与父 Session 来源边界。"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.agents.qoder import runner as qoder_task
from scripts.agents.delegation import qoder_cli
from scripts.agents.delegation.codex_cli import main as work_package_main
from scripts.agents.local_codex_runtime import CodexRuntimeError


RUN_ID = "11111111-2222-4333-8444-555555555555"
PARENT_ID = "22222222-3333-4444-8555-666666666666"


class QoderCliContractTest(unittest.TestCase):
    def test_disabled_actions_reject_before_policy_or_input_and_have_no_parameters(
        self,
    ):
        from scripts.agents.qoder.cli import parse_command

        for command in ("start", "resume", "preflight"):
            with (
                self.subTest(command=command),
                patch.object(qoder_task, "_find_repo_root") as root,
                patch.object(
                    qoder_task._fallback, "_policy", return_value={"primary": "qoder"}
                ) as policy,
                patch.object(qoder_task.subprocess, "Popen") as spawn,
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(qoder_task.main([command]), 2)
                self.assertEqual(
                    qoder_task.main(
                        [command, "--task", "/missing", "--runtime-recovery-confirmed"]
                    ),
                    2,
                )
                self.assertIn(
                    "disabled; use native Codex collaboration", errors.getvalue()
                )
                root.assert_not_called()
                policy.assert_not_called()
                spawn.assert_not_called()
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                parse_command([command], {})
            self.assertEqual(raised.exception.code, 2)

    def test_help_lists_only_historical_actions_and_evidence_parameters(self):
        with (
            contextlib.redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as raised,
        ):
            qoder_task.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn(
            "status,result,validate-result,ack,record-fallback", output.getvalue()
        )
        for removed in (
            "--followup",
            "--runtime-recovery-confirmed",
            "preflight",
            "resume",
            "start",
        ):
            self.assertNotIn(removed, output.getvalue())

    def test_public_delegation_entry_forwards_only_explicit_argv(self):
        with patch.object(qoder_cli, "run_command", return_value=0) as run:
            self.assertEqual(qoder_cli.main(["status", RUN_ID]), 0)
        run.assert_called_once_with(["status", RUN_ID])

    def test_ack_uses_real_parent_environment_not_caller_option(self):
        with (
            patch.object(qoder_task, "_find_repo_root", return_value=Path("/repo")),
            patch.object(
                qoder_task, "_task_dir", return_value=Path("/repo/tmp/qoder-tasks")
            ),
            patch.object(
                qoder_task,
                "discover_codex_runtime",
                return_value=SimpleNamespace(context={"parent_session_id": PARENT_ID}),
            ) as discover,
            patch.object(
                qoder_task._lifecycle,
                "ack_run",
                return_value={"status": "acknowledged"},
            ) as ack,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(qoder_task.main(["ack", RUN_ID]), 0)
        discover.assert_called_once_with(Path("/repo"))
        ack.assert_called_once_with(
            Path("/repo/tmp/qoder-tasks"), RUN_ID, PARENT_ID, codex_thread_id=PARENT_ID
        )

    def test_ack_rejects_removed_parent_override(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            qoder_task.main(["ack", RUN_ID, "--parent-session-id", PARENT_ID])

    def test_ack_without_trusted_runtime_does_not_consume_run(self):
        with (
            patch.object(qoder_task, "_find_repo_root", return_value=Path("/repo")),
            patch.object(
                qoder_task, "_task_dir", return_value=Path("/repo/tmp/qoder-tasks")
            ),
            patch.object(
                qoder_task,
                "discover_codex_runtime",
                side_effect=CodexRuntimeError(
                    "runtime-metadata-unavailable", "missing"
                ),
            ),
            patch.object(qoder_task._lifecycle, "ack_run") as ack,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as raised:
                qoder_task.main(["ack", RUN_ID])
            self.assertEqual(raised.exception.code, 1)
        ack.assert_not_called()

    def test_work_package_verify_rejects_root_override(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            work_package_main(["verify", "--run-id", RUN_ID, "--root", "."])


if __name__ == "__main__":
    unittest.main()
