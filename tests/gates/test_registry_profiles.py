"""Deterministic registry projection tests for phase task-contract profiles."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.gates.registry_profiles import RegistryProfileError, check_registry, render_registry
from scripts.gates.task_contracts import load_profiles


REPO = Path(__file__).resolve().parents[2]


class RegistryProfileTests(unittest.TestCase):
    def test_current_registry_is_exact_profile_projection(self):
        result = check_registry(REPO)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["entry_count"], 35)
        self.assertEqual(result["profile_count"], 26)
        current = yaml.safe_load((REPO / "harness/gate-check-registry.yaml").read_text())
        self.assertEqual(current["registry_version"], 3)
        self.assertEqual(current["execution"]["source_scan"], "forbidden")
        self.assertEqual(current, render_registry(REPO))

    def test_all_profile_tasks_have_dispatch_contracts(self):
        profiles = load_profiles(REPO)
        catalog = yaml.safe_load((REPO / "planning/workstreams.yaml").read_text())
        tasks = {}
        for workstream in catalog["workstreams"]:
            for epic in workstream.get("epics", []):
                for capability in epic.get("capabilities", []):
                    for task in capability.get("seed_tasks", []):
                        tasks[task["id"]] = (workstream["id"], task)
        for task_id, profile in profiles.items():
            owner, task = tasks[task_id]
            with self.subTest(task_id=task_id):
                self.assertEqual(task["owner"], owner)
                self.assertEqual(profile["owner"], owner)
                self.assertIn(profile["evidence_file"], task["allowed_files"])
                self.assertTrue(task["file_claims"])
                self.assertTrue(
                    all(claim["owner"] == owner for claim in task["file_claims"])
                )
                self.assertTrue(task["validation_command"])
                self.assertTrue(task["discovered_from"])

    def test_external_profile_projects_its_fixed_command_and_timeout(self):
        registry = render_registry(REPO)
        entry = next(
            candidate
            for candidate in registry["entries"]
            if candidate["subject_task_id"] == "LF-TSK-DAT-0003"
        )
        self.assertEqual(entry["check_id"], "external.dat.0003")
        self.assertEqual(
            entry["fixed_argv"],
            ["python3", "scripts/toolchain/postgres_test.py", "verify", "--scope", "indexes"],
        )
        self.assertEqual(entry["timeout_seconds"], 300)

    def test_external_profile_command_argv_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in (
                "planning/workstreams.yaml",
                "harness/gate-check-registry.yaml",
                "harness/phase-task-contract-profiles.yaml",
            ):
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            profile_path = root / "harness/phase-task-contract-profiles.yaml"
            profile_document = yaml.safe_load(profile_path.read_text())
            profile_document["profiles"]["LF-TSK-DAT-0003"]["fixed_argv"][-1] = "verify-runtime"
            profile_path.write_text(yaml.safe_dump(profile_document, sort_keys=False))
            with self.assertRaisesRegex(RegistryProfileError, "command/argv mismatch"):
                render_registry(root)

    def test_generated_entry_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in (
                "planning/workstreams.yaml",
                "harness/gate-check-registry.yaml",
                "harness/phase-task-contract-profiles.yaml",
            ):
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            registry_path = root / "harness/gate-check-registry.yaml"
            registry = yaml.safe_load(registry_path.read_text())
            generated = next(entry for entry in registry["entries"] if entry["subject_task_id"] == "LF-TSK-ARCH-0008")
            generated["fixed_argv"][-1] = "LF-TSK-QLT-0001"
            registry_path.write_text(yaml.safe_dump(registry, sort_keys=False))
            with self.assertRaisesRegex(RegistryProfileError, "registry differs"):
                check_registry(root)

    def test_stale_registry_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in (
                "planning/workstreams.yaml",
                "harness/gate-check-registry.yaml",
                "harness/phase-task-contract-profiles.yaml",
            ):
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            registry_path = root / "harness/gate-check-registry.yaml"
            registry = yaml.safe_load(registry_path.read_text())
            registry["registry_version"] = 1
            registry_path.write_text(yaml.safe_dump(registry, sort_keys=False))
            with self.assertRaisesRegex(RegistryProfileError, "version must be 3"):
                render_registry(root)

    def test_execution_contract_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in (
                "planning/workstreams.yaml",
                "harness/gate-check-registry.yaml",
                "harness/phase-task-contract-profiles.yaml",
            ):
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            registry_path = root / "harness/gate-check-registry.yaml"
            registry = yaml.safe_load(registry_path.read_text())
            registry["execution"]["source_scan"] = "allowed"
            registry_path.write_text(yaml.safe_dump(registry, sort_keys=False))
            with self.assertRaisesRegex(RegistryProfileError, "execution contract mismatch"):
                render_registry(root)


if __name__ == "__main__":
    unittest.main()
