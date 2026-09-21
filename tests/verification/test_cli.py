"""Tests for CLI entry points: check_changes.py and check_repository.py.

Covers: argument parsing, exit codes, JSON output, and public API
surface.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

_REPO_ROOT = str(Path(__file__).resolve().parents[2])


def _repo_env() -> dict[str, str]:
    """Build env with PYTHONPATH pointing to the repo root."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _REPO_ROOT + (":" + existing if existing else "")
    return env


def _init_git_repo(tmpdir: Path) -> str:
    subprocess.run(["git", "init"], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (tmpdir / "initial.txt").write_text("initial")
    (tmpdir / ".gitignore").write_text("harness/\n")
    subprocess.run(["git", "add", "."], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(tmpdir),
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _complete_check(check: dict) -> dict:
    complete = dict(check)
    complete.setdefault("module_dependencies", [])
    complete.setdefault("required_environment", [])
    complete.setdefault("input_paths", [])
    complete.setdefault("result_contract", {"type": "exit-code", "completeness_guarantee": "fixture-owned command completion"})
    return complete


def _write_declarations(root: Path, checks: list[dict]) -> None:
    harness = root / "harness"
    harness.mkdir(exist_ok=True)
    data = {
        "schema_version": "lexiflow.module-checks.v1",
        "checks": [_complete_check(check) for check in checks],
    }
    (harness / "module-checks.yaml").write_text(
        yaml.dump(data, default_flow_style=False), encoding="utf-8",
    )


class TestCheckRepositoryCLI(unittest.TestCase):
    def test_exit_code_pass(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "cli.pass",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "repository-baseline",
                "triggers": [{"path": "test/"}],
                "required_environment": ["python3"],
                "consumed_inputs": [],
            }])
            r = subprocess.run(
                [sys.executable, os.path.join(_REPO_ROOT, "scripts", "check_repository.py"),
                 "--repo-root", str(root)],
                capture_output=True, text=True,
                cwd=_REPO_ROOT, env=_repo_env(),
            )
            report = json.loads(r.stdout)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(r.returncode, 0)

    def test_exit_code_fail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            r = subprocess.run(
                [sys.executable, os.path.join(_REPO_ROOT, "scripts", "check_repository.py"),
                 "--repo-root", str(root)],
                capture_output=True, text=True,
                cwd=_REPO_ROOT, env=_repo_env(),
            )
            report = json.loads(r.stdout)
        self.assertEqual(report["result"], "FAIL")
        self.assertNotEqual(r.returncode, 0)

    def test_json_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            r = subprocess.run(
                [sys.executable, os.path.join(_REPO_ROOT, "scripts", "check_repository.py"),
                 "--repo-root", str(root)],
                capture_output=True, text=True,
                cwd=_REPO_ROOT, env=_repo_env(),
            )
            report = json.loads(r.stdout)
        self.assertIn("schema_version", report)
        self.assertIn("result", report)


class TestCheckChangesCLI(unittest.TestCase):
    def test_no_changes_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "cli.change",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "test/"}],
            }])
            r = subprocess.run(
                [sys.executable, os.path.join(_REPO_ROOT, "scripts", "check_changes.py"),
                 "--repo-root", str(root), "--base", base],
                capture_output=True, text=True,
                cwd=_REPO_ROOT, env=_repo_env(),
            )
            report = json.loads(r.stdout)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(r.returncode, 0)

    def test_expected_path_argument(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "cli.change",
                "module": "test",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 10,
                "scope": "change-targeted",
                "triggers": [{"path": "test/"}],
            }])
            r = subprocess.run(
                [sys.executable, os.path.join(_REPO_ROOT, "scripts", "check_changes.py"),
                 "--repo-root", str(root), "--base", base,
                 "--expected-path", "backend/"],
                capture_output=True, text=True,
                cwd=_REPO_ROOT, env=_repo_env(),
            )
            report = json.loads(r.stdout)
        self.assertIn("scope_review", report)


if __name__ == "__main__":
    unittest.main()
