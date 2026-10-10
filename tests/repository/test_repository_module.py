"""Repository module boundary and dependency direction tests.

These tests verify that scripts.checks.repository:
1. Does not import execution modules; planning may use four pure path contracts
2. Has complete test coverage entry point
3. Maintains correct dependency direction (no reverse imports)
"""
from __future__ import annotations
import ast
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_DIR = REPO_ROOT / "scripts" / "checks" / "repository"

FORBIDDEN_IMPORTS = {
    "scripts.gates",
    "scripts.harness",
    "scripts.acceptance",
    "scripts.agents",
    "scripts.verification",
    "scripts.environment",
}


PLANNING_CONTRACT_SYMBOLS = {
    "AgentContractError",
    "dispatch_paths_intersect",
    "match_dispatch_path_v1",
    "parse_dispatch_path_v1",
}


def _forbidden_imports(tree: ast.AST, module_name: str) -> list[str]:
    """Only planning_validator may import the named pure contracts, not agents."""
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if (
                module_name == "scripts.checks.repository.catalog.task_catalog_rules"
                and node.level == 0
                and node.module == "scripts.agents.work_packages.work_package_contract_checks"
                and all(alias.name in PLANNING_CONTRACT_SYMBOLS for alias in node.names)
            ):
                continue
            prefix = module_name.split(".")[:-node.level] if node.level else []
            parts = [*prefix, *node.module.split(".")] if node.module else prefix
            module = ".".join(parts)
            imports = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        else:
            continue
        for imported in imports:
            if any(
                imported == forbidden or imported.startswith(forbidden + ".")
                for forbidden in FORBIDDEN_IMPORTS
            ):
                violations.append(imported)
    return violations


class RepositoryModuleBoundaryTest(unittest.TestCase):
    """scripts.checks.repository may reuse pure grammar, never execution modules."""

    def test_no_forbidden_imports_in_repository_module(self):
        violations = []
        for py_file in sorted(REPOSITORY_DIR.glob("**/*.py")):
            if "__pycache__" in str(py_file):
                continue
            module = ".".join(py_file.relative_to(REPO_ROOT).with_suffix("").parts)
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for imp in _forbidden_imports(tree, module):
                violations.append(f"{py_file.relative_to(REPO_ROOT)}: {imp}")
        self.assertEqual(violations, [], f"forbidden imports found: {violations}")

    def test_planning_allows_only_named_pure_contracts(self):
        source = "from scripts.agents.work_packages.work_package_contract_checks import " + ", ".join(
            sorted(PLANNING_CONTRACT_SYMBOLS)
        )
        tree = ast.parse(source)
        self.assertEqual(
            [], _forbidden_imports(tree, "scripts.checks.repository.catalog.task_catalog_rules")
        )
        self.assertTrue(_forbidden_imports(tree, "scripts.checks.repository.run_repository_check_group"))

    def test_execution_and_unrestricted_contract_imports_remain_forbidden(self):
        sources = (
            "import scripts.agents.work_packages.work_package_contract_checks",
            "from scripts.agents.work_packages.work_package_contract_checks import *",
            "from scripts.agents.work_packages.work_package_contract_checks import validate_caller_contract",
            "from scripts.agents.work_packages.work_package_contract_checks import parse_dispatch_path_v1, unknown",
            "from scripts.agents import contracts",
            "from scripts import agents",
            "from ....agents.work_packages.work_package_contract_checks import parse_dispatch_path_v1",
            "from scripts.agents.delegation import codex_cli",
            "from scripts.agents import local_codex_runtime",
            "from scripts.acceptance import submit",
            "from scripts.verification import development",
            "from scripts.environment import java_exec",
        )
        for source in sources:
            with self.subTest(source=source):
                self.assertTrue(
                    _forbidden_imports(
                        ast.parse(source), "scripts.checks.repository.catalog.task_catalog_rules"
                    )
                )

    def test_retired_git_hook_entries_are_absent(self):
        """禁止复活已退役 Git Hook，不要求保留空目录或配置壳。"""
        for name in ("pre-commit", "post-commit"):
            path = REPO_ROOT / ".githooks" / name
            with self.subTest(name=name):
                self.assertFalse(path.exists() or path.is_symlink())

    def test_repository_module_files_exist(self):
        expected = {
            "__init__.py", "run_repository_check_group.py",
            "catalog/check_task_catalog.py", "catalog/task_catalog_rules.py",
            "documentation/check_documentation.py", "python/check_python_quality.py",
            "python/chinese_docstring_rule.py",
        }
        actual = {p.relative_to(REPOSITORY_DIR).as_posix() for p in REPOSITORY_DIR.rglob("*.py")}
        self.assertEqual(expected - actual, set())
        self.assertNotIn("__main__.py", actual)
        self.assertNotIn("hooks.py", actual)
        self.assertEqual([], list((REPO_ROOT / "scripts/repository").rglob("*.py")))

    def test_planning_check_has_cli_entry(self):
        """目录整理后真实 Catalog CLI 保持可调用。"""
        content = (REPOSITORY_DIR / "catalog/check_task_catalog.py").read_text(encoding="utf-8")
        self.assertIn("def main(", content)
        self.assertIn('if __name__', content)

    def test_planning_cli_real_positive_and_negative_fixtures(self):
        from scripts.checks.repository.catalog.check_task_catalog import main

        passed = subprocess.run(
            [sys.executable, "-m", "scripts.checks.repository.catalog.check_task_catalog"],
            cwd=REPO_ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, passed.returncode, passed.stderr)
        self.assertEqual("PASS", json.loads(passed.stdout)["status"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(REPO_ROOT / "planning", root / "planning")
            (root / "config/engineering").mkdir(parents=True)
            shutil.copy2(
                REPO_ROOT / "config/engineering/module-checks.yaml",
                root / "config/engineering/module-checks.yaml",
            )
            catalog = root / "planning" / "workstreams.yaml"
            catalog.write_text(catalog.read_text().replace(
                "catalog_mode: execution", "catalog_mode: planning-only", 1))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                failed_code = main([], root=root)
        self.assertNotEqual(0, failed_code)
        report = json.loads(output.getvalue())
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
    """scripts/harness/ must be retired; files moved to scripts/checks/repository/."""

    RETIRED_FILES = {
        "scripts/harness/docs_check.py",
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
