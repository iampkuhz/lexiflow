"""合成基仓可以复用字节，测试可变文件、Git 与证据必须私有。"""

from __future__ import annotations

import unittest
import uuid

from scripts.agents.identity import codex_actor_identity as local_codex_runtime
from scripts.acceptance.evidence.acceptance_record_store import delivery_gate_locator
from tests.delivery_gate.fixtures import DeliveryGateFixture


class FixtureIsolationTests(unittest.TestCase):
    def fixture(self, kind=DeliveryGateFixture):
        fixture = kind()
        self.addCleanup(fixture.cleanup)
        return fixture

    def test_base_bytes_do_not_share_mutable_files_git_or_evidence(self):
        first, second = self.fixture(), self.fixture()
        original_head = second._git("rev-parse", "HEAD").stdout
        private_paths = (
            "protected.py",
            "planning/workstreams.yaml",
            "config/engineering/module-checks.yaml",
            ".git/config",
            ".git/index",
        )
        for relative in private_paths:
            with self.subTest(relative=relative):
                left, right = first.root / relative, second.root / relative
                self.assertEqual(left.read_bytes(), right.read_bytes())
                self.assertNotEqual(left.stat().st_ino, right.stat().st_ino)
        original = {path: (second.root / path).read_bytes() for path in private_paths}
        first.write("protected.py", "private mutation\n")
        first.write("planning/workstreams.yaml", "private catalog\n")
        first.write("config/engineering/module-checks.yaml", "private checks\n")
        first._git("config", "user.name", "private actor")
        first._git("add", ".")
        first._git("commit", "-m", "private commit")
        first.write("tmp/quality/private-record.json", '{"status":"FAIL"}\n')
        self.assertNotEqual(first._git("rev-parse", "HEAD").stdout, original_head)
        # 修改之后才创建第三实例，证明缓存本身也未被污染。
        third = self.fixture()
        for fixture in (second, third):
            for relative, content in original.items():
                with self.subTest(root=fixture.root.name, relative=relative):
                    self.assertEqual((fixture.root / relative).read_bytes(), content)
            self.assertEqual(fixture._git("rev-parse", "HEAD").stdout, original_head)
            self.assertEqual(fixture._git("status", "--porcelain").stdout, b"")
            self.assertFalse((fixture.root / "tmp").exists())
            self.assertIsNone(fixture.report_id)
            self.assertIsNone(fixture.submission_id)
            self.assertIsNone(fixture.validation_id)
            self.assertIsNone(fixture.review_id)
        snapshot = DeliveryGateFixture._base_files()
        self.assertIsInstance(snapshot, tuple)
        for relative, content, mode in snapshot:
            self.assertIsInstance(relative, str)
            self.assertIsInstance(content, bytes)
            self.assertIsInstance(mode, int)

    def test_subclass_preparation_has_an_independent_snapshot(self):
        class DifferentDeclarations(DeliveryGateFixture):
            def _write_declarations(self):
                super()._write_declarations()
                self.write("fixture_check.py", "raise RuntimeError('private check')\n")

        custom = self.fixture(DifferentDeclarations)
        normal = self.fixture()
        self.assertNotEqual(
            (custom.root / "fixture_check.py").read_bytes(),
            (normal.root / "fixture_check.py").read_bytes(),
        )
        self.assertEqual(custom._git("status", "--porcelain").stdout, b"")
        self.assertEqual(normal._git("status", "--porcelain").stdout, b"")

    def test_uuid_receipts_reports_and_runtime_patch_are_private(self):
        original_verifier = local_codex_runtime.verify_proof
        first, second = DeliveryGateFixture(), DeliveryGateFixture()
        try:
            artifacts = []
            for fixture in (first, second):
                submission = fixture.create_submission()
                validation = fixture.create_validation()
                review = fixture.create_review()
                artifacts.append(
                    [
                        submission["change_report"]["locator"],
                        submission["diff"]["locator"],
                        validation["verification_report"]["locator"],
                        delivery_gate_locator("submissions", fixture.submission_id),
                        delivery_gate_locator("validations", fixture.validation_id),
                        delivery_gate_locator("reviews", fixture.review_id),
                    ]
                )
            for field in ("report_id", "submission_id", "validation_id", "review_id"):
                with self.subTest(field=field):
                    self.assertNotEqual(getattr(first, field), getattr(second, field))
                    for fixture in (first, second):
                        self.assertEqual(uuid.UUID(getattr(fixture, field)).version, 4)
            for fixture, other, locators in (
                (first, second, artifacts[0]),
                (second, first, artifacts[1]),
            ):
                for locator in locators:
                    with self.subTest(locator=locator):
                        self.assertTrue((fixture.root / locator).is_file())
                        self.assertFalse((other.root / locator).exists())
            reports = [
                fixture.root / locators[2]
                for fixture, locators in zip((first, second), artifacts)
            ]
            self.assertNotEqual(reports[0].stat().st_ino, reports[1].stat().st_ino)
            second_content = reports[1].read_bytes()
            reports[0].chmod(0o600)
            reports[0].write_bytes(b"private evidence mutation\n")
            self.assertEqual(reports[1].read_bytes(), second_content)
        finally:
            second.cleanup()
            first.cleanup()
        self.assertIs(local_codex_runtime.verify_proof, original_verifier)


if __name__ == "__main__":
    unittest.main()
