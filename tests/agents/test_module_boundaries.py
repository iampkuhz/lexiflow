"""Regression checks for agent execution ownership boundaries."""
from __future__ import annotations

import ast
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

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
            self.assertFalse(any(name.startswith("scripts.acceptance") for name in imported), source)

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

    def test_work_package_cli_uses_repository_root_after_relocation(self) -> None:
        from scripts.agents.work_packages.verify_codex_work_package import main

        with (
            patch("scripts.agents.work_packages.verify_codex_work_package.CodexWorkPackagePublisher") as publisher,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            publisher.return_value.verify.return_value = {"status": "PASS"}
            self.assertEqual(main(["verify", "--run-id", "fixture"]), 0)
        publisher.assert_called_once_with(ROOT)
        publisher.return_value.verify.assert_called_once_with("fixture")

    def test_active_agent_layout_has_no_empty_legacy_packages(self) -> None:
        for name in ("codex", "delegation", "qoder"):
            self.assertFalse((AGENTS / name).exists(), name)
        for relative in (
            "identity/codex_actor_identity.py",
            "identity/codex_identity_binding.py",
            "work_packages/codex_work_package_publisher.py",
            "work_packages/work_package_contract_checks.py",
            "work_packages/verify_codex_work_package.py",
        ):
            self.assertTrue((AGENTS / relative).is_file(), relative)

    def test_qoder_runtime_and_fallback_have_no_compatibility_wrappers(self) -> None:
        retired = (
            AGENTS / "dispatch_fallback.py",
            AGENTS / "delegation" / "qoder_cli.py",
        )
        self.assertTrue(all(not path.exists() for path in retired))
        self.assertFalse(any((AGENTS / "qoder").rglob("*.py")))


if __name__ == "__main__":
    unittest.main()
