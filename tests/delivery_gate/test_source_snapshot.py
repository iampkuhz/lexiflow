"""冻结审查覆盖未跟踪源码、二进制、空文件，并拒绝不完整附件。"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from scripts.delivery_gate.acceptance import verify_plan
from scripts.delivery_gate.records import (
    RecordError,
    load_submission,
    publish_bytes,
    read_bound_bytes,
)
from scripts.delivery_gate.source_snapshot import review_patch
from scripts.delivery_gate.submit import submit
from scripts.verification import risk
from tests.delivery_gate.fixtures import (
    DeliveryGateFixture,
    TASK_ID,
    PRODUCER_SESSION,
    make_mock_runtime,
)


class SourceSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.addCleanup(self.f.cleanup)

    def test_tracked_untracked_binary_and_empty_subject_are_all_visible(self):
        self.f.write("protected.py", "tracked body changed\n")
        self.f.write(
            "src/new.py", "def sensitive_new_body():\n    return 'must review'\n"
        )
        self.f.write("src/new.bin", b"\0\xff\x01" * 20)
        self.f.write("src/empty file.txt", b"")
        paths = ["protected.py", "src/empty file.txt", "src/new.bin", "src/new.py"]
        data = review_patch(self.f.root, "HEAD", paths)
        for evidence in (
            b"tracked body changed",
            b"sensitive_new_body",
            b"GIT binary patch",
            b"empty file.txt",
            b"new file mode",
        ):
            self.assertIn(evidence, data)
        self.f.write("outside.txt", "unrelated source\n")
        self.assertEqual(data, review_patch(self.f.root, "HEAD", paths))
        self.assertNotIn(b"unrelated source", data)

    def test_real_high_risk_submission_contains_untracked_body_and_rejects_omission(
        self,
    ):
        self.f.create_verification_report()
        with patch(
            "scripts.agents.local_codex_runtime.discover",
            return_value=make_mock_runtime(PRODUCER_SESSION),
        ):
            created = submit(
                self.f.root,
                task_id=TASK_ID,
                change_report_id=self.f.report_id,
                confirm_scope_report_id=self.f.report_id,
            )
        submission = load_submission(self.f.root, created["submission_id"])
        self.assertEqual(
            "high-risk-engineering", submission["risk_assessment"]["level"]
        )
        descriptor = submission["diff"]
        self.assertIn(
            b"content of src/test.py",
            read_bound_bytes(self.f.root, descriptor["locator"], descriptor["sha256"]),
        )
        verify_plan(self.f.root, submission)
        forged = copy.deepcopy(submission)
        forged["diff"] = publish_bytes(
            self.f.root, "tmp/quality/delivery-gate/omitted.patch", b""
        )
        with self.assertRaisesRegex(RecordError, "review patch does not cover"):
            verify_plan(self.f.root, forged)

    def test_unsafe_source_is_not_followed(self):
        self.f.write("src/ordinary.py", "safe\n")
        (self.f.root / "src/link.py").symlink_to(self.f.root / "src/ordinary.py")
        with self.assertRaises(RecordError):
            review_patch(self.f.root, "HEAD", ["src/link.py"])

    def test_git_magic_and_glob_names_are_literal_subjects(self):
        names = [":(exclude)hidden.py", ":(glob)*.py", "[ab].py"]
        for name in names:
            self.f.write(name, "initial exact subject\n")
        self.f._git("add", ".")
        self.f._git("commit", "-m", "literal subjects")
        for index, name in enumerate(names):
            self.f.write(name, f"changed exact subject {index}\n")
        self.f.write("protected.py", "unrelated tracked body\n")
        for index, name in enumerate(names):
            with self.subTest(name=name):
                data = review_patch(self.f.root, "HEAD", [name])
                self.assertIn(f"changed exact subject {index}".encode(), data)
                self.assertNotIn(b"unrelated tracked body", data)
                raw = risk._git(self.f.root, "diff", "--binary", "HEAD", "--", name)
                self.assertIn(f"changed exact subject {index}".encode(), raw)
                self.assertNotIn(b"unrelated tracked body", raw)


if __name__ == "__main__":
    unittest.main()
