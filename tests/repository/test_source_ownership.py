"""共享源码候选与单一严格路径语法的隔离回归。"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.checks.repository.catalog.task_catalog_rules import PlanningValidator, repository_source_inventory


class SourceOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.validator = PlanningValidator(self.root)
        self.validator.path_scopes = [{"scope": "shared", "owner": "owner", "proposed_paths": [".gitignore"]}]

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True, check=True)

    def test_personal_ignore_cannot_hide_new_sources(self):
        personal = self.root.parent / (self.root.name + "-global-ignore")
        personal.write_text("global-hidden.py\n")
        self.addCleanup(personal.unlink)
        self.git("config", "core.excludesFile", str(personal))
        (self.root / ".git/info/exclude").write_text("local-hidden.py\n")
        for name in ("global-hidden.py", "local-hidden.py"):
            (self.root / name).write_text("private test fixture")
        self.validator.check_repo_path_ownership()
        for name in ("global-hidden.py", "local-hidden.py"):
            self.assertTrue(any(f"repo-unowned-file: {name}" in issue for issue in self.validator.errors))

    def test_shared_ignored_tree_is_not_traversed_or_read(self):
        (self.root / ".gitignore").write_text("private/\n")
        private = self.root / "private"
        private.mkdir()
        (private / "source.py").write_text("private fixture")
        original = Path.read_bytes
        def guarded(path):
            self.assertFalse(path.is_relative_to(private))
            return original(path)
        with patch.object(Path, "read_bytes", guarded):
            inventory = repository_source_inventory(self.root)
        self.assertNotIn("private/source.py", inventory["candidate_paths"])
        self.assertEqual({".gitignore"}, set(inventory["shared_ignore_sha256"]))

    def test_ignore_that_hides_itself_is_still_frozen(self):
        (self.root / ".gitignore").write_text(".gitignore\n")
        nested = self.root / "shared"
        nested.mkdir()
        (nested / ".gitignore").write_text("private.py\n")
        (nested / "public.py").write_text("fixture")
        inventory = repository_source_inventory(self.root)
        self.assertEqual({".gitignore", "shared/.gitignore"}, set(inventory["shared_ignore_sha256"]))
        self.assertNotIn("shared/.gitignore", inventory["candidate_paths"])

    def test_tracked_missing_source_stays_subject_and_private_root_does_not(self):
        (self.root / "missing.py").write_text("fixture")
        self.git("add", "missing.py")
        (self.root / "missing.py").unlink()
        (self.root / "tmp").mkdir()
        (self.root / "tmp/secret.py").write_text("private fixture")
        inventory = repository_source_inventory(self.root)
        self.assertIn("missing.py", inventory["candidate_paths"])
        self.assertNotIn("tmp/secret.py", inventory["candidate_paths"])

    def test_inventory_or_ignore_drift_fails_closed(self):
        frozen = {"candidate_paths": [], "shared_ignore_sha256": {}}
        for changed in ({**frozen, "candidate_paths": ["new.py"]}, {**frozen, "shared_ignore_sha256": {".gitignore": "changed"}}):
            self.validator.errors = []
            with patch("scripts.checks.repository.catalog.task_catalog_rules.repository_source_inventory", side_effect=[frozen, changed]):
                self.validator.check_repo_path_ownership()
            self.assertIn("repo-ownership-input-drift: shared source inventory changed", self.validator.errors)

    def test_engineering_configuration_is_a_shared_source_subject(self):
        self.validator.path_scopes.append({"scope": "engineering", "owner": "owner", "proposed_paths": ["config/engineering/**"]})
        config = self.root / "config/engineering"
        config.mkdir(parents=True)
        (config / "risk.yaml").write_text("high_risk_paths: []\n")
        frozen = repository_source_inventory(self.root)
        self.assertIn("config/engineering/risk.yaml", frozen["candidate_paths"])
        self.validator.check_repo_path_ownership()
        self.assertEqual([], self.validator.errors)
        (config / "module-checks.yaml").write_text("checks: []\n")
        changed = repository_source_inventory(self.root)
        self.assertNotEqual(frozen, changed)
        self.assertIn("config/engineering/module-checks.yaml", changed["candidate_paths"])

    def test_non_repository_does_not_fall_back_to_private_tree_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            validator = PlanningValidator(directory)
            validator.check_repo_path_ownership()
            self.assertTrue(any("inventory-unavailable" in issue for issue in validator.errors))
