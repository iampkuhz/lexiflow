"""Deterministic regression coverage for risk assessment."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification.risk import assess, formal_only_ids


class RiskAssessmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "harness").mkdir()
        repo = Path(__file__).resolve().parents[2]
        (self.root / "harness/agent-policy.manifest.yaml").write_bytes(
            (repo / "harness/agent-policy.manifest.yaml").read_bytes()
        )
        self._write_ci()
        self._write_checks()
        subprocess.run(["git", "init"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=self.root, check=True
        )
        (self.root / "initial.txt").write_text("initial\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "base"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def tearDown(self):
        self.temp.cleanup()

    def _write_ci(self, ids=None):
        (self.root / "harness/ci-policy.yaml").write_text(
            yaml.safe_dump(
                {"formal_only_check_ids": ids or ["formal.runtime", "formal.candidate"]}
            )
        )

    def _write_checks(self):
        checks = []
        for check_id in (
            "formal.runtime",
            "formal.runtime-on-change",
            "formal.candidate",
            "formal.candidate-on-change",
            "local.extension",
            "local.enrichment",
            "local.lexicon",
            "local.docs",
        ):
            checks.append(
                {
                    "check_id": check_id,
                    "module": check_id,
                    "command": ["python3", "-c", "pass"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": "change-targeted"
                    if check_id.endswith("on-change") or check_id.startswith("local.")
                    else "repository-baseline",
                    "triggers": [
                        {
                            "path": "docs/user/"
                            if check_id == "local.docs"
                            else "extension/src/"
                            if check_id == "local.extension"
                            else "backend/product/enrichment/"
                            if check_id == "local.enrichment"
                            else "backend/product/lexicon/"
                            if check_id == "local.lexicon"
                            else "src/"
                        }
                    ],
                    "module_dependencies": [],
                    "required_environment": [],
                    "input_paths": [],
                    "result_contract": {
                        "type": "exit-code",
                        "completeness_guarantee": "test",
                    },
                }
            )
        (self.root / "harness/module-checks.yaml").write_text(
            yaml.safe_dump(
                {"schema_version": "lexiflow.module-checks.v1", "checks": checks}
            )
        )

    def _assess(self, path: str, content: bytes, *, required=()):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return assess(self.root, base=self.base, required_check_ids=required)

    def test_four_risk_levels_and_formal_requirement_source(self):
        result = self._assess("docs/user/help.md", b"hello\r\n")
        self.assertEqual("local-function", result["level"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            (root / "harness/ci-policy.yaml").write_text(
                "formal_only_check_ids: [one, one]\n"
            )
            with self.assertRaisesRegex(ValueError, "formal-policy-invalid"):
                formal_only_ids(root)

    def test_crlf_only_user_markdown_is_mechanical_and_formal_is_task_required(self):
        (self.root / "docs/user").mkdir(parents=True)
        path = self.root / "docs/user/help.md"
        path.write_bytes(b"one\r\ntwo\r\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "docs"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        path.write_bytes(b"one\ntwo\n")
        result = assess(self.root, base=self.base)
        self.assertEqual("mechanical", result["level"])
        formal = assess(
            self.root, base=self.base, required_check_ids=("formal.runtime-on-change",)
        )
        self.assertEqual("formal-release", formal["level"])
        ordinary = assess(self.root, base=self.base)
        self.assertEqual("mechanical", ordinary["level"])

    def test_sensitive_content_cross_owner_uncovered_unknown_and_binary_raise_risk(
        self,
    ):
        local = self._assess(
            "backend/product/enrichment/Private.java", b"class Private {}\n"
        )
        self.assertEqual("local-function", local["level"])
        sensitive = self._assess(
            "backend/product/enrichment/Private.java",
            b"class Private { public String token; }\n",
        )
        self.assertEqual("high-risk-engineering", sensitive["level"])
        second_owner = self.root / "extension/src/other.ts"
        second_owner.parent.mkdir(parents=True, exist_ok=True)
        second_owner.write_text("const x = 1;\n")
        crossed = assess(self.root, base=self.base)
        self.assertEqual("high-risk-engineering", crossed["level"])
        self.assertIn("multiple-product-owners", crossed["reason_codes"])

        unknown = self._assess("mystery/new.bin", b"unknown\n")
        self.assertEqual("high-risk-engineering", unknown["level"])
        self.assertTrue(unknown["coverage_gaps"])
        binary = self._assess("backend/product/enrichment/blob.java", b"\0\x01")
        self.assertEqual("high-risk-engineering", binary["level"])

    def test_formal_ids_do_not_upgrade_ordinary_trigger_and_task_identity_is_not_an_input(
        self,
    ):
        ordinary = self._assess("src/file.py", b"value = 1\n")
        self.assertNotEqual("formal-release", ordinary["level"])
        self.assertEqual(
            set(formal_only_ids(self.root)),
            {
                "formal.runtime",
                "formal.runtime-on-change",
                "formal.candidate",
                "formal.candidate-on-change",
            },
        )
        self.assertEqual(
            set(ordinary),
            {
                "schema_version",
                "level",
                "reason_codes",
                "base",
                "head",
                "changed_files",
                "file_snapshots",
                "selected_check_ids",
                "required_check_ids",
                "coverage_gaps",
                "subject_hash",
            },
        )

    def test_dependency_policy_and_content_change_subject_hash(self):
        first = self._assess(
            "backend/product/enrichment/Private.java", b"class Private {}\n"
        )
        changed = self._assess(
            "backend/product/enrichment/Private.java", b"class Private { int x; }\n"
        )
        self.assertNotEqual(first["subject_hash"], changed["subject_hash"])
        (self.root / "harness/ci-policy.yaml").write_text(
            "formal_only_check_ids: [formal.runtime]\n"
        )
        policy_changed = assess(self.root, base=self.base)
        self.assertNotEqual(changed["subject_hash"], policy_changed["subject_hash"])
        declaration_path = self.root / "harness/module-checks.yaml"
        declaration = yaml.safe_load(declaration_path.read_text())
        next(
            item
            for item in declaration["checks"]
            if item["check_id"] == "local.enrichment"
        )["module_dependencies"] = ["local.extension"]
        declaration_path.write_text(yaml.safe_dump(declaration))
        dependency_changed = assess(self.root, base=self.base)
        self.assertNotEqual(
            policy_changed["subject_hash"], dependency_changed["subject_hash"]
        )
        self.assertIn("local.extension", dependency_changed["selected_check_ids"])
        agent_policy = self.root / "harness/agent-policy.manifest.yaml"
        agent_policy.write_bytes(
            agent_policy.read_bytes() + b"\n# risk fixture policy change\n"
        )
        source_policy_changed = assess(self.root, base=self.base)
        self.assertNotEqual(
            dependency_changed["subject_hash"], source_policy_changed["subject_hash"]
        )

    def test_subject_paths_ignore_unrelated_later_changes_but_hash_their_contents(self):
        (self.root / "backend/product/enrichment").mkdir(parents=True)
        target = self.root / "backend/product/enrichment/Private.java"
        target.write_text("class Private {}\n")
        original = assess(self.root, base=self.base)
        unrelated = self.root / "docs/user/later.md"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("unrelated\n")
        historical = assess(
            self.root, base=self.base, _subject_paths=tuple(original["changed_files"])
        )
        self.assertEqual(original["subject_hash"], historical["subject_hash"])
        target.write_text("class Private { int changed; }\n")
        changed = assess(
            self.root, base=self.base, _subject_paths=tuple(original["changed_files"])
        )
        self.assertNotEqual(original["subject_hash"], changed["subject_hash"])

    def test_deletion_rename_and_untracked_files_are_snapshotted(self):
        source = self.root / "extension/src/source.ts"
        source.parent.mkdir(parents=True)
        source.write_text("const value = 1;\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "source"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        self.base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        destination = source.with_name("renamed.ts")
        source.rename(destination)
        (self.root / "extension/src/new.ts").write_text("const other = 2;\n")
        result = assess(self.root, base=self.base)
        self.assertEqual(
            set(result["changed_files"]),
            {
                "extension/src/new.ts",
                "extension/src/renamed.ts",
                "extension/src/source.ts",
            },
        )
        self.assertEqual(
            result["file_snapshots"]["extension/src/source.ts"], {"state": "absent"}
        )
        self.assertEqual(
            result["file_snapshots"]["extension/src/renamed.ts"]["state"], "present"
        )

    def test_caller_cannot_supply_level_and_invalid_required_check_rejected(self):
        with self.assertRaises(TypeError):
            assess(self.root, base=self.base, level="mechanical")
        self._assess("src/file.py", b"value = 1\n")
        with self.assertRaisesRegex(ValueError, "unknown-required-check"):
            assess(self.root, base=self.base, required_check_ids=("does.not.exist",))

    def test_missing_or_invalid_risk_policy_is_rejected(self):
        policy = self.root / "harness/agent-policy.manifest.yaml"
        original = policy.read_bytes()
        policy.write_text("architecture: {}\n")
        (self.root / "docs/user/help.md").parent.mkdir(parents=True)
        (self.root / "docs/user/help.md").write_text("plain\n")
        with self.assertRaisesRegex(ValueError, "policy-invalid"):
            assess(self.root, base=self.base)
        policy.write_bytes(original)

    def test_input_drift_between_snapshots_is_rejected(self):
        self._assess("backend/product/enrichment/Private.java", b"class Private {}\n")
        from scripts.verification import risk

        original = risk._check_snapshot
        calls = 0

        def drift_on_recheck(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "backend/product/enrichment/Private.java").write_text(
                    "class Private { int changed; }\n"
                )
            return original(*args, **kwargs)

        with (
            patch(
                "scripts.verification.risk._check_snapshot",
                side_effect=drift_on_recheck,
            ),
            self.assertRaisesRegex(ValueError, "input-drift"),
        ):
            assess(self.root, base=self.base)

    def test_private_paths_symlinks_and_empty_change_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "private-path"):
            self._assess(".env", b"not-read")
        (self.root / ".env").unlink()
        (self.root / "extension/src").mkdir(parents=True)
        (self.root / "extension/src/link").symlink_to(self.root / "initial.txt")
        with self.assertRaisesRegex(ValueError, "unsafe-file"):
            assess(self.root, base=self.base)
        from scripts.verification.risk import _read_regular

        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir(exist_ok=True)
        (outside / "payload").write_text("must not follow parent link\n")
        parent_link = self.root / "linked-parent"
        parent_link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "unsafe-file"):
            _read_regular(self.root, "linked-parent/payload")
        parent_link.unlink()
        (outside / "payload").unlink()
        outside.rmdir()
        (self.root / "extension/src/link").unlink()
        with self.assertRaisesRegex(ValueError, "empty-change-set"):
            assess(self.root, base=self.base)


if __name__ == "__main__":
    unittest.main()
