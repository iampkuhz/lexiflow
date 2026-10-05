from __future__ import annotations

import unittest
import copy
import yaml

from scripts.delivery_gate.task_subject import derive_task_subject
from tests.delivery_gate.fixtures import DeliveryGateFixture, TASK_ID
from scripts.delivery_gate.requirements import load_task_requirements


class TestTaskSubject(unittest.TestCase):
    def setUp(self):
        self.fixture = DeliveryGateFixture()
        self.requirements = load_task_requirements(self.fixture.root, TASK_ID)

    def tearDown(self):
        self.fixture.cleanup()

    def test_empty_real_diff_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "task-subject-empty"):
            derive_task_subject(self.fixture.root, self.requirements, [])

    def test_forbidden_and_claims_are_enforced(self):
        self.requirements["allowed_files"] = ["secrets/**"]
        with self.assertRaisesRegex(ValueError, "forbidden-scope"):
            derive_task_subject(self.fixture.root, self.requirements, ["secrets/key"])
        self.requirements["allowed_files"] = ["src/**"]
        self.requirements["file_claims"] = ["src/test.py"]
        with self.assertRaisesRegex(ValueError, "outside-file-claim"):
            derive_task_subject(self.fixture.root, self.requirements, ["src/other.py"])

    def test_task_scope_is_derived_without_override(self):
        self.fixture.write("src/test.py", "changed")
        subject = derive_task_subject(
            self.fixture.root, self.requirements, ["src/test.py"]
        )
        self.assertEqual(subject["task_paths"], ["src/test.py"])
        self.assertEqual(subject["changed_files"], ["src/test.py"])

    def test_actual_check_input_is_dependency_not_task_body(self):
        self.fixture.write("src/test.py", "changed")
        self.fixture.write("protected.py", "dependency changed")
        subject = derive_task_subject(
            self.fixture.root, self.requirements,
            ["src/test.py", "protected.py"],
        )
        self.assertEqual(subject["task_paths"], ["src/test.py"])
        self.assertEqual(subject["dependency_paths"], ["protected.py"])
        self.assertEqual(subject["changed_files"], ["protected.py", "src/test.py"])

    def test_dependency_expansion_reaches_fixed_point(self):
        path = self.fixture.root / "harness/module-checks.yaml"
        declarations = yaml.safe_load(path.read_text())
        first = next(x for x in declarations["checks"] if x["check_id"] == "fixture.change")
        first["input_paths"].append("dep-one")
        second = copy.deepcopy(first)
        second.update(check_id="fixture.cascade", module="fixture.cascade",
                      triggers=[{"path": "dep-one/"}], input_paths=["dep-two"])
        declarations["checks"].append(second)
        path.write_text(yaml.safe_dump(declarations))
        subject = derive_task_subject(self.fixture.root, self.requirements,
                                     ["dep-one/new.py", "dep-two/new.py", "src/test.py", "unrelated.txt"])
        self.assertEqual(["src/test.py"], subject["task_paths"])
        self.assertEqual(["dep-one/new.py", "dep-two/new.py"], subject["dependency_paths"])
        self.assertIn("fixture.cascade", subject["selected_check_ids"])
