"""Regression tests for closed phase task-contract checks."""

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
        self.assertEqual(len(profiles), 26)
        for task_id, profile in profiles.items():
            with self.subTest(task_id=task_id):
                if profile["runner"] == "external":
                    with self.assertRaisesRegex(TaskContractError, "external-runner-required"):
                        evaluate_task(REPO, task_id)
                    continue
                result = evaluate_task(REPO, task_id)
                # Non-exit historical baseline contracts may truthfully be
                # BLOCKED after the catalog reset; they must never be inferred
                # as PASS merely because their checker ran.
                self.assertIn(result["status"], {"PASS", "BLOCKED"})
                self.assertTrue(result["inputs"])
                self.assertTrue(result["assertions"])

    def test_missing_required_heading_is_blocked_at_its_declared_locator(self):
        profile = load_profiles(REPO)["LF-TSK-ARCH-0008"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in ["harness/phase-task-contract-profiles.yaml", *profile["required_inputs"]]:
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            source = root / "docs/architecture/overview.md"
            source.write_text(source.read_text().replace("## 1.3. 主链路", "正文中的 1.3. 主链路"))
            result = evaluate_task(root, "LF-TSK-ARCH-0008")

        self.assertEqual(result["status"], "BLOCKED")
        blocked = [item for item in result["assertions"] if item["status"] == "BLOCKED"]
        self.assertEqual(blocked[0]["locator"], "docs/architecture/overview.md")
        self.assertEqual(blocked[0]["missing_headings"], ["1.3. 主链路"])

    def test_body_or_fenced_code_cannot_satisfy_a_heading_assertion(self):
        profile = load_profiles(REPO)["LF-TSK-ARCH-0008"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in ["harness/phase-task-contract-profiles.yaml", *profile["required_inputs"]]:
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            source = root / "docs/architecture/overview.md"
            source.write_text(source.read_text().replace(
                "## 1.3. 主链路", "```markdown\n## 1.3. 主链路\n```\n正文 1.3. 主链路"
            ))
            result = evaluate_task(root, "LF-TSK-ARCH-0008")

        blocked = [item for item in result["assertions"] if item["status"] == "BLOCKED"]
        self.assertEqual(blocked[0]["missing_headings"], ["1.3. 主链路"])

    def test_yaml_paths_are_checked_in_the_declared_yaml_file(self):
        profile = load_profiles(REPO)["LF-TSK-QLT-0001"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for locator in ["harness/phase-task-contract-profiles.yaml", *profile["required_inputs"]]:
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            runtime = root / "harness/agent-runtime.manifest.yaml"
            value = yaml.safe_load(runtime.read_text())
            del value["subagent_protocol"]["runner_bound_identity"]
            runtime.write_text(yaml.safe_dump(value, sort_keys=False))
            result = evaluate_task(root, "LF-TSK-QLT-0001")

        blocked = [item for item in result["assertions"] if item["status"] == "BLOCKED"]
        self.assertEqual(blocked[0]["missing_paths"], ["subagent_protocol.runner_bound_identity"])

    def test_merged_contracts_are_bound_inputs(self):
        profiles = load_profiles(REPO)
        required = {"LF-TSK-ARCH-0008": "docs/architecture/flows.md"}
        for task, source in required.items():
            with self.subTest(task=task):
                self.assertIn(source, profiles[task]["required_inputs"])
                self.assertIn(source, {item["locator"] for item in evaluate_task(REPO, task)["inputs"]})

    def test_g1_exit_requires_explicit_approval_marker(self):
        profiles = load_profiles(REPO)
        task_id = "LF-TSK-ARCH-0008"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locators = ["harness/phase-task-contract-profiles.yaml", *profiles[task_id]["required_inputs"]]
            for locator in locators:
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / locator, destination)
            decision = root / "docs/roadmap/phase-1-status.md"
            decision.write_text(decision.read_text().replace("G1 user decision: APPROVED", "G1 user decision: PENDING"))
            self.assertEqual(evaluate_task(root, task_id)["status"], "BLOCKED")
            decision.write_text(decision.read_text() + "\nG1 user decision: APPROVED\n")
            result = evaluate_task(root, task_id)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assertions"][-1]["id"], "user-approval-recorded")

    def test_unknown_task_and_symlinked_input_fail(self):
        with self.assertRaisesRegex(TaskContractError, "unknown-task"):
            evaluate_task(REPO, "LF-TSK-ARCH-9999")
        profiles = load_profiles(REPO)
        task_id = "LF-TSK-ARCH-0008"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "harness/phase-task-contract-profiles.yaml"
            profile_path.parent.mkdir(parents=True)
            shutil.copyfile(REPO / "harness/phase-task-contract-profiles.yaml", profile_path)
            for index, locator in enumerate(profiles[task_id]["required_inputs"]):
                destination = root / locator
                destination.parent.mkdir(parents=True, exist_ok=True)
                if index == 0:
                    destination.symlink_to(REPO / locator)
                else:
                    shutil.copyfile(REPO / locator, destination)
            with self.assertRaisesRegex(TaskContractError, "invalid-current-input"):
                evaluate_task(root, task_id)

    def test_profile_rejects_unsafe_or_noncanonical_evidence_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "harness/phase-task-contract-profiles.yaml"
            profile_path.parent.mkdir(parents=True)
            profile = yaml.safe_load((REPO / "harness/phase-task-contract-profiles.yaml").read_text())
            profile["profiles"]["LF-TSK-ARCH-0008"]["evidence_file"] = "../escape.json"
            profile_path.write_text(yaml.safe_dump(profile, sort_keys=False))
            with self.assertRaisesRegex(TaskContractError, "evidence_file"):
                load_profiles(root)

    def test_g1_exit_profile_cannot_drop_approval_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "harness/phase-task-contract-profiles.yaml"
            profile_path.parent.mkdir(parents=True)
            profile = yaml.safe_load((REPO / "harness/phase-task-contract-profiles.yaml").read_text())
            del profile["profiles"]["LF-TSK-ARCH-0008"]["approval_gate"]
            profile_path.write_text(yaml.safe_dump(profile, sort_keys=False))
            with self.assertRaisesRegex(TaskContractError, "requires an approval gate"):
                load_profiles(root)


if __name__ == "__main__":
    unittest.main()
