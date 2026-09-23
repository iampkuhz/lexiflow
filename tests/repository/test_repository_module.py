"""Repository module boundary and dependency direction tests.

These tests verify that scripts.repository:
1. Does not import forbidden modules (gates, harness, acceptance, agents)
2. Has complete test coverage entry point
3. Maintains correct dependency direction (no reverse imports)
"""
from __future__ import annotations
import ast
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_DIR = REPO_ROOT / "scripts" / "repository"

FORBIDDEN_IMPORTS = {
    "scripts.gates",
    "scripts.harness",
    "scripts.acceptance",
    "scripts.agents",
    "scripts.verification",
    "scripts.environment",
}


def _collect_imports(filepath: Path) -> list[str]:
    """Extract all import module names from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return []
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


class RepositoryModuleBoundaryTest(unittest.TestCase):
    """scripts.repository must not import forbidden modules."""

    def test_no_forbidden_imports_in_repository_module(self):
        violations = []
        for py_file in sorted(REPOSITORY_DIR.glob("**/*.py")):
            if "__pycache__" in str(py_file):
                continue
            for imp in _collect_imports(py_file):
                for forbidden in FORBIDDEN_IMPORTS:
                    if imp == forbidden or imp.startswith(forbidden + "."):
                        violations.append(f"{py_file.relative_to(REPO_ROOT)}: {imp}")
        self.assertEqual(violations, [], f"forbidden imports found: {violations}")

    def test_repository_module_files_exist(self):
        expected = {
            "__init__.py", "__main__.py", "catalog.py", "planning_check.py",
            "task_source.py", "docs_check.py", "policy_projection.py",
            "local_skills.py", "hooks.py",
            "quality.py",
        }
        actual = {p.name for p in REPOSITORY_DIR.glob("*.py") if "__pycache__" not in str(p)}
        missing = expected - actual
        self.assertEqual(missing, set(), f"missing repository module files: {missing}")

    def test_planning_check_has_cli_entry(self):
        """planning_check.py must have a CLI entry point."""
        planning_check = REPOSITORY_DIR / "planning_check.py"
        content = planning_check.read_text(encoding="utf-8")
        self.assertIn("def main(", content, "planning_check.py must have main() function")
        self.assertIn('if __name__', content, "planning_check.py must have __name__ guard")

    def test_planning_cli_real_positive_and_negative_fixtures(self):
        passed = subprocess.run(
            [sys.executable, "-m", "scripts.repository.planning_check", "--root", "."],
            cwd=REPO_ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, passed.returncode, passed.stderr)
        self.assertEqual("PASS", json.loads(passed.stdout)["status"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPO_ROOT / "planning", root / "planning")
            (root / "harness").mkdir()
            for name in ("agent-policy.manifest.yaml", "agent-runtime.manifest.yaml", "module-checks.yaml"):
                shutil.copy2(REPO_ROOT / "harness" / name, root / "harness" / name)
            catalog = root / "planning" / "workstreams.yaml"
            catalog.write_text(catalog.read_text().replace(
                "catalog_mode: execution", "catalog_mode: planning-only", 1))
            failed = subprocess.run(
                [sys.executable, "-m", "scripts.repository.planning_check", "--root", str(root)],
                cwd=REPO_ROOT, text=True, capture_output=True, check=False,
            )
        self.assertNotEqual(0, failed.returncode)
        report = json.loads(failed.stdout)
        self.assertNotEqual("PASS", report["status"])
        self.assertTrue(report["errors"])


class OldGateRetirementTest(unittest.TestCase):
    """Old scripts/gates/ entries must not remain as permanent code."""

    RETIRED_FILES = {
        "scripts/gates/change_verify.py",
        "scripts/gates/repository_verify.py",
        "scripts/gates/verification/local_verify_plan.py",
        "scripts/gates/execution/check_executor.py",
        "scripts/gates/planning/registry_profiles.py",
        "scripts/gates/planning/__main__.py",
        "scripts/gates/planning/catalog_validator.py",
        "scripts/gates/planning/tools/task_source_tools.py",
        "scripts/gates/planning/tools/test_case_tools.py",
        "scripts/gates/phase/task_contracts.py",
        "scripts/gates/catalog.py",
        "scripts/gates/change_context.py",
    }

    def test_retired_gate_files_are_absent(self):
        absent_violations = []
        for rel_path in self.RETIRED_FILES:
            full_path = REPO_ROOT / rel_path
            if full_path.exists():
                absent_violations.append(rel_path)
        self.assertEqual(absent_violations, [],
                         f"retired files still present: {absent_violations}")

    def test_retired_engineering_directories_are_absent(self):
        retired = (
            REPO_ROOT / "scripts" / "gates",
            REPO_ROOT / "scripts" / "harness",
            REPO_ROOT / "scripts" / "toolchain",
            REPO_ROOT / "tests" / "gates",
            REPO_ROOT / "tests" / "harness",
            REPO_ROOT / "tests" / "toolchain",
        )
        self.assertEqual([], [path.relative_to(REPO_ROOT).as_posix()
                              for path in retired if path.exists()])


class ToolchainRetirementTest(unittest.TestCase):
    """scripts/toolchain/java_gradle.py wrapper must be retired."""

    def test_java_gradle_wrapper_is_absent(self):
        wrapper = REPO_ROOT / "scripts" / "toolchain" / "java_gradle.py"
        self.assertFalse(wrapper.exists(),
                         "scripts/toolchain/java_gradle.py must be retired")

    def test_toolchain_init_is_absent(self):
        init = REPO_ROOT / "scripts" / "toolchain" / "__init__.py"
        self.assertFalse(init.exists(),
                         "scripts/toolchain/__init__.py must be retired")


class HarnessRetirementTest(unittest.TestCase):
    """scripts/harness/ must be retired; files moved to scripts/repository/."""

    RETIRED_FILES = {
        "scripts/harness/docs_check.py",
        "scripts/harness/policy_projection.py",
        "scripts/harness/local_skills.py",
        "scripts/harness/hooks.py",
    }

    def test_retired_harness_files_are_absent(self):
        absent_violations = []
        for rel_path in self.RETIRED_FILES:
            full_path = REPO_ROOT / rel_path
            if full_path.exists():
                absent_violations.append(rel_path)
        self.assertEqual(absent_violations, [],
                         f"retired harness files still present: {absent_violations}")

    def test_harness_directory_is_absent(self):
        harness_dir = REPO_ROOT / "scripts" / "harness"
        self.assertFalse(harness_dir.exists(),
                         "scripts/harness/ directory must be retired")


class StaleConfigurationRetirementTest(unittest.TestCase):
    """Stale configuration files must be retired."""

    RETIRED_CONFIGS = {
        "harness/gate-check-registry.yaml",
        "harness/phase-task-contract-profiles.yaml",
        "harness/gate-issuer-authorities.yaml",
    }

    def test_retired_config_files_are_absent(self):
        absent_violations = []
        for rel_path in self.RETIRED_CONFIGS:
            full_path = REPO_ROOT / rel_path
            if full_path.exists():
                absent_violations.append(rel_path)
        self.assertEqual(absent_violations, [],
                         f"retired config files still present: {absent_violations}")


if __name__ == "__main__":
    unittest.main()
