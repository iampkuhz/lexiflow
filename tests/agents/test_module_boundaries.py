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
                self.assertFalse(any(name == "scripts.agents.qoder.runner" for name in imported), source)

    def test_retired_harness_agent_paths_have_no_compatibility_wrappers(self) -> None:
        retired = (
            ROOT / "scripts" / "harness" / "qoder_task.py",
            ROOT / "scripts" / "harness" / "local_codex_runtime.py",
            ROOT / "scripts" / "harness" / "codex_work_package.py",
            ROOT / "scripts" / "harness" / "qoder",
            ROOT / "scripts" / "harness" / "codex",
        )
        self.assertTrue(all(not path.exists() for path in retired))
        self.assertFalse((AGENTS / "qoder_task.py").exists())
        self.assertFalse((AGENTS / "codex_work_package.py").exists())

    def test_disabled_dispatch_has_no_start_handshake(self) -> None:
        from scripts.agents.qoder import runner as qoder_task
        self.assertFalse(hasattr(qoder_task, "_start_handshake"))
        self.assertFalse(hasattr(qoder_task, "START_HANDSHAKE_SECONDS"))

if __name__ == "__main__":
    unittest.main()

class RunnerRecordStateTest(unittest.TestCase):
    def test_status_requires_a_bound_task_record(self) -> None:
        from scripts.agents.qoder import runner as qoder_task
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            qoder_task._atomic_write_json(run / "completion.json", {"status": "finished"})
            status, detail = qoder_task._status_snapshot(run)
            self.assertEqual(status, "conflict")
            self.assertIn("task-record-invalid", detail["reason"])
