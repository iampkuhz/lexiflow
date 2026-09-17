from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from scripts.harness.codex_runtime import CodexRuntimeBinding, CodexRuntimeError


class CodexRuntimeBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.caller = {"goal": "fixture", "parent_client": "codex"}
        self.host = {
            "parent_session_id": str(uuid.uuid4()), "agent_id": "runtime_fixture",
            "session_id": str(uuid.uuid4()), "client": "codex", "parent_client": "codex",
        }
        self.run_id = str(uuid.uuid4())

    def test_host_binding_is_stable_across_consecutive_runs_and_persists_immutably(self) -> None:
        first = CodexRuntimeBinding.create(self.caller, self.host, self.run_id)
        second = CodexRuntimeBinding.create(self.caller, self.host, str(uuid.uuid4()))
        self.assertEqual(first.identity["agent_id"], second.identity["agent_id"])
        self.assertEqual(first.identity["session_id"], second.identity["session_id"])
        with tempfile.TemporaryDirectory() as root:
            one = first.persist_immutable(root)
            two = first.persist_immutable(root)
            self.assertEqual(one, two)
            changed = dict(first.identity); changed["agent_id"] = "other_actor"
            with self.assertRaisesRegex(CodexRuntimeError, "runtime-binding-collision"):
                CodexRuntimeBinding(changed).persist_immutable(root)

    def test_missing_host_metadata_blocks_and_caller_identity_fails(self) -> None:
        with self.assertRaises(CodexRuntimeError) as blocked:
            CodexRuntimeBinding.create(self.caller, None, self.run_id)
        self.assertEqual(blocked.exception.status, "BLOCKED")
        caller = {**self.caller, "agent_id": "forged"}
        with self.assertRaisesRegex(CodexRuntimeError, "caller-identity-injection"):
            CodexRuntimeBinding.create(caller, self.host, self.run_id)
