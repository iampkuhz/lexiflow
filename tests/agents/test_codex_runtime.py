"""校验可信 Codex 身份、原生来源及拒绝调用者注入。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.agents.identity.codex_identity_binding import _CodexRuntimeBinding, CodexRuntimeError
from scripts.agents.identity.codex_actor_identity import discover, verify_proof


class NativeCodexRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        self.root.mkdir()
        self.home = self.root.parent / "codex"
        self.sessions = self.home / "sessions"
        self.sessions.mkdir(parents=True)
        self.parent, self.child = str(uuid.uuid4()), str(uuid.uuid4())
        self.parent_meta = {
            "id": self.parent,
            "session_id": self.parent,
            "cwd": str(self.root),
            "source": "vscode",
            "thread_source": "user",
            "originator": "Codex Desktop",
        }
        self.child_meta = {
            "id": self.child,
            "session_id": self.parent,
            "cwd": str(self.root),
            "thread_source": "subagent",
            "originator": "Codex Desktop",
            "parent_thread_id": self.parent,
            "agent_path": "/root/validator",
            "source": {
                "subagent": {
                    "thread_spawn": {
                        "parent_thread_id": self.parent,
                        "agent_path": "/root/validator",
                        "depth": 1,
                    }
                }
            },
        }
        self.write(self.parent_meta)
        self.write(self.child_meta)
        self.env = patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(self.home),
                "CODEX_THREAD_ID": self.child,
                "CODEX_SESSION_ID": self.parent,
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, metadata):
        path = self.sessions / f"rollout-{metadata['id']}.jsonl"
        path.write_text(
            json.dumps({"type": "session_meta", "payload": metadata}) + "\n"
        )
        path.chmod(0o600)
        return path

    def test_shared_host_native_subagent_resolves_and_proof_survives_log_growth(self):
        runtime = discover(self.root)
        identity = {
            "agent_id": runtime.context["actor_id"],
            "run_id": str(uuid.uuid4()),
            "parent_client": "codex",
            **{
                key: runtime.context[key]
                for key in ("session_id", "parent_session_id", "client")
            },
        }
        binding = _CodexRuntimeBinding(identity, runtime.proof)
        binding.persist_immutable(self.root)
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-binding-collision"):
            binding.persist_immutable(self.root)
        changed = {**identity, "run_id": str(uuid.uuid4()), "agent_id": "other_actor"}
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-identity-drift"):
            _CodexRuntimeBinding(changed, runtime.proof).persist_immutable(self.root)
        self.assertEqual(
            runtime.context,
            {
                "actor_id": "codex-thread-" + self.child,
                "session_id": self.parent,
                "parent_session_id": self.parent,
                "client": "codex",
            },
        )
        with (self.sessions / f"rollout-{self.child}.jsonl").open("a") as stream:
            stream.write('{"type":"event_msg","payload":{}}\n')
        verify_proof(self.root, runtime.proof, runtime.context)
        self.assertEqual(discover(self.root).context, runtime.context)

    def test_root_task_and_second_child_have_distinct_stable_actors(self):
        child_actor = discover(self.root).context["actor_id"]
        with patch.dict(os.environ, {"CODEX_THREAD_ID": self.parent}):
            self.assertEqual(
                discover(self.root).context["actor_id"], "codex-session-" + self.parent
            )
        other = {**self.child_meta, "id": str(uuid.uuid4())}
        self.write(other)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": other["id"]}):
            self.assertNotEqual(discover(self.root).context["actor_id"], child_actor)

    def test_unrelated_session_parent_and_workspace_fail(self):
        with (
            patch.dict(os.environ, {"CODEX_SESSION_ID": str(uuid.uuid4())}),
            self.assertRaisesRegex(CodexRuntimeError, "runtime-route-conflict"),
        ):
            discover(self.root)
        self.write({**self.child_meta, "parent_thread_id": str(uuid.uuid4())})
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-identity-drift"):
            discover(self.root)
        self.write({**self.child_meta, "cwd": str(self.root.parent)})
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-workspace-mismatch"):
            discover(self.root)

    def test_missing_parent_blocks_and_changed_parent_invalidates_proof(self):
        runtime = discover(self.root)
        self.write({**self.parent_meta, "originator": "changed"})
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-proof-drift"):
            verify_proof(self.root, runtime.proof, runtime.context)
        (self.sessions / f"rollout-{self.parent}.jsonl").unlink()
        with self.assertRaises(CodexRuntimeError) as caught:
            discover(self.root)
        self.assertEqual(caught.exception.status, "BLOCKED")

    def test_unknown_source_and_unsafe_metadata_are_rejected(self):
        path = self.write({**self.child_meta, "source": "forged"})
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-source-invalid"):
            discover(self.root)
        self.write(self.child_meta)
        path.chmod(0o666)
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-source-unsafe"):
            discover(self.root)

    def test_actor_cannot_be_changed_in_proof(self):
        runtime = discover(self.root)
        with self.assertRaisesRegex(CodexRuntimeError, "runtime-identity-drift"):
            verify_proof(
                self.root, runtime.proof, {**runtime.context, "actor_id": "other"}
            )

    def test_session_only_route_cannot_discover_actor(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ""}):
            with self.assertRaises(CodexRuntimeError) as caught:
                discover(self.root)
        self.assertEqual(caught.exception.status, "BLOCKED")

    def test_public_identity_factory_and_main_materializer_are_removed(self):
        import scripts.agents.identity.codex_identity_binding as binding
        import scripts.agents.identity.codex_actor_identity as runtime

        for name in ("CodexRuntimeBinding", "bind_trusted_host_context"):
            self.assertFalse(hasattr(binding, name))
        for name in ("bind_main_task", "main"):
            self.assertFalse(hasattr(runtime, name))
        self.assertFalse(hasattr(runtime.LocalCodexRuntime, "bind"))
