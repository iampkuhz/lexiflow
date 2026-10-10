"""End-to-end fixture tests for verification scenarios.

Uses real git repos and real subprocess execution (not mocks) to
validate the full pipeline from declarations through execution
to report generation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.verification.execution.check_plan_runner import verify_repository
from scripts.verification.commands.diagnose_repository_check import diagnose
from tests.verification.declaration_fixtures import write_declarations


def _init_git_repo(tmpdir: Path) -> str:
    subprocess.run(
        ["git", "init"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (tmpdir / "initial.txt").write_text("initial")
    (tmpdir / ".gitignore").write_text("config/\ntmp/\n")
    subprocess.run(
        ["git", "add", "."],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmpdir),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(tmpdir),
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _complete_check(check: dict) -> dict:
    complete = dict(check)
    complete.setdefault("module_dependencies", [])
    complete.setdefault("required_environment", [])
    complete.setdefault("input_paths", [])
    complete.setdefault(
        "result_contract",
        {
            "type": "exit-code",
            "completeness_guarantee": "fixture-owned command completion",
        },
    )
    return complete


def _write_declarations(root: Path, checks: list[dict]) -> None:
    write_declarations(
        root / "config" / "engineering" / "module-checks.yaml",
        [_complete_check(check) for check in checks],
    )


class TestFixtureRepositoryVerify(unittest.TestCase):
    """End-to-end test with real subprocess execution."""

    def test_passing_check_real_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "fixture.pass",
                        "command": [
                            "python3",
                            "-c",
                            "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "fixture/"}],
                        "required_environment": ["python3"],
                    }
                ],
            )
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["checks"][0]["status"], "PASS")
        self.assertEqual(report["checks"][0]["process"]["exit_code"], 0)

    def test_failing_check_real_execution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "fixture.fail",
                        "command": [
                            "python3",
                            "-c",
                            "import sys; print('error', file=sys.stderr); sys.exit(1)",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "fixture/"}],
                        "required_environment": ["python3"],
                    }
                ],
            )
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["checks"][0]["status"], "FAIL")

    def test_missing_environment_real(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "fixture.blocked",
                        "command": [
                            "python3",
                            "-c",
                            "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "fixture/"}],
                        "required_environment": ["nonexistent-binary-xyz"],
                    }
                ],
            )
            report = verify_repository(tmpdir)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(report["checks"][0]["reason"], "missing-environment")

    def test_report_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "fixture.json",
                        "command": [
                            "python3",
                            "-c",
                            "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "fixture/"}],
                        "required_environment": ["python3"],
                    }
                ],
            )
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
            target = root / "scripts/checks/repository/catalog/task_catalog_rules.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository / "scripts/checks/repository/catalog/task_catalog_rules.py", target)
            marker = root / "product-ran"
            _write_declarations(
                root,
                [
                    {
                        "check_id": "fixture.planning",
                        "command": [
                            "python3",
                            "-c",
                            "from contextlib import redirect_stdout; from pathlib import Path; import sys,json; from scripts.checks.repository.catalog.check_task_catalog import main; rc=main([], root=Path('.')); print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''})); raise SystemExit(rc)",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "planning/"}],
                        "required_environment": ["python3"],
                        "input_paths": [
                            "planning",
                            "scripts/checks/repository/catalog/task_catalog_rules.py",
                        ],
                    },
                    {
                        "check_id": "fixture.product",
                        "command": [
                            "python3",
                            "-c",
                            "from pathlib import Path; import json; Path('product-ran').write_text('yes'); print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "product/"}],
                        "required_environment": ["python3"],
                        "input_paths": [],
                    },
                ],
            )
            report = diagnose(root, check_ids=("fixture.planning", "fixture.product"))
            self.assertEqual("yes", marker.read_text())
        self.assertEqual("FAIL", report["selected_result"])
        self.assertEqual(
            ["FAIL", "PASS"],
            [item["status"] for item in report["selected_report"]["checks"]],
        )

    def test_delivery_stops_product_after_invalid_planning(self):
        repository = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shutil.copytree(repository / "planning", root / "planning")
            catalog_path = root / "planning/workstreams.yaml"
            catalog = yaml.safe_load(catalog_path.read_text())
            catalog["workstreams"] = "invalid-topology"
            catalog_path.write_text(yaml.safe_dump(catalog, sort_keys=False))
            target = root / "scripts/checks/repository/catalog/task_catalog_rules.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repository / "scripts/checks/repository/catalog/task_catalog_rules.py", target)
            _write_declarations(
                root,
                [
                    {
                        "check_id": "fixture.planning",
                        "command": [
                            "python3",
                            "-c",
                            "from pathlib import Path; import json; from scripts.checks.repository.catalog.check_task_catalog import main; rc=main([], root=Path('.')); print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''})); raise SystemExit(rc)",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "planning/"}],
                        "required_environment": ["python3"],
                        "input_paths": [
                            "planning",
                            "scripts/checks/repository/catalog/task_catalog_rules.py",
                        ],
                    },
                    {
                        "check_id": "fixture.product",
                        "command": [
                            "python3",
                            "-c",
                            "from pathlib import Path; import json; Path('product-ran').write_text('yes'); print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
                        ],
                        "cwd": ".",
                        "timeout_seconds": 30,
                        "scope": "repository-baseline",
                        "triggers": [{"path": "product/"}],
                        "required_environment": ["python3"],
                        "input_paths": [],
                    },
                ],
            )
            report = verify_repository(root)
            self.assertFalse((root / "product-ran").exists())
        self.assertEqual("FAIL", report["result"])
        self.assertEqual(
            ["FAIL", "BLOCKED"], [item["status"] for item in report["checks"]]
        )
        self.assertEqual("not-run", report["checks"][1]["process"]["exit_reason"])


if __name__ == "__main__":
    unittest.main()
