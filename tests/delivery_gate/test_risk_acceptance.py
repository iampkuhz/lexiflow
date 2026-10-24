"""风险选择的真实 Gate 链保持独立验证，且不伪造缺席审查。"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import yaml

from scripts.acceptance.task_definition.task_acceptance_plan import verify_plan
from scripts.acceptance.task_operations.evaluate_task_acceptance import check_conditions
from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import CandidateProofError, _extract
from scripts.acceptance.evidence.verify_existing_acceptance_chain import consume_existing_pass
from scripts.acceptance.evidence.acceptance_record_store import RecordError, load_submission
from scripts.acceptance.task_operations.record_independent_task_review import ReviewError, review
from scripts.acceptance.task_operations.submit_task_for_acceptance import submit
from scripts.acceptance.task_operations.run_independent_task_validation import ValidationError, validate_batch
from scripts.verification import freeze_inputs, persist_report, verify_profile
from tests.delivery_gate.fixtures import (
    DeliveryGateFixture,
    PRODUCER_SESSION,
    VALIDATOR_SESSION,
    TASK_ID,
    make_mock_runtime,
)
from scripts.verification.planning.check_definitions import load_declarations
from tests.delivery_gate.fixtures import write_declarations


class RiskAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.addCleanup(self.f.cleanup)
        root = self.f.root
        path = root / "planning/workstreams.yaml"
        catalog = yaml.safe_load(path.read_text())
        task = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0]
        task["allowed_files"] = ["docs/user/**"]
        task["file_claims"] = [{"path": "docs/user/**"}]
        path.write_text(yaml.safe_dump(catalog))
        path = root / "config/engineering/module-checks.yaml"
        checks = load_declarations(root)
        for check in checks["checks"]:
            if check["check_id"].startswith("fixture.") and check["check_id"] in {
                "fixture.baseline",
                "fixture.change",
            }:
                check["triggers"] = [{"path": "docs/user/"}]
                check["input_paths"].append("docs/user")
        write_declarations(path, checks["checks"])
        self.f.write("docs/user/help.md", "简单说明。\n")
        self.f._git("add", ".")
        self.f._git("commit", "-m", "local contract fixture")
        self.f.write("docs/user/help.md", "清楚的说明。\n")

    def test_acceptance_levels_are_implementation_owned_without_policy_reads(self):
        from scripts.acceptance.task_definition.task_acceptance_plan import acceptance_plan
        from scripts.verification.planning.change_risk_assessment import RISK_LEVELS, INDEPENDENT_REVIEW_LEVELS, RISK_ASSESSMENT_SCHEMA

        for level in RISK_LEVELS:
            with self.subTest(level=level), patch(
                "pathlib.Path.read_bytes",
                side_effect=AssertionError("human rules must not be machine inputs"),
            ):
                plan = acceptance_plan(
                    self.f.root,
                    {"schema_version": RISK_ASSESSMENT_SCHEMA, "level": level, "coverage_gaps": [], "required_check_ids": []},
                    {"required_check_ids": []},
                )
            self.assertTrue(plan["validator_independent"])
            self.assertEqual(
                ["TASK_VALIDATION"] + (["INDEPENDENT_REVIEW"] if level in INDEPENDENT_REVIEW_LEVELS else []),
                plan["required_layers"],
            )

    def _submit(self):
        freeze = freeze_inputs(
            self.f.root, verification_scope="development-change", base="HEAD"
        )
        self.assertEqual("PASS", freeze["result"], freeze)
        report = verify_profile(self.f.root, frozen_inputs=freeze)
        self.assertEqual("PASS", report["result"], report)
        persist_report(self.f.root, report)
        with patch(
            "scripts.agents.identity.execution_identity.discover",
            return_value=make_mock_runtime(PRODUCER_SESSION),
        ):
            result = submit(
                self.f.root,
                task_id=TASK_ID,
                change_report_id=report["run_id"],
            )
        self.assertEqual("PASS", result["result"])
        return result["submission_id"]

    def test_local_chain_requires_independent_validation_without_review_receipt(self):
        sid = self._submit()
        submission = load_submission(self.f.root, sid)
        self.assertEqual("local-function", submission["risk_assessment"]["level"])
        self.assertEqual(
            ["TASK_VALIDATION"], submission["acceptance_plan"]["required_layers"]
        )
        with patch(
            "scripts.agents.identity.execution_identity.discover",
            return_value=make_mock_runtime(PRODUCER_SESSION),
        ):
            with self.assertRaisesRegex(ValidationError, "self-validation-forbidden"):
                validate_batch(self.f.root, submission_ids=[sid])[0]
        with patch(
            "scripts.agents.identity.execution_identity.discover",
            return_value=make_mock_runtime(VALIDATOR_SESSION),
        ):
            validation = validate_batch(self.f.root, submission_ids=[sid])[0]
        self.assertEqual("PASS", validation["result"], validation)
        with self.assertRaisesRegex(ReviewError, "review-not-required"):
            review(
                self.f.root,
                validation_id=validation["validation_id"],
                findings=[],
            )
        checked = check_conditions(self.f.root, submission_id=sid)
        self.assertEqual("PASS", checked["result"], checked)
        consumed = consume_existing_pass(self.f.root, submission_id=sid)
        self.assertEqual("PASS", consumed["result"], consumed)
        self.assertIsNone(consumed["proof"]["review_id"])
        self.assertIsNone(consumed["proof"]["review_content_hash"])
        self.assertFalse(checked["conditions"]["review_required"])
        with self.assertRaisesRegex(
            CandidateProofError, "verification-report-not-full-pass"
        ):
            _extract(self.f.root, consumed["proof"])

    def test_frozen_local_subject_allows_unrelated_writes_not_dependency_drift(self):
        sid = self._submit()
        self.f.write("unrelated.txt", "not part of frozen dependency closure\n")
        with patch(
            "scripts.agents.identity.execution_identity.discover",
            return_value=make_mock_runtime(VALIDATOR_SESSION),
        ):
            validation = validate_batch(self.f.root, submission_ids=[sid])[0]
        self.assertEqual("PASS", validation["result"], validation)
        self.f.write("docs/user/related.md", "新增依赖输入。\n")
        checked = check_conditions(self.f.root, submission_id=sid)
        self.assertEqual("BLOCKED", checked["result"], checked)

    def test_required_release_check_upgrades_local_change_and_requires_review(self):
        path = self.f.root / "planning/workstreams.yaml"
        catalog = yaml.safe_load(path.read_text())
        task = catalog["workstreams"][0]["epics"][0]["capabilities"][0]["seed_tasks"][0]
        module_path = self.f.root / "config/engineering/module-checks.yaml"
        declaration = yaml.safe_load(module_path.read_text())
        release_id = "eng.release.candidate-runtime"
        declaration["scenario_policy"]["ci_quick"]["formal_only_check_ids"] = [
            release_id
        ]
        module_path.write_text(yaml.safe_dump(declaration))
        task["required_check_ids"].append(release_id)
        path.write_text(yaml.safe_dump(catalog))
        self.f._git("add", "planning/workstreams.yaml", "config/engineering/module-checks.yaml")
        self.f._git("commit", "-m", "release requirement fixture")
        sid = self._submit()
        submission = load_submission(self.f.root, sid)
        self.assertEqual("formal-release", submission["risk_assessment"]["level"])
        self.assertEqual(
            "repository-baseline", submission["acceptance_plan"]["verification_scope"]
        )
        self.assertIn(
            release_id,
            {item["check_id"] for item in submission["verification_freeze"]["checks"]},
        )
        with patch(
            "scripts.agents.identity.execution_identity.discover",
            return_value=make_mock_runtime(VALIDATOR_SESSION),
        ):
            validation = validate_batch(self.f.root, submission_ids=[sid])[0]
        self.assertEqual("PASS", validation["result"], validation)
        result = check_conditions(self.f.root, submission_id=sid)
        self.assertEqual(
            ("BLOCKED", "review-missing"), (result["result"], result["reason"])
        )

    def test_plan_level_and_subject_tampering_fail_closed(self):
        sid = self._submit()
        original = load_submission(self.f.root, sid)
        for field in ("level", "required_layers", "changed_files"):
            forged = copy.deepcopy(original)
            if field == "level":
                forged["risk_assessment"][field] = "mechanical"
            elif field == "required_layers":
                forged["acceptance_plan"][field] = []
            else:
                forged["risk_assessment"][field] = []
            with self.subTest(field=field), self.assertRaises(RecordError):
                verify_plan(self.f.root, forged)


if __name__ == "__main__":
    unittest.main()
