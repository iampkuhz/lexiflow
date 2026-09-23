from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from scripts.agents import dispatch_fallback as subject

SESSION = "11111111-2222-4333-8444-555555555555"


def task() -> dict:
    return {"parent_session_id": SESSION, "work_package_id": "DISPATCH-WP-01",
            "task_ids": ["LF-TSK-DISPATCH-001"], "task_versions": {"LF-TSK-DISPATCH-001": 1},
            "change_versions": {"LF-TSK-DISPATCH-001": "1.0.0"}}


class DispatchFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"; self.root.mkdir()
        policy = {"agent_dispatch": {"primary": "qoder", "fallback_model": "gpt-6-sol",
                  "fallback_reasoning_effort": "high", "max_consecutive_failures": 3,
                  "host_wait_policy": "reject-active-or-unknown-goal-without-supported-wait-adapter"}}
        path = self.root / "harness"; path.mkdir(); (path / "agent-policy.manifest.yaml").write_text(yaml.safe_dump(policy))
        self.home = Path(self.temp.name) / "codex"; (self.home / "sessions/2026/09/21").mkdir(parents=True)
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.home)}, clear=False); self.env.start(); self.addCleanup(self.env.stop)
        self.runtime = patch.object(subject, "discover", return_value=SimpleNamespace(context={"parent_session_id": SESSION}))
        self.runtime.start(); self.addCleanup(self.runtime.stop)

    def _rollout(self, call_id: str, output: object | None, *, message: str | None = None,
                 tool: str = "spawn_agent", arguments: object | None = None) -> None:
        if arguments is None:
            arguments = ({} if tool == "list_agents" else {"model": "gpt-6-sol", "reasoning_effort": "high",
                         "message": message or f"DISPATCH-WP-01 attempt {call_id}"})
        raw_args = arguments if isinstance(arguments, str) else json.dumps(arguments)
        events = [{"type": "response_item", "payload": {"type": "function_call", "name": tool, "call_id": call_id,
                   "arguments": raw_args}}]
        if output is not None:
            value = json.dumps(output) if isinstance(output, dict) else output
            events.append({"type": "response_item", "payload": {"type": "function_call_output", "call_id": call_id, "output": value}})
        p = next((self.home / "sessions").glob(f"**/*-{SESSION}.jsonl"), self.home / "sessions/2026/09/21" / f"rollout-{SESSION}.jsonl")
        p.write_text("".join(json.dumps(item) + "\n" for item in events))

    def test_real_native_handle_resets_streak_and_replay_is_rejected(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "call-a", "external-busy")
        self._rollout("call-a", {"task_name": "/root/terra-child"})
        result = subject.record_terra_fallback(self.root, task(), "call-a", "call-a")
        self.assertEqual(result["status"], "PASS")
        replay = subject.record_terra_fallback(self.root, task(), "call-a", "call-a")
        self.assertEqual(replay["status"], "PASS")

    def test_three_verified_native_errors_stop_but_unknown_never_counts(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "unknown", "external-busy")
        self._rollout("unknown", "not a structured native result")
        unknown = subject.record_terra_fallback(self.root, task(), "unknown", "unknown")
        self.assertEqual(unknown["code"], "TERRA_RESULT_UNKNOWN")
        with self.assertRaisesRegex(subject.DispatchFallbackError, "pending"):
            subject.begin_terra_fallback(self.root, task(), "blocked-by-unknown", "external-busy")
        # A distinct package/identity is not used to evade the pending pair; settle this fixture as a known error.
        self._rollout("unknown", "collab spawn failed: agent thread limit reached")
        subject.record_terra_fallback(self.root, task(), "unknown", "unknown")
        for index in range(1, 3):
            attempt = f"call-{index}"; subject.begin_terra_fallback(self.root, task(), attempt, "external-busy")
            self._rollout(attempt, "collab spawn failed: agent thread limit reached")
            result = subject.record_terra_fallback(self.root, task(), attempt, attempt)
        self.assertEqual(result["code"], "DISPATCH_STOPPED")
        stopped = subject.begin_terra_fallback(self.root, task(), "four", "external-busy")
        self.assertEqual(stopped["code"], "DISPATCH_STOPPED")

    def test_pending_qoder_no_start_transitions_once_without_execution_counter(self) -> None:
        subject.begin_qoder_attempt(self.root, task(), "qoder-run")
        route = subject.mark_qoder_unavailable(self.root, task(), "qoder-run", "qoder-cli-not-started")
        self.assertEqual(route["code"], "TERRA_REQUIRED")
        self.assertEqual(route["fallback"], {"model": "gpt-6-sol", "reasoning_effort": "high"})
        with self.assertRaisesRegex(subject.DispatchFallbackError, "pending"):
            subject.mark_qoder_unavailable(self.root, task(), "qoder-run", "again")

    def test_unsupported_fallback_model_is_rejected(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "old-model", "external-busy")
        self._rollout("old-model", {"task_name": "/root/legacy-child"}, arguments={
            "model": "unsupported-model", "reasoning_effort": "high",
            "message": "DISPATCH-WP-01 attempt old-model",
        })
        with self.assertRaisesRegex(subject.DispatchFallbackError, "configured fallback model"):
            subject.record_terra_fallback(self.root, task(), "old-model", "old-model")

    def test_active_terra_handle_blocks_new_qoder_until_verified_terminal_record(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "terra-active", "external-busy")
        self._rollout("terra-active", {"task_name": "/root/terra-child"})
        subject.record_terra_fallback(self.root, task(), "terra-active", "terra-active")
        with self.assertRaisesRegex(subject.DispatchFallbackError, "pending"):
            subject.begin_qoder_attempt(self.root, task(), "unsafe-qoder-retry")

    def test_terra_terminal_from_current_list_agents_record_allows_next_round(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "first", "external-busy")
        self._rollout("first", "collab spawn failed: agent thread limit reached")
        subject.record_terra_fallback(self.root, task(), "first", "first")
        subject.begin_terra_fallback(self.root, task(), "second", "external-busy")
        self._rollout("second", {"task_name": "/root/terra-child"})
        self.assertEqual(subject.record_terra_fallback(self.root, task(), "second", "second")["status"], "PASS")
        self._rollout("list-second", {"agents": [{"agent_name": "/root/terra-child", "agent_status": {"completed": "fixture completion"}}]}, tool="list_agents")
        terminal = subject.record_terra_fallback(self.root, task(), "second", "list-second")
        self.assertEqual(subject.record_terra_fallback(self.root, task(), "second", "list-second"), terminal)
        self.assertEqual(terminal["code"], "TERRA_TERMINAL")
        subject.begin_qoder_attempt(self.root, task(), "next-round")
        state = json.loads(next((self.root / "tmp/quality/agent-dispatch").glob("*/state.json")).read_text())
        self.assertEqual(state["consecutive_failures"], 0)

    def test_unrelated_terminal_and_duplicate_json_are_rejected(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "bound", "external-busy")
        self._rollout("bad", '{"task_name":"/root/a","task_name":"/root/b"}', message="DISPATCH-WP-01 attempt bound")
        with self.assertRaisesRegex(subject.DispatchFallbackError, "strict JSON"):
            subject.record_terra_fallback(self.root, task(), "bound", "bad")
        self._rollout("bound", {"task_name": "/root/terra-child"})
        subject.record_terra_fallback(self.root, task(), "bound", "bound")
        self._rollout("wrong-list", {"agents": [{"task_name": "/root/other", "status": "completed"}]}, tool="list_agents")
        with self.assertRaisesRegex(subject.DispatchFallbackError, "does not prove"):
            subject.record_terra_fallback(self.root, task(), "bound", "wrong-list")

    def test_concurrent_begin_and_native_failure_replay_create_one_pair_and_count_once(self) -> None:
        barrier = threading.Barrier(2); errors: list[Exception] = []
        def begin() -> None:
            try:
                barrier.wait(); subject.begin_terra_fallback(self.root, task(), "same", "external-busy")
            except Exception as exc: errors.append(exc)
        threads = [threading.Thread(target=begin), threading.Thread(target=begin)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(errors, [])
        self._rollout("same", "collab spawn failed: agent thread limit reached")
        subject.record_terra_fallback(self.root, task(), "same", "same")
        replay = subject.record_terra_fallback(self.root, task(), "same", "same")
        self.assertEqual(replay["consecutive_failures"], 1)

    def test_native_call_must_bind_the_full_package_and_attempt(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "bound", "external-busy")
        self._rollout("bound", {"task_name": "/root/terra-child"}, message="unrelated task bound")
        with self.assertRaisesRegex(subject.DispatchFallbackError, "does not bind"):
            subject.record_terra_fallback(self.root, task(), "bound", "bound")

    def test_list_agents_running_wrong_handle_and_invented_shape_cannot_unlock(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "active", "external-busy")
        self._rollout("active", {"task_name": "/root/terra-child"})
        subject.record_terra_fallback(self.root, task(), "active", "active")
        cases = [
            {"agent_name": "/root/terra-child", "agent_status": "running"},
            {"agent_name": "/root/other", "agent_status": {"completed": "done"}},
            {"task_name": "/root/terra-child", "status": "completed"},
        ]
        for index, record in enumerate(cases):
            call_id = f"list-{index}"
            self._rollout(call_id, {"agents": [record]}, tool="list_agents")
            with self.assertRaisesRegex(subject.DispatchFallbackError, "terminal"):
                subject.record_terra_fallback(self.root, task(), "active", call_id)
            with self.assertRaisesRegex(subject.DispatchFallbackError, "pending"):
                subject.begin_qoder_attempt(self.root, task(), "unsafe-new-run")

    def test_wrong_model_and_changed_pending_call_id_are_rejected(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "model", "external-busy")
        self._rollout("model", {"task_name": "/root/terra"}, arguments={
            "model": "gpt-6-luna", "reasoning_effort": "high", "message": "DISPATCH-WP-01 model",
        })
        with self.assertRaisesRegex(subject.DispatchFallbackError, "explicitly request"):
            subject.record_terra_fallback(self.root, task(), "model", "model")
        self._rollout("unknown-a", None, message="DISPATCH-WP-01 attempt model")
        subject.record_terra_fallback(self.root, task(), "model", "unknown-a")
        self._rollout("unknown-b", "collab spawn failed: agent thread limit reached", message="DISPATCH-WP-01 attempt model")
        with self.assertRaisesRegex(subject.DispatchFallbackError, "cannot change"):
            subject.record_terra_fallback(self.root, task(), "model", "unknown-b")

    def test_real_qoder_start_clears_streak_without_claiming_acceptance(self) -> None:
        subject.begin_terra_fallback(self.root, task(), "failed", "external-busy")
        self._rollout("failed", "collab spawn failed: agent thread limit reached")
        subject.record_terra_fallback(self.root, task(), "failed", "failed")
        subject.begin_qoder_attempt(self.root, task(), "qoder-started")
        subject.mark_qoder_started(self.root, task(), "qoder-started")
        state = next((self.root / "tmp/quality/agent-dispatch").glob("*/state.json"))
        value = json.loads(state.read_text())
        self.assertEqual(value["consecutive_failures"], 0)
        self.assertEqual(value["attempts"]["qoder-started"]["qoder"]["status"], "dispatched")

    def test_dangling_symlink_and_bad_counter_are_fail_closed(self) -> None:
        resolved = self.root.resolve(); _, key = subject._identity(resolved, task()); directory = subject._root(resolved, key)
        state = directory / "state.json"; state.symlink_to(directory / "missing.json")
        with self.assertRaisesRegex(subject.DispatchFallbackError, "unsafe"):
            subject.begin_qoder_attempt(self.root, task(), "bad-link")
        state.unlink(); state.write_text(json.dumps({"schema_version": "lexiflow.agent-dispatch.v1", "identity": subject._identity(resolved, task())[0], "consecutive_failures": True, "history": [], "attempts": {}}))
        with self.assertRaisesRegex(subject.DispatchFallbackError, "invalid"):
            subject.begin_qoder_attempt(self.root, task(), "bad-state")

    def test_goal_active_unknown_and_inactive_are_derived_from_current_row_only(self) -> None:
        db = self.home / "goals_1.sqlite"
        con = sqlite3.connect(db); con.execute("create table thread_goals (thread_id text primary key, status text not null)")
        con.execute("insert into thread_goals values (?, ?)", (SESSION, "active")); con.commit(); con.close(); os.chmod(db, 0o600)
        self.assertEqual(subject.host_wait_decision(self.root)["host_goal"]["state"], "active")
        con = sqlite3.connect(db); con.execute("update thread_goals set status='complete' where thread_id=?", (SESSION,)); con.commit(); con.close()
        self.assertIsNone(subject.host_wait_decision(self.root))
        db.unlink()
        self.assertEqual(subject.host_wait_decision(self.root)["host_goal"]["state"], "unknown")


if __name__ == "__main__":
    unittest.main()
