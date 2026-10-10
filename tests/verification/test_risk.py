"""以真实主体快照、确定性风险分类和失败关闭合同提供回归覆盖。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification.planning.change_risk_assessment import assess, formal_only_ids
from tests.verification.declaration_fixtures import write_declarations


class RiskAssessmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "config/engineering").mkdir(parents=True)
        repo = Path(__file__).resolve().parents[2]
        (self.root / "config/engineering/risk.yaml").write_bytes(
            (repo / "config/engineering/risk.yaml").read_bytes()
        )
        (self.root / "scripts/verification/planning").mkdir(parents=True)
        (self.root / "scripts/verification/planning/change_risk_assessment.py").write_bytes(
            (repo / "scripts/verification/planning/change_risk_assessment.py").read_bytes()
        )
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
        path = self.root / "config/engineering/module-checks.yaml"
        document = yaml.safe_load(path.read_text())
        document["scenario_policy"]["ci_quick"]["formal_only_check_ids"] = (
            ids if ids is not None else ["formal.runtime", "formal.candidate"]
        )
        path.write_text(yaml.safe_dump(document))

    def _write_checks(self):
        checks = []
        for check_id in (
            "formal.runtime",
            "formal.candidate",
            "local.extension",
            "local.enrichment",
            "local.lexicon",
            "local.docs",
        ):
            checks.append(
                {
                    "check_id": check_id,
                    "command": ["python3", "-c", "pass"],
                    "cwd": ".",
                    "timeout_seconds": 10,
                    "scope": "change-targeted"
                    if check_id.startswith("local.")
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
        for check in checks:
            if check["check_id"].startswith("formal."):
                check["scenarios"] = ["repository-baseline", "change-targeted"]
        write_declarations(self.root / "config/engineering/module-checks.yaml", checks)
        self._write_ci()

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
            (root / "config/engineering").mkdir(parents=True)
            write_declarations(
                root / "config/engineering/module-checks.yaml",
                [],
                ci_quick={
                    "always_required_check_ids": [],
                    "formal_only_check_ids": ["one", "one"],
                },
            )
            with self.assertRaisesRegex(ValueError, "ci_quick"):
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
            self.root, base=self.base, required_check_ids=("formal.runtime",)
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
                "formal.candidate",
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
        self._write_ci(["formal.runtime"])
        policy_changed = assess(self.root, base=self.base)
        self.assertNotEqual(changed["subject_hash"], policy_changed["subject_hash"])
        declaration_path = self.root / "config/engineering/module-checks.yaml"
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
        agent_policy = self.root / "config/engineering/risk.yaml"
        agent_policy.write_bytes(
            agent_policy.read_bytes() + b"\n# risk fixture policy change\n"
        )
        source_policy_changed = assess(self.root, base=self.base)
        self.assertNotEqual(
            dependency_changed["subject_hash"], source_policy_changed["subject_hash"]
        )

    def test_sensitive_line_changes_use_captured_bytes_not_unchanged_context(self):
        from scripts.verification.planning import change_risk_assessment as risk

        path = "backend/product/enrichment/Private.java"
        target = self.root / path
        target.parent.mkdir(parents=True)
        original = b"class Private {\n public String token;\n int x = 1;\n}\n"
        target.write_bytes(original)
        subprocess.run(["git", "add", path], cwd=self.root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "sensitive fixture"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )
        self.base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
        ).strip()
        target.write_bytes(original.replace(b"x = 1", b"x = 2"))
        with patch(
            "scripts.verification.planning.change_risk_assessment._read_regular", wraps=risk._read_regular
        ) as read:
            ordinary = assess(self.root, base=self.base)
        self.assertEqual("local-function", ordinary["level"])
        self.assertEqual(2, sum(call.args[1] == path for call in read.call_args_list))
        for changed in (
            original.replace(b" public String token;\n", b""),
            original.replace(b" int x = 1;", b" public String secret;"),
        ):
            with self.subTest(changed=changed):
                target.write_bytes(changed)
                assessed = assess(self.root, base=self.base)
                self.assertEqual("high-risk-engineering", assessed["level"])
                self.assertIn("sensitive-product-content", assessed["reason_codes"])
        self.assertEqual(
            b"removed\nadded", risk._changed_content(b"same\nremoved", b"same\nadded")
        )
        self.assertEqual(b"new", risk._changed_content(None, b"new"))

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
        policy = self.root / "config/engineering/risk.yaml"
        original = policy.read_bytes()
        policy.write_text("architecture: {}\n")
        (self.root / "docs/user/help.md").parent.mkdir(parents=True)
        (self.root / "docs/user/help.md").write_text("plain\n")
        with self.assertRaisesRegex(ValueError, "policy-invalid"):
            assess(self.root, base=self.base)
        policy.write_bytes(original)

    def test_latest_config_rejects_unknown_nested_fixed_and_duplicate_fields(self):
        from scripts.verification.planning.change_risk_assessment import _risk_policy

        path = self.root / "config/engineering/risk.yaml"
        valid = yaml.safe_load(path.read_bytes())
        self.assertEqual(set(valid), {"high_risk_paths", "local_owner_roots"})
        for key in ("schema_version", "levels", "private_paths", "sensitive_content_patterns", "validation_actor", "independent_review_levels", "risk_classification"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "policy-invalid"):
                _risk_policy(yaml.safe_dump({**valid, key: []}).encode())
        for invalid in (
            b"high_risk_paths: [one]\nhigh_risk_paths: [two]\nlocal_owner_roots: {a: 'src/'}\n",
            b"high_risk_paths: [one]\nlocal_owner_roots: {a: 'src/', a: 'other/'}\n",
            yaml.safe_dump({**valid, "high_risk_paths": "scripts/**"}).encode(),
            yaml.safe_dump({**valid, "local_owner_roots": {"bad": "../private/"}}).encode(),
            yaml.safe_dump({**valid, "local_owner_roots": {"bad": "src//"}}).encode(),
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "policy-invalid"):
                _risk_policy(invalid)

    def test_config_root_is_high_risk_even_without_configured_pattern(self):
        from scripts.verification.planning.change_risk_assessment import _high_path, _risk_policy

        settings = _risk_policy(b"high_risk_paths: [other/**]\nlocal_owner_roots: {custom: 'src/'}\n")
        self.assertTrue(_high_path("config/engineering/risk.yaml", settings))
        self.assertTrue(_high_path("config/engineering/new.yaml", settings))
        self.assertFalse(_high_path("src/local.java", settings))

    def test_fixed_risk_implementation_bytes_change_subject_hash(self):
        first = self._assess("backend/product/enrichment/Private.java", b"class Private {}\n")
        source = self.root / "scripts/verification/planning/change_risk_assessment.py"
        source.write_bytes(source.read_bytes() + b"\n# changed implementation fixture\n")
        changed = assess(self.root, base=self.base, _subject_paths=tuple(first["changed_files"]))
        self.assertNotEqual(first["subject_hash"], changed["subject_hash"])

    def test_sensitive_detection_is_code_owned(self):
        from scripts.verification.planning.change_risk_assessment import _signal_content

        for content in (b"public String token", b"fetch(url)", b"chrome.storage.local", b"ProcessBuilder", b"Files.readString", b"x.execute()"):
            with self.subTest(content=content):
                self.assertTrue(_signal_content(content))
        self.assertFalse(_signal_content(b"int x = 2;"))

    def test_input_drift_between_snapshots_is_rejected(self):
        self._assess("backend/product/enrichment/Private.java", b"class Private {}\n")
        from scripts.verification.planning import change_risk_assessment as risk

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
                "scripts.verification.planning.change_risk_assessment._check_snapshot",
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
        from scripts.verification.planning.change_risk_assessment import _read_regular

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
