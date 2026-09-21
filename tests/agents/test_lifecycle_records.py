"""Identity-bound lifecycle snapshots must not trust a file merely because it exists."""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agents import qoder_task
from tests.agents.test_qoder_runner import valid_task, terminal_completion

RUN_ID = "00000000-0000-4000-8000-000000000001"


def bound_task() -> dict:
    task = valid_task()
    task.update(session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", agent_id="agent_test001", run_id=RUN_ID, client="qoder")
    return task


class LifecycleRecordTest(unittest.TestCase):
    def _run(self):
        directory = tempfile.TemporaryDirectory()
        run = Path(directory.name) / RUN_ID; run.mkdir()
        task = bound_task()
        qoder_task._atomic_write_json(run / "task.json", task)
        return directory, run, task

    def test_handshake_rejects_wrong_session_and_bad_completion(self) -> None:
        directory, run, task = self._run()
        with directory:
            qoder_task._atomic_write_json(run / "started.json", {"run_id": RUN_ID, "started_at": 1, "pid": 2, "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"})
            with patch.object(qoder_task._lifecycle, "wait_for_started", return_value=True):
                self.assertEqual(qoder_task._start_handshake(run), "STARTING")
            (run / "started.json").unlink()
            (run / "completion.json").write_text("{not-json", encoding="utf-8")
            with patch.object(qoder_task._lifecycle, "wait_for_started", return_value=False):
                self.assertEqual(qoder_task._start_handshake(run), "STARTING")

    def test_snapshot_rejects_wrong_completion_identity_and_bad_callback(self) -> None:
        directory, run, task = self._run()
        with directory:
            wrong = terminal_completion(RUN_ID); wrong["session_id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            qoder_task._atomic_write_json(run / "completion.json", wrong)
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "conflict")
            self.assertIn("completion-record-invalid", detail["reason"])
            (run / "completion.json").unlink()
            qoder_task._atomic_write_json(run / "callback.json", {"status": "queued", "parent_session_id": task["parent_session_id"]})
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual((status, detail["reason"]), ("conflict", "callback-without-bound-terminal-completion"))

    def test_bound_worker_spawn_failure_is_terminal_without_started(self) -> None:
        directory, run, task = self._run()
        with directory:
            qoder_task._write_worker_spawn_failure(run, task, OSError("denied"))
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "failed")
            self.assertEqual(detail["startup"], "FAILED")

    def test_pending_cmd_status_returns_snapshot_without_watching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / ".git").mkdir(); run = root / "tmp/qoder-tasks" / RUN_ID; run.mkdir(parents=True)
            task = bound_task(); qoder_task._atomic_write_json(run / "task.json", task); qoder_task._write_continuation(RUN_ID, run)
            with patch.object(Path, "cwd", return_value=root), patch("builtins.print") as output:
                qoder_task.cmd_status(argparse.Namespace(run_id=RUN_ID))
            value = json.loads(output.call_args.args[0])
            self.assertEqual(value["status"], "unknown")
            self.assertEqual(value["continuation"]["state"], "AWAITING_CALLBACK")

if __name__ == "__main__":
    unittest.main()
