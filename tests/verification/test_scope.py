"""Tests for scripts.verification.scope.

Covers: git operations, check selection by triggers, module
dependency resolution, scope review computation, expected paths
self-review, unsafe paths, no-changes scenario.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.verification.scope import (
    ScopeError,
    _path_matches,
    changed_paths,
    compute_scope_review,
    resolve_base,
    resolve_module_dependencies,
    select_checks_for_changes,
)


def _init_git_repo(tmpdir: Path) -> None:
    """Initialize a minimal git repo for testing."""
    subprocess.run(["git", "init"], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (tmpdir / "initial.txt").write_text("initial")
    subprocess.run(["git", "add", "."], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmpdir), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class TestPathMatches(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(_path_matches("scripts/foo.py", "scripts/foo.py"))

    def test_prefix_match(self):
        self.assertTrue(_path_matches("scripts/", "scripts/foo.py"))

    def test_no_match(self):
        self.assertFalse(_path_matches("scripts/", "tests/foo.py"))

    def test_partial_dir_no_match(self):
        self.assertFalse(_path_matches("scripts/", "scripts_extra/foo.py"))


class TestSelectChecksForChanges(unittest.TestCase):
    def setUp(self):
        self.checks = [
            {
                "check_id": "backend.build",
                "module": "backend",
                "scope": "change-targeted",
                "triggers": [{"path": "backend/"}],
            },
            {
                "check_id": "extension.build",
                "module": "extension",
                "scope": "change-targeted",
                "triggers": [{"path": "clients/chrome-extension/"}],
            },
            {
                "check_id": "planning.validate",
                "module": "planning",
                "scope": "repository-baseline",
                "triggers": [{"path": "planning/"}],
            },
        ]

    def test_selects_matching_check(self):
        selected = select_checks_for_changes(
            self.checks, ["backend/src/Main.java"],
        )
        ids = [c["check_id"] for c in selected]
        self.assertIn("backend.build", ids)
        self.assertNotIn("extension.build", ids)

    def test_excludes_repository_baseline(self):
        selected = select_checks_for_changes(
            self.checks, ["planning/workstreams.yaml"],
        )
        ids = [c["check_id"] for c in selected]
        self.assertNotIn("planning.validate", ids)

    def test_no_match_returns_empty(self):
        selected = select_checks_for_changes(
            self.checks, ["unrelated/file.txt"],
        )
        self.assertEqual(selected, [])

    def test_selection_reasons_included(self):
        selected = select_checks_for_changes(
            self.checks, ["backend/src/Main.java"],
        )
        self.assertTrue(selected[0]["selection_reasons"])
        self.assertEqual(
            selected[0]["selection_reasons"][0]["kind"], "changed-file",
        )


class TestResolveModuleDependencies(unittest.TestCase):
    def test_adds_dependency(self):
        selected = [{
            "check_id": "integration.test",
            "module": "integration",
            "module_dependencies": ["backend"],
        }]
        all_checks = selected + [{
            "check_id": "backend.build",
            "module": "backend",
        }]
        result = resolve_module_dependencies(selected, all_checks)
        ids = {c["check_id"] for c in result}
        self.assertIn("backend.build", ids)
        self.assertIn("integration.test", ids)

    def test_no_duplicate_when_already_selected(self):
        selected = [{
            "check_id": "a",
            "module": "mod-a",
            "module_dependencies": ["mod-b"],
        }, {
            "check_id": "b",
            "module": "mod-b",
        }]
        result = resolve_module_dependencies(selected, selected)
        self.assertEqual(len(result), 2)

    def test_no_dependency_declaration(self):
        selected = [{"check_id": "a", "module": "mod-a"}]
        result = resolve_module_dependencies(selected, selected)
        self.assertEqual(len(result), 1)


class TestComputeScopeReview(unittest.TestCase):
    def test_no_context_when_no_expected(self):
        review = compute_scope_review(
            ["a.py", "b.py"], (), {"check1"}, 5,
        )
        self.assertEqual(review["kind"], "no-context")
        self.assertEqual(len(review["unexpected"]), 2)

    def test_expected_context(self):
        review = compute_scope_review(
            ["backend/a.py", "other/b.py"],
            ("backend/",),
            {"check1"}, 5,
        )
        self.assertEqual(review["kind"], "expected")
        self.assertIn("backend/a.py", review["expected"])
        self.assertIn("other/b.py", review["unexpected"])

    def test_advisory_when_no_checks_matched(self):
        review = compute_scope_review(
            ["file.py"], (), set(), 5,
        )
        self.assertIn("no checks matched changed files", review["advisories"])


class TestGitOperations(unittest.TestCase):
    def test_resolve_base_explicit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _init_git_repo(root)
            base = resolve_base(root, "HEAD")
            self.assertEqual(base, "HEAD")

    def test_resolve_base_auto(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _init_git_repo(root)
            base = resolve_base(root)
            self.assertTrue(len(base) >= 7)

    def test_changed_paths_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _init_git_repo(root)
            base = resolve_base(root)
            changed = changed_paths(root, base)
            self.assertEqual(changed, [])

    def test_changed_paths_with_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _init_git_repo(root)
            base = resolve_base(root)
            (root / "new_file.txt").write_text("new")
            changed = changed_paths(root, base)
            self.assertIn("new_file.txt", changed)


if __name__ == "__main__":
    unittest.main()
