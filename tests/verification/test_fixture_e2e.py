"""End-to-end fixture tests for verification scenarios.

Uses real git repos and real subprocess execution (not mocks) to
validate the full pipeline from declarations through execution
to report generation.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.verification.scenarios import verify_changes, verify_repository


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


class TestFixtureRepositoryVerify(unittest.TestCase):
    """End-to-end test with real subprocess execution."""

    def test_passing_check_real_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "fixture.pass",
                "module": "fixture",
                "command": [
                    "python3", "-c",
                    "import json; print(json.dumps({'status': 'PASS'}))",
                ],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "repository-baseline",
                "triggers": [{"path": "fixture/"}],
                "required_environment": ["python3"],
                "consumed_inputs": [],
            }])
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["checks"][0]["status"], "PASS")
        self.assertEqual(report["checks"][0]["process"]["exit_code"], 0)

    def test_failing_check_real_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "fixture.fail",
                "module": "fixture",
                "command": [
                    "python3", "-c",
                    "import sys; print('error', file=sys.stderr); sys.exit(1)",
                ],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "repository-baseline",
                "triggers": [{"path": "fixture/"}],
                "required_environment": ["python3"],
                "consumed_inputs": [],
            }])
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["checks"][0]["status"], "FAIL")

    def test_missing_environment_real(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "fixture.blocked",
                "module": "fixture",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "repository-baseline",
                "triggers": [{"path": "fixture/"}],
                "required_environment": ["nonexistent-binary-xyz"],
                "consumed_inputs": [],
            }])
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(report["checks"][0]["reason"], "missing-environment")

    def test_report_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(root, [{
                "check_id": "fixture.json",
                "module": "fixture",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "repository-baseline",
                "triggers": [{"path": "fixture/"}],
                "required_environment": ["python3"],
                "consumed_inputs": [],
            }])
            report = verify_repository(tmpdir)
        serialized = json.dumps(report, ensure_ascii=False)
        deserialized = json.loads(serialized)
        self.assertEqual(deserialized["result"], report["result"])

    def test_invalid_planning_isolated_from_unrelated_product_execution(self):
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shutil.copytree(repository / "planning", root / "planning")
            # Inject a real defect; the repository may legitimately have no
            # decomposed product tasks in explicit planning-only mode.
            catalog_path = root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(catalog_path.read_text())
            catalog["workstreams"] = "invalid-topology"
            catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False))
            (root / "harness").mkdir()
            for name in ("agent-policy.manifest.yaml", "agent-runtime.manifest.yaml"):
                shutil.copy2(repository / "harness" / name, root / "harness" / name)
            marker = root / "product-ran"
            _write_declarations(root, [
                {
                    "check_id": "fixture.planning",
                    "module": "planning",
                    "command": ["python3", str(repository / "scripts/repository/planning_check.py"), "--root", "."],
                    "cwd": ".", "timeout_seconds": 30,
                    "scope": "repository-baseline", "triggers": [{"path": "planning/"}],
                    "required_environment": ["python3"],
                    "input_paths": ["planning", "harness/agent-policy.manifest.yaml", "harness/agent-runtime.manifest.yaml"],
                },
                {
                    "check_id": "fixture.product", "module": "product",
                    "command": ["python3", "-c", "from pathlib import Path; Path('product-ran').write_text('yes')"],
                    "cwd": ".", "timeout_seconds": 30,
                    "scope": "repository-baseline", "triggers": [{"path": "product/"}],
                    "required_environment": ["python3"], "input_paths": [],
                },
            ])
            report = verify_repository(root)
            self.assertEqual("yes", marker.read_text())
        self.assertEqual("FAIL", report["result"])
        self.assertEqual(["FAIL", "PASS"], [item["status"] for item in report["checks"]])


class TestFixtureChangeVerify(unittest.TestCase):
    """End-to-end test for change verification with real git."""

    def test_no_changes_real(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "fixture.change",
                "module": "fixture",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "change-targeted",
                "triggers": [{"path": "src/"}],
            }])
            report = verify_changes(tmpdir, base=base)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["scope_review"]["kind"], "no-changes")

    def test_matching_change_real(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "fixture.change",
                "module": "fixture",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "change-targeted",
                "triggers": [{"path": "src/"}],
                "required_environment": ["python3"],
                "consumed_inputs": [],
            }])
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("print('hello')")
            report = verify_changes(tmpdir, base=base)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(len(report["checks"]), 1)

    def test_coverage_gap_real(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            _write_declarations(root, [{
                "check_id": "fixture.change",
                "module": "fixture",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "change-targeted",
                "triggers": [{"path": "backend/"}],
            }])
            (root / "unrelated.txt").write_text("change")
            report = verify_changes(tmpdir, base=base)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertIn("no-check-matched", report["coverage_gaps"])

    def test_input_drift_real(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = _init_git_repo(root)
            (root / "input.txt").write_text("original")
            from scripts.verification.kernel import sha256_bytes
            original_hash = sha256_bytes((root / "input.txt").read_bytes())
            _write_declarations(root, [{
                "check_id": "fixture.drift",
                "module": "fixture",
                "command": ["python3", "-c", "pass"],
                "cwd": ".",
                "timeout_seconds": 30,
                "scope": "change-targeted",
                "triggers": [{"path": "src/"}],
                "consumed_inputs": [{
                    "locator": "input.txt",
                    "sha256": original_hash,
                }],
                "required_environment": ["python3"],
            }])
            # Modify the consumed input
            (root / "input.txt").write_text("modified")
            (root / "src").mkdir()
            (root / "src" / "file.py").write_text("code")
            report = verify_changes(tmpdir, base=base)
        self.assertEqual(report["checks"][0]["status"], "FAIL")
        self.assertEqual(report["checks"][0]["reason"], "input-drift")


if __name__ == "__main__":
    unittest.main()
