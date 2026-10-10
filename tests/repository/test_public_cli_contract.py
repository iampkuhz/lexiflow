"""维护入口只暴露动作，不暴露隔离测试的 root/source 注入。"""

from __future__ import annotations

import contextlib
import io
import unittest

from scripts.checks.repository.documentation import check_documentation as docs_check
from scripts.local import manage_local_skill_links as local_skills
from scripts.checks.repository.catalog import check_task_catalog as planning_check


class PublicCliContractTest(unittest.TestCase):
    def test_root_and_source_overrides_are_not_public(self):
        calls = (
            (docs_check.main, ["--root", "."]),
            (planning_check.main, ["--root", "."]),
            (planning_check.main, ["--verbose"]),
            (local_skills.main, ["check", "--source", "/tmp"]),
        )
        for entry, argv in calls:
            with self.subTest(entry=entry.__module__, argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                entry(argv)


if __name__ == "__main__":
    unittest.main()
