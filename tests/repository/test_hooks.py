from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.repository import hooks


class TestHooks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".githooks").mkdir()
        for name in ("pre-commit", "post-commit"):
            (self.root / ".githooks" / name).write_text("#!/bin/sh\n")

    def tearDown(self):
        self.temp.cleanup()

    @mock.patch("scripts.repository.hooks._git")
    def test_install_configures_versioned_path(self, git):
        git.return_value = subprocess.CompletedProcess([], 0, "", "")
        self.assertEqual(hooks.install(self.root)["result"], "PASS")
        git.assert_called_once_with(self.root, "config", "core.hooksPath", ".githooks")

    @mock.patch("scripts.repository.hooks._git")
    def test_doctor_explains_missing_installation(self, git):
        git.return_value = subprocess.CompletedProcess([], 1, "", "")
        result = hooks.doctor(self.root)
        self.assertEqual(result["result"], "BLOCKED")
        self.assertIn("hooks install", result["next_action"])

    def test_repository_reminder_points_to_real_entrypoints_without_running_them(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            ["/bin/sh", str(root / ".githooks/pre-commit")],
            cwd=self.root, env={"PATH": "/nonexistent"},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode)
        for entry in ("scripts/check_changes.py", "scripts/check_repository.py"):
            self.assertTrue((root / entry).is_file())
            self.assertIn(entry, result.stderr)
        self.assertNotIn("scripts/gates/", result.stderr)


if __name__ == "__main__":
    unittest.main()
