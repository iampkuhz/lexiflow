"""Tests for delivery gate module dependency direction.

Verifies:
- delivery_gate -> verification (public API) is allowed
- delivery_gate -> agents (public facts) is allowed
- agents -> delivery_gate is forbidden
- verification -> delivery_gate is forbidden
- delivery gate does not import root CLI or create execution callbacks
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path


DELIVERY_GATE_ROOT = Path(__file__).resolve().parents[2] / "scripts" / "delivery_gate"
AGENTS_ROOT = Path(__file__).resolve().parents[2] / "scripts" / "agents"
VERIFICATION_ROOT = Path(__file__).resolve().parents[2] / "scripts" / "verification"


def _collect_imports(directory: Path) -> list[tuple[str, str]]:
    """Return (file, imported_module) pairs for all Python files in directory."""
    imports: list[tuple[str, str]] = []
    if not directory.is_dir():
        return imports
    for py_file in sorted(directory.rglob("*.py")):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((str(py_file.relative_to(directory.parent.parent)), alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append((str(py_file.relative_to(directory.parent.parent)), node.module))
    return imports


def _collect_dynamic_imports(directory: Path) -> list[tuple[str, str]]:
    """Find literal __import__/importlib imports, including lazy function bodies."""
    found: list[tuple[str, str]] = []
    for py_file in sorted(directory.rglob("*.py")):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            literal = node.args[0]
            if not isinstance(literal, ast.Constant) or not isinstance(literal.value, str):
                continue
            direct = isinstance(node.func, ast.Name) and node.func.id == "__import__"
            via_importlib = (isinstance(node.func, ast.Attribute)
                             and node.func.attr == "import_module")
            if direct or via_importlib:
                found.append((str(py_file.relative_to(directory.parent.parent)), literal.value))
    return found


class TestDeliveryGateDependencyDirection(unittest.TestCase):
    """Delivery Gate module dependency rules."""

    def test_delivery_gate_can_import_verification(self) -> None:
        imports = _collect_imports(DELIVERY_GATE_ROOT)
        verification_imports = [m for _, m in imports if m.startswith("scripts.verification")]
        self.assertTrue(
            any(m == "scripts.verification" for m in verification_imports),
            "delivery gate should import scripts.verification public API",
        )

    def test_delivery_gate_can_import_agents_public(self) -> None:
        imports = _collect_imports(DELIVERY_GATE_ROOT)
        agents_imports = [m for _, m in imports if m.startswith("scripts.agents")]
        self.assertTrue(
            len(agents_imports) > 0,
            "delivery gate should import agents public facts (e.g. local_codex_runtime)",
        )


class TestReverseDependenciesForbidden(unittest.TestCase):
    """Reverse dependencies must not exist."""

    def test_agents_does_not_import_delivery_gate(self) -> None:
        imports = _collect_imports(AGENTS_ROOT)
        delivery_gate_imports = [m for _, m in imports if "scripts.delivery_gate" in m]
        self.assertEqual(
            delivery_gate_imports, [],
            f"agents must not import delivery_gate: {delivery_gate_imports}",
        )

    def test_verification_does_not_import_delivery_gate(self) -> None:
        imports = _collect_imports(VERIFICATION_ROOT)
        delivery_gate_imports = [m for _, m in imports if "scripts.delivery_gate" in m]
        self.assertEqual(
            delivery_gate_imports, [],
            f"verification must not import delivery_gate: {delivery_gate_imports}",
        )

    def test_lazy_reverse_imports_are_forbidden(self) -> None:
        for directory in (AGENTS_ROOT, VERIFICATION_ROOT):
            imports = _collect_dynamic_imports(directory)
            reverse = [(file, name) for file, name in imports
                       if name == "scripts.delivery_gate" or name.startswith("scripts.delivery_gate.")]
            self.assertEqual([], reverse, f"lazy reverse imports found: {reverse}")

    def test_product_sources_do_not_depend_on_engineering_control_plane(self) -> None:
        root = Path(__file__).resolve().parents[2]
        forbidden = ("scripts.agents", "scripts.delivery_gate", "scripts.verification",
                     "scripts.repository")
        violations = []
        for base in (root / "backend", root / "extension" / "src"):
            for source in base.rglob("*"):
                if not source.is_file() or "build" in source.parts or "dist" in source.parts:
                    continue
                if source.suffix not in {".java", ".kt", ".kts", ".ts", ".js", ".mjs"}:
                    continue
                text = source.read_text(errors="replace")
                for name in forbidden:
                    if name in text:
                        violations.append(f"{source.relative_to(root)}: {name}")
        self.assertEqual([], violations)


class TestNoCLICallback(unittest.TestCase):
    """Delivery Gate internal API must not callback to outer CLI."""

    def test_no_subprocess_cli_callback_in_review(self) -> None:
        review_file = DELIVERY_GATE_ROOT / "review.py"
        if not review_file.is_file():
            self.skipTest("review.py not yet created")
        source = review_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                self.fail("review.py must not import subprocess (no CLI callback)")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        self.fail("review.py must not import subprocess (no CLI callback)")

    def test_no_subprocess_cli_callback_in_check(self) -> None:
        check_file = DELIVERY_GATE_ROOT / "check.py"
        if not check_file.is_file():
            self.skipTest("check.py not yet created")
        source = check_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                self.fail("check.py must not import subprocess (no CLI callback)")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        self.fail("check.py must not import subprocess (no CLI callback)")


class TestIdentityFromRuntime(unittest.TestCase):
    """Identity must come from runtime discovery, not caller parameters."""

    def test_validate_uses_discover(self) -> None:
        validate_file = DELIVERY_GATE_ROOT / "validate.py"
        if not validate_file.is_file():
            self.skipTest("validate.py not yet created")
        source = validate_file.read_text()
        self.assertIn("discover", source, "validate.py must use runtime discovery")
        self.assertIn("_discover_runtime", source, "validate.py must have _discover_runtime function")

    def test_review_uses_discover(self) -> None:
        review_file = DELIVERY_GATE_ROOT / "review.py"
        if not review_file.is_file():
            self.skipTest("review.py not yet created")
        source = review_file.read_text()
        self.assertIn("discover", source, "review.py must use runtime discovery")
        self.assertIn("_discover_runtime", source, "review.py must have _discover_runtime function")

    def test_submit_uses_discover(self) -> None:
        submit_file = DELIVERY_GATE_ROOT / "submit.py"
        if not submit_file.is_file():
            self.skipTest("submit.py not yet created")
        source = submit_file.read_text()
        self.assertIn("discover", source, "submit.py must use runtime discovery")
        self.assertIn("_discover_runtime", source, "submit.py must have _discover_runtime function")


if __name__ == "__main__":
    unittest.main()
