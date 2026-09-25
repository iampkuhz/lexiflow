"""维护入口只暴露动作，不暴露隔离测试的 root/source 注入。"""

from __future__ import annotations

import contextlib
import io
import unittest

from scripts.repository import docs_check, hooks, local_skills, planning_check, policy_projection


class PublicCliContractTest(unittest.TestCase):
    def test_root_and_source_overrides_are_not_public(self):
        calls = (
            (docs_check.main, ["--root", "."]),
            (planning_check.main, ["--root", "."]),
            (planning_check.main, ["--verbose"]),
            (policy_projection.main, ["--check", "--root", "."]),
            (local_skills.main, ["check", "--source", "/tmp"]),
            (hooks.main, ["doctor", "--root", "."]),
        )
        for entry, argv in calls:
            with self.subTest(entry=entry.__module__, argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                entry(argv)


if __name__ == "__main__":
    unittest.main()
