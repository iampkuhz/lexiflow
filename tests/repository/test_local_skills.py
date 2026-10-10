"""固定本机 skill 接入的真实链接、来源与失败边界。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.local import manage_local_skill_links as local_skills

NAMES = ("feipi-plantuml-generate-diagram", "feipi-skill-govern")


class LocalSkillTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.source = Path(self.temp.name) / "source"
        self.root.mkdir()

    def install(self):
        for name in NAMES:
            directory = self.source / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "SKILL.md").write_text(f"---\nname: {name}\n---\n")

    def test_fixed_skills_do_not_require_a_policy_and_link_is_idempotent(self):
        self.assertEqual(
            "BLOCKED", local_skills.run(self.root, self.source, "check")["status"]
        )
        self.install()
        self.assertEqual(
            "BLOCKED", local_skills.run(self.root, self.source, "check")["status"]
        )
        self.assertFalse((self.root / ".agents").exists())
        for mode in ("link", "check", "link"):
            result = local_skills.run(self.root, self.source, mode)
            self.assertEqual("PASS", result["status"])
            self.assertEqual(list(NAMES), [item["name"] for item in result["skills"]])
        for name in NAMES:
            self.assertEqual(
                (self.source / name).resolve(),
                (self.root / ".agents/skills" / name).resolve(),
            )

    def test_missing_or_invalid_source_blocks_all_links(self):
        self.install()
        entry = self.source / NAMES[1] / "SKILL.md"
        for content in (None, "---\n[]\n---\n", "---\nname: other\n---\n"):
            if content is None:
                entry.unlink()
            else:
                entry.write_text(content)
            self.assertEqual(
                "BLOCKED", local_skills.run(self.root, self.source, "link")["status"]
            )
            self.assertFalse((self.root / ".agents").exists())

    def test_existing_directory_or_wrong_symlink_is_never_overwritten(self):
        self.install()
        base = self.root / ".agents/skills"
        base.mkdir(parents=True)
        link = base / NAMES[0]
        link.mkdir()
        (link / "owned").write_text("existing")
        self.assertEqual(
            "FAIL", local_skills.run(self.root, self.source, "link")["status"]
        )
        self.assertEqual("existing", (link / "owned").read_text())
        (link / "owned").unlink()
        link.rmdir()
        link.symlink_to(self.source / "missing", target_is_directory=True)
        self.assertEqual(
            "FAIL", local_skills.run(self.root, self.source, "link")["status"]
        )
        self.assertEqual(self.source / "missing", link.readlink())
        self.assertFalse((base / NAMES[1]).exists())

    def test_parent_symlinks_cannot_redirect_writes(self):
        self.install()
        (self.root / ".agents").symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "父链接"):
            local_skills.run(self.root, self.source, "link")
        (self.root / ".agents").unlink()
        (self.root / ".agents").mkdir()
        (self.root / ".agents/skills").symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "父链接"):
            local_skills.run(self.root, self.source, "link")

    def test_entry_symlink_preserves_installed_directory_and_resources(self):
        self.install()
        upstream = Path(self.temp.name) / "upstream.md"
        upstream.write_text(f"---\nname: {NAMES[0]}\n---\n")
        installed = self.source / NAMES[0]
        (installed / "SKILL.md").unlink()
        (installed / "SKILL.md").symlink_to(upstream)
        (installed / "resource").write_text("安装目录资源")
        self.assertEqual(
            "PASS", local_skills.run(self.root, self.source, "link")["status"]
        )
        self.assertEqual(
            installed.resolve(), (self.root / ".agents/skills" / NAMES[0]).resolve()
        )
        self.assertTrue(
            (self.root / ".agents/skills" / NAMES[0] / "resource").is_file()
        )

    def test_cli_uses_codex_home_and_has_no_policy_or_source_override(self):
        self.install()
        with (
            mock.patch.dict(
                local_skills.os.environ, {"CODEX_HOME": str(self.source.parent)}
            ),
            mock.patch.object(
                local_skills, "run", return_value={"status": "BLOCKED"}
            ) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(1, local_skills.main(["check"], root=self.root))
        self.assertEqual(
            (self.source.parent / "skills").resolve(), run.call_args.args[1]
        )
        for option in ("--policy", "--source"):
            with (
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                local_skills.main(["check", option, str(self.source)], root=self.root)


if __name__ == "__main__":
    unittest.main()
