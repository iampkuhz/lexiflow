"""Identity-bound lifecycle snapshots must not trust a file merely because it exists."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agents.qoder import runner as qoder_task
from tests.agents.test_qoder_runner import valid_task, terminal_completion

RUN_ID = "00000000-0000-4000-8000-000000000001"


def bound_task() -> dict:
    task = valid_task()
    task.update(
        session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        agent_id="agent_test001",
        run_id=RUN_ID,
        client="qoder",
    )
    return task


class LifecycleRecordTest(unittest.TestCase):
    def _run(self):
        directory = tempfile.TemporaryDirectory()
        run = Path(directory.name) / RUN_ID
        run.mkdir()
        task = bound_task()
        qoder_task._atomic_write_json(run / "task.json", task)
        return directory, run, task

    def test_snapshot_rejects_wrong_session_and_bad_completion(self) -> None:
        directory, run, task = self._run()
        with directory:
            qoder_task._atomic_write_json(
                run / "started.json",
                {
                    "run_id": RUN_ID,
                    "started_at": 1,
                    "pid": 2,
                    "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                },
            )
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "unknown")
            self.assertIn("started-record-invalid", detail["startup_reason"])
            (run / "started.json").unlink()
            (run / "completion.json").write_text("{not-json", encoding="utf-8")
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "conflict")
            self.assertIn("completion-record-invalid", detail["reason"])

    def test_snapshot_rejects_wrong_completion_identity_and_bad_callback(self) -> None:
        directory, run, task = self._run()
        with directory:
            wrong = terminal_completion(RUN_ID)
            wrong["session_id"] = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            qoder_task._atomic_write_json(run / "completion.json", wrong)
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "conflict")
            self.assertIn("completion-record-invalid", detail["reason"])
            (run / "completion.json").unlink()
            qoder_task._atomic_write_json(
                run / "callback.json",
                {"status": "queued", "parent_session_id": task["parent_session_id"]},
            )
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(
                (status, detail["reason"]),
                ("conflict", "callback-without-bound-terminal-completion"),
            )

    def test_bound_worker_spawn_failure_is_terminal_without_started(self) -> None:
        directory, run, task = self._run()
        with directory:
            qoder_task._atomic_write_json(
                run / "completion.json",
                {
                    **terminal_completion(RUN_ID, "failed"),
                    "exit_code": 126,
                    "error": "worker spawn failed: OSError",
                },
            )
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "failed")
            self.assertEqual(detail["startup"], "FAILED")

    def test_pending_cmd_status_returns_snapshot_without_watching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            run = root / "tmp/qoder-tasks" / RUN_ID
            run.mkdir(parents=True)
            task = bound_task()
            qoder_task._atomic_write_json(run / "task.json", task)
            qoder_task._atomic_write_json(
                run / "continuation.json",
                {
                    "run_id": RUN_ID,
                    "state": "AWAITING_CALLBACK",
                    "next_action": "end-current-turn-await-callback",
                },
            )
            with (
                patch.object(Path, "cwd", return_value=root),
                patch("builtins.print") as output,
            ):
                qoder_task.cmd_status(argparse.Namespace(run_id=RUN_ID))
            value = json.loads(output.call_args.args[0])
            self.assertEqual(value["status"], "unknown")
            self.assertEqual(value["continuation"]["state"], "AWAITING_CALLBACK")

    def test_existing_resume_worker_confirms_supersession_and_preserves_ack(self):
        directory, old, task = self._run()
        with directory:
            lifecycle = qoder_task._lifecycle
            lifecycle._atomic_write_json(
                old / "completion.json", terminal_completion(RUN_ID)
            )
            ack = lifecycle.ack_run(
                old.parent,
                RUN_ID,
                task["parent_session_id"],
                codex_thread_id=task["parent_session_id"],
            )
            new_id = "00000000-0000-4000-8000-000000000002"
            new = old.parent / new_id
            new.mkdir()
            new_task = {
                **task,
                "run_id": new_id,
                "agent_id": "agent_fixture002",
                "_resume_mode": True,
            }
            lifecycle._atomic_write_json(
                old / "resume.reservation", {"new_run_id": new_id}
            )
            lifecycle.write_lifecycle(new, {"previous_run_id": RUN_ID})
            qoder_task._complete_resume_start(new, new_task)
            state = lifecycle.read_lifecycle(old)
            self.assertEqual(
                (state["status"], state["next_run_id"]), ("superseded", new_id)
            )
            self.assertEqual(lifecycle._safe_read_json(old / "ack.json"), ack)
            self.assertEqual(
                lifecycle.ack_run(
                    old.parent,
                    RUN_ID,
                    task["parent_session_id"],
                    codex_thread_id=task["parent_session_id"],
                ),
                ack,
            )
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                lifecycle.record_supersession(
                    old, "00000000-0000-4000-8000-000000000003"
                )

    def test_existing_resume_reservation_release_is_bound_to_this_worker(self):
        directory, old, task = self._run()
        with directory:
            lifecycle = qoder_task._lifecycle
            new = old.parent / "00000000-0000-4000-8000-000000000002"
            new.mkdir()
            lifecycle.write_lifecycle(new, {"previous_run_id": RUN_ID})
            reservation = old / "resume.reservation"
            lifecycle._atomic_write_json(
                reservation, {"new_run_id": "different-fixture"}
            )
            before = reservation.read_bytes()
            qoder_task._release_resume_reservation(new)
            self.assertEqual(reservation.read_bytes(), before)
            lifecycle._atomic_write_json(reservation, {"new_run_id": new.name})
            qoder_task._release_resume_reservation(new)
            self.assertFalse(reservation.exists())
            lifecycle._atomic_write_json(reservation, {"new_run_id": new.name})
            with self.assertRaisesRegex(ValueError, "resume identity mismatch"):
                qoder_task._complete_resume_start(
                    new, {**task, "session_id": "mismatch"}
                )
            self.assertTrue(reservation.exists())

    def test_ack_requires_saved_parent_and_unknown_requires_verified_dead_worker(self):
        directory, run, task = self._run()
        with directory:
            lifecycle = qoder_task._lifecycle
            lifecycle.write_lifecycle(
                run, {"status": "unknown", "anomaly": "worker died without completion"}
            )
            (run / "worker.pid").write_text("12345")
            with self.assertRaisesRegex(ValueError, "parent_session_id does not match"):
                lifecycle.ack_run(
                    run.parent,
                    RUN_ID,
                    "22222222-3333-4444-8555-666666666666",
                    codex_thread_id=task["parent_session_id"],
                )
            with (
                patch.object(lifecycle, "_is_pid_alive", return_value=True),
                self.assertRaisesRegex(ValueError, "pid still alive"),
            ):
                lifecycle.ack_run(
                    run.parent,
                    RUN_ID,
                    task["parent_session_id"],
                    codex_thread_id=task["parent_session_id"],
                )
            self.assertFalse(lifecycle.ack_path(run).exists())
            with patch.object(lifecycle, "_is_pid_alive", return_value=False):
                ack = lifecycle.ack_run(
                    run.parent,
                    RUN_ID,
                    task["parent_session_id"],
                    codex_thread_id=task["parent_session_id"],
                )
            self.assertEqual(ack["completion_status"], "unknown")
            with patch.object(lifecycle, "_is_pid_alive", return_value=True) as alive:
                self.assertEqual(
                    lifecycle.ack_run(
                        run.parent,
                        RUN_ID,
                        task["parent_session_id"],
                        codex_thread_id=task["parent_session_id"],
                    ),
                    ack,
                )
            alive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
