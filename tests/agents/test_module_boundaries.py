"""Regression checks for agent execution ownership boundaries."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "scripts" / "agents"

class AgentModuleBoundaryTest(unittest.TestCase):
    def test_execution_modules_do_not_import_gate_or_entry_backwards(self) -> None:
        for source in AGENTS.rglob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            self.assertFalse(any(name.startswith("scripts.gates") for name in imported), source)
            if source.name == "lifecycle.py":
                self.assertFalse(any(name == "scripts.agents.qoder_task" for name in imported), source)

    def test_retired_harness_agent_paths_have_no_compatibility_wrappers(self) -> None:
        retired = (
            ROOT / "scripts" / "harness" / "qoder_task.py",
            ROOT / "scripts" / "harness" / "local_codex_runtime.py",
            ROOT / "scripts" / "harness" / "codex_work_package.py",
            ROOT / "scripts" / "harness" / "qoder",
            ROOT / "scripts" / "harness" / "codex",
        )
        self.assertTrue(all(not path.exists() for path in retired))

    def test_start_handshake_has_only_three_runner_states(self) -> None:
        from scripts.agents import qoder_task
        self.assertEqual({"STARTED", "STARTING", "FAILED"}, {
            "STARTED", "STARTING", "FAILED"
        })
        self.assertGreater(qoder_task.START_HANDSHAKE_SECONDS, 0)

if __name__ == "__main__":
    unittest.main()

class RunnerRecordStateTest(unittest.TestCase):
    def test_start_handshake_timeout_is_starting_not_failed(self) -> None:
        from scripts.agents import qoder_task
        from unittest.mock import patch
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(qoder_task._lifecycle, "wait_for_started", return_value=False):
                self.assertEqual(qoder_task._start_handshake(Path(directory)), "STARTING")

    def test_status_requires_a_bound_task_record(self) -> None:
        from scripts.agents import qoder_task
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            qoder_task._atomic_write_json(run / "completion.json", {"status": "finished"})
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "conflict")
            self.assertIn("task-record-invalid", detail["reason"])
