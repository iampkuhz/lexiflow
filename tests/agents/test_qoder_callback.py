"""隔离 fixture 校验历史 callback 身份、单次认领和 watchdog 异常通知。"""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agents.qoder import callback, lifecycle, runner
from tests.agents.test_lifecycle_records import RUN_ID, bound_task


class QoderCallbackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / RUN_ID
        self.run.mkdir()
        self.task = bound_task()
        self.completion = runner._build_completion(
            self.task, "finished", 0, run_id=RUN_ID
        )

    def test_directory_run_is_checked_against_task_completion_and_package_before_claim(
        self,
    ):
        for target, field in (
            ("task", "run_id"),
            ("completion", "run_id"),
            ("completion", "task_version"),
            ("completion", "work_package_id"),
            ("completion", "task_versions"),
        ):
            task, completion = copy.deepcopy(self.task), copy.deepcopy(self.completion)
            (task if target == "task" else completion)[field] = "conflicting-fixture"
            with (
                self.subTest(target=target, field=field),
                patch.object(callback, "_run_codex_queue_cli") as queue,
            ):
                with self.assertRaises(ValueError):
                    callback.attempt_codex_callback(
                        self.run, completion, task, self.root
                    )
                queue.assert_not_called()
                self.assertFalse(lifecycle.callback_claim_path(self.run).exists())
                self.assertFalse(lifecycle.callback_file_path(self.run).exists())

    def test_exact_receipt_is_queued_once_and_uses_directory_identity(self):
        receipt = f"Queued message 33333333-3333-4333-8333-333333333333 for thread {self.task['parent_session_id']}."
        with patch.object(
            callback,
            "_run_codex_queue_cli",
            return_value=subprocess.CompletedProcess([], 0, receipt, ""),
        ) as queue:
            callback.attempt_codex_callback(
                self.run, self.completion, self.task, self.root
            )
            callback.attempt_codex_callback(
                self.run, self.completion, self.task, self.root
            )
        queue.assert_called_once()
        self.assertIn(f"run_id={self.run.name}", queue.call_args.args[1])
        self.assertEqual(queue.call_args.args[0], self.task["parent_session_id"])
        self.assertEqual(queue.call_args.kwargs["cwd"], str(self.root))
        self.assertEqual(
            json.loads(lifecycle.callback_file_path(self.run).read_text())["status"],
            "queued",
        )

    def test_unverified_receipt_timeout_and_missing_transport_do_not_claim_delivery(
        self,
    ):
        for result, expected in (
            (subprocess.CompletedProcess([], 0, "exit zero only", ""), "unknown"),
            (
                subprocess.CompletedProcess(
                    [],
                    0,
                    "Queued message 33333333-3333-4333-8333-333333333333 for thread 99999999-9999-4999-8999-999999999999.",
                    "",
                ),
                "unknown",
            ),
            (subprocess.TimeoutExpired("codex", 10), "unknown"),
            (FileNotFoundError("codex"), "failed"),
        ):
            with self.subTest(expected=expected):
                kwargs = (
                    {"side_effect": result}
                    if isinstance(result, Exception)
                    else {"return_value": result}
                )
                with patch.object(callback, "_run_codex_queue_cli", **kwargs) as queue:
                    callback.attempt_codex_callback(
                        self.run, self.completion, self.task, self.root
                    )
                    callback.attempt_codex_callback(
                        self.run, self.completion, self.task, self.root
                    )
                queue.assert_called_once()
                self.assertEqual(
                    json.loads(lifecycle.callback_file_path(self.run).read_text())[
                        "status"
                    ],
                    expected,
                )
                lifecycle.callback_file_path(self.run).unlink()
                lifecycle.callback_claim_path(self.run).unlink()

    def test_ack_and_supersession_suppress_notification_before_or_after_claim(self):
        for states, claimed in (([True], False), ([False, True], True)):
            with (
                patch.object(
                    callback, "is_run_consumed_or_superseded", side_effect=states
                ),
                patch.object(callback, "_run_codex_queue_cli") as queue,
            ):
                callback.attempt_codex_callback(
                    self.run, self.completion, self.task, self.root
                )
            queue.assert_not_called()
            self.assertEqual(lifecycle.callback_claim_path(self.run).exists(), claimed)
            if claimed:
                lifecycle.callback_claim_path(self.run).unlink()

    def test_dead_worker_anomaly_has_bound_identity_without_fabricating_completion(
        self,
    ):
        lifecycle._atomic_write_json(self.run / "task.json", self.task)
        (self.run / "worker.pid").write_text("12345")
        with (
            patch.object(lifecycle, "_is_pid_alive", return_value=False),
            patch.object(
                callback,
                "_run_codex_queue_cli",
                return_value=subprocess.CompletedProcess([], 0, "unverifiable", ""),
            ) as queue,
        ):
            result = lifecycle.watchdog_check(
                self.run, self.root, callback.attempt_codex_callback
            )
            lifecycle.watchdog_check(
                self.run, self.root, callback.attempt_codex_callback
            )
        self.assertEqual(result, "recorded_unknown")
        queue.assert_called_once()
        self.assertIn("status: unknown", queue.call_args.args[1])
        self.assertIn(
            f"work_package_id={self.task['work_package_id']}", queue.call_args.args[1]
        )
        self.assertFalse((self.run / "completion.json").exists())
        self.assertEqual(lifecycle.read_lifecycle(self.run)["status"], "unknown")

    def test_watchdog_terminal_callback_uses_saved_identity_and_respects_existing_claim(
        self,
    ):
        lifecycle._atomic_write_json(self.run / "task.json", self.task)
        lifecycle._atomic_write_json(self.run / "completion.json", self.completion)
        with patch.object(
            callback,
            "_run_codex_queue_cli",
            return_value=subprocess.CompletedProcess([], 0, "unverifiable", ""),
        ) as queue:
            self.assertEqual(
                lifecycle.watchdog_check(
                    self.run, self.root, callback.attempt_codex_callback
                ),
                "sent_first",
            )
            self.assertEqual(
                lifecycle.watchdog_check(
                    self.run, self.root, callback.attempt_codex_callback
                ),
                "continue",
            )
        queue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
