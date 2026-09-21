"""Dependency boundary tests.

Ensures the new verification and environment modules do NOT import
scripts.gates, scripts.harness, or any task/phase/identity modules.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


_FORBIDDEN_IMPORTS = (
    "scripts.gates",
    "scripts.harness",
    "scripts.toolchain",
)

_MODULES_TO_CHECK = [
    "scripts/verification/__init__.py",
    "scripts/verification/kernel.py",
    "scripts/verification/scope.py",
    "scripts/verification/environment.py",
    "scripts/verification/declarations.py",
    "scripts/verification/scenarios.py",
    "scripts/environment/__init__.py",
    "scripts/check_changes.py",
    "scripts/check_repository.py",
]


def _get_imports(file_path: Path) -> list[str]:
    """Extract all import module names from a Python file."""
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


class TestNoForbiddenImports(unittest.TestCase):
    """Verify that verification modules do not import forbidden modules."""

    def test_no_gates_import(self):
        root = Path(__file__).resolve().parents[2]
        for module_path in _MODULES_TO_CHECK:
            full_path = root / module_path
            if not full_path.is_file():
                continue
            imports = _get_imports(full_path)
            for imp in imports:
                for forbidden in _FORBIDDEN_IMPORTS:
                    self.assertFalse(
                        imp.startswith(forbidden),
                        f"{module_path} imports {imp!r} which starts with "
                        f"forbidden prefix {forbidden!r}",
                    )

    def test_no_task_catalog_import(self):
        """Verification modules must not read task catalogs."""
        root = Path(__file__).resolve().parents[2]
        for module_path in _MODULES_TO_CHECK:
            full_path = root / module_path
            if not full_path.is_file():
                continue
            source = full_path.read_text(encoding="utf-8")
            self.assertNotIn(
                "workstreams.yaml", source,
                f"{module_path} references workstreams.yaml",
            )
            self.assertNotIn(
                "task_validation", source,
                f"{module_path} references task_validation",
            )

    def test_no_phase_profile_import(self):
        """Verification modules must not read phase profiles."""
        root = Path(__file__).resolve().parents[2]
        for module_path in _MODULES_TO_CHECK:
            full_path = root / module_path
            if not full_path.is_file():
                continue
            source = full_path.read_text(encoding="utf-8")
            self.assertNotIn(
                "phase-task-contract", source,
                f"{module_path} references phase-task-contract",
            )
            self.assertNotIn(
                "phase_profile", source,
                f"{module_path} references phase_profile",
            )

    def test_no_identity_import(self):
        """Verification modules must not read formal identity."""
        root = Path(__file__).resolve().parents[2]
        for module_path in _MODULES_TO_CHECK:
            full_path = root / module_path
            if not full_path.is_file():
                continue
            source = full_path.read_text(encoding="utf-8")
            for term in ("issuer_packet", "evidence_packet", "local_issuer"):
                self.assertNotIn(
                    term, source,
                    f"{module_path} references {term}",
                )


if __name__ == "__main__":
    unittest.main()
