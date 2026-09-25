"""CLI 合同：日常入口零参数，诊断不发布正式报告。"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts import check_changes, check_repository
from scripts.verification import diagnose as diagnostic_cli


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "initial.txt").write_text("initial")
    (root / ".gitignore").write_text("harness/\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _write_declarations(root: Path, *, scope: str, result: str = "PASS") -> None:
    (root / "harness").mkdir(exist_ok=True)
    command = ["python3", "-c", "pass" if result == "PASS" else "raise SystemExit(1)"]
    declaration = {
        "check_id": "cli.fixture",
        "module": "test",
        "command": command,
        "executable": "python3",
        "cwd": ".",
        "timeout_seconds": 10,
        "scope": scope,
        "triggers": [{"path": "test/"}],
        "module_dependencies": [],
        "required_environment": ["python3"],
        "input_paths": ["initial.txt"],
        "result_contract": {"type": "exit-code", "completeness_guarantee": "fixture-owned command completion"},
    }
    (root / "harness/module-checks.yaml").write_text(yaml.safe_dump({"schema_version": "lexiflow.module-checks.v1", "checks": [declaration]}))


def _invoke(function, argv: list[str], root: Path) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = function(argv, root=root)
    return code, json.loads(output.getvalue())


class TestDailyAndDiagnosticCLI(unittest.TestCase):
    def test_repository_daily_pass_and_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "initial.txt").write_text("fixture")
            _write_declarations(root, scope="repository-baseline")
            code, report = _invoke(check_repository.main, [], root)
            self.assertEqual((code, report["result"]), (0, "PASS"))
            _write_declarations(root, scope="repository-baseline", result="FAIL")
            code, report = _invoke(check_repository.main, [], root)
            self.assertEqual((code, report["result"]), (1, "FAIL"))

    def test_change_daily_and_diagnostic_have_distinct_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = _init_git_repo(root)
            _write_declarations(root, scope="change-targeted")
            code, report = _invoke(check_changes.main, [], root)
            self.assertEqual((code, report["result"]), (0, "PASS"))
            diagnostic = diagnostic_cli.diagnose(root, "change", base=base, expected_paths=("backend/",))
            self.assertEqual(diagnostic["kind"], "diagnostic")
            self.assertIs(diagnostic["full_repository_executed"], False)
            self.assertIn("scope_review", diagnostic["selected_report"])

    def test_repository_selection_is_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "initial.txt").write_text("fixture")
            _write_declarations(root, scope="repository-baseline")
            result = diagnostic_cli.diagnose(root, "repository", check_ids=("cli.fixture",))
            self.assertEqual(result["selected_result"], "PASS")
            self.assertIs(result["full_repository_executed"], False)
            self.assertNotIn("publication", result["selected_report"])
            self.assertNotIn("result", result["selected_report"])
            self.assertNotIn("scope", result["selected_report"])

    def test_daily_overrides_are_rejected(self):
        for entry, option in ((check_changes.main, "--base"), (check_repository.main, "--check-id")):
            with self.subTest(option=option), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                entry([option, "fixture"])


if __name__ == "__main__":
    unittest.main()
