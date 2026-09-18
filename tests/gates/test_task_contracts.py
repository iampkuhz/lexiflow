"""Regression tests for closed G1 current-input task-contract checks."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from scripts.gates.task_contracts import TaskContractError, evaluate_task, load_profiles


REPO = Path(__file__).resolve().parents[2]


class TaskContractTests(unittest.TestCase):
    def test_current_profile_is_closed_and_has_expected_outcomes(self):
        profiles = load_profiles(REPO)
        self.assertEqual(len(profiles), 21)
        for task_id, profile in profiles.items():
            with self.subTest(task_id=task_id):
                if profile["runner"] == "external":
                    with self.assertRaisesRegex(TaskContractError, "external-runner-required"):
                        evaluate_task(REPO, task_id)
                    continue
                result = evaluate_task(REPO, task_id)
                expected = "BLOCKED" if task_id == "LF-TSK-ARCH-0008" else "PASS"
                self.assertEqual(result["status"], expected)
                self.assertTrue(result["inputs"])
                self.assertTrue(result["assertions"])

    def test_missing_semantic_term_is_blocked(self):
        profile = load_profiles(REPO)["LF-TSK-ARCH-0001"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in ["harness/g1-task-contract-profiles.yaml", *profile["required_inputs"]]:
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            for locator in profile["required_inputs"]:
                source = root / locator
                source.write_text(source.read_text().replace("英文优先", "已移除的约束"))
            result = evaluate_task(root, "LF-TSK-ARCH-0001")

        self.assertEqual(result["status"], "BLOCKED")
        blocked = [item for item in result["assertions"] if item["status"] == "BLOCKED"]
        self.assertEqual(blocked[0]["missing_terms"], ["英文优先"])

    def test_merged_contracts_are_bound_inputs(self):
        profiles = load_profiles(REPO)
        required = {
            "LF-TSK-ARCH-0002": "docs/architecture/modules-and-dependencies.md",
            "LF-TSK-ARCH-0003": "docs/architecture/modules-and-dependencies.md",
            "LF-TSK-ARCH-0004": "docs/architecture/caption-and-learning-flows.md",
            "LF-TSK-ARCH-0005": "docs/architecture/caption-and-learning-flows.md",
        }
        for task, source in required.items():
            with self.subTest(task=task):
                self.assertIn(source, profiles[task]["required_inputs"])
                self.assertIn(source, {item["locator"] for item in evaluate_task(REPO, task)["inputs"]})

    def test_g1_exit_requires_explicit_approval_marker(self):
        profiles = load_profiles(REPO)
        task_id = "LF-TSK-ARCH-0008"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locators = ["harness/g1-task-contract-profiles.yaml", *profiles[task_id]["required_inputs"]]
            for locator in locators:
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            decision = root / "docs/reviews/g1-decision-package.md"
            decision.write_text(decision.read_text() + "\nG1 user decision: APPROVED\n")
            result = evaluate_task(root, task_id)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assertions"][-1]["id"], "user-approval-recorded")

    def test_unknown_task_and_symlinked_input_fail(self):
        with self.assertRaisesRegex(TaskContractError, "unknown-task"):
            evaluate_task(REPO, "LF-TSK-ARCH-9999")
        profiles = load_profiles(REPO)
        task_id = "LF-TSK-ARCH-0001"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "harness/g1-task-contract-profiles.yaml"
            profile_path.parent.mkdir(parents=True)
            shutil.copyfile(REPO / "harness/g1-task-contract-profiles.yaml", profile_path)
            for locator in profiles[task_id]["required_inputs"]:
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                if locator.endswith("product-brief.md"):
                    destination.symlink_to(REPO / locator)
                else:
                    shutil.copyfile(REPO / locator, destination)
            with self.assertRaisesRegex(TaskContractError, "invalid-current-input"):
                evaluate_task(root, task_id)

    def test_profile_rejects_unsafe_or_noncanonical_evidence_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "harness/g1-task-contract-profiles.yaml"
            profile_path.parent.mkdir(parents=True)
            profile = yaml.safe_load((REPO / "harness/g1-task-contract-profiles.yaml").read_text())
            profile["profiles"]["LF-TSK-ARCH-0001"]["evidence_file"] = "../escape.json"
            profile_path.write_text(yaml.safe_dump(profile, sort_keys=False))
            with self.assertRaisesRegex(TaskContractError, "evidence_file"):
                load_profiles(root)

    def test_g1_exit_profile_cannot_drop_approval_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "harness/g1-task-contract-profiles.yaml"
            profile_path.parent.mkdir(parents=True)
            profile = yaml.safe_load((REPO / "harness/g1-task-contract-profiles.yaml").read_text())
            del profile["profiles"]["LF-TSK-ARCH-0008"]["approval_gate"]
            profile_path.write_text(yaml.safe_dump(profile, sort_keys=False))
            with self.assertRaisesRegex(TaskContractError, "requires an approval gate"):
                load_profiles(root)


if __name__ == "__main__":
    unittest.main()
