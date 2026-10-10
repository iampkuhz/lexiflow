"""验证 Git 子进程 PATH 的受限、只读优化。"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.environment.git_runtime import (
    generate_prepared_git_wrapper,
    parse_prepared_git_wrapper,
    prepared_git_bin,
    prepend_prepared_git,
)
from scripts.environment.runtime import execution_environment
from scripts.environment.toolchain import resolve_toolchain
from tests.verification.declaration_fixtures import write_declarations


class GitRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.target = self.root / "actual-git"
        self.target.write_text("#!/bin/sh\necho synthetic-git\n", encoding="utf-8")
        self.target.chmod(0o755)
        self.bin = self.root / "tmp/quality/toolchains/git/bin"
        self.bin.mkdir(parents=True)
        self.entry = self.bin / "git"
        self.entry.write_bytes(generate_prepared_git_wrapper(self.target))
        self.entry.chmod(0o700)
        for parent in (
            self.root / "tmp",
            self.root / "tmp/quality",
            self.root / "tmp/quality/toolchains",
            self.root / "tmp/quality/toolchains/git",
            self.bin,
        ):
            parent.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def _which(self, name, path=None):
        if name == "git" and path == "/usr/bin:/bin":
            return "/usr/bin/git"
        return None

    def _run(self, *, platform="darwin", xcrun=None, check=None, path="/usr/bin:/bin"):
        declaration = check or {
            "transaction_reuse": True,
            "required_environment": ["git"],
        }
        with (
            patch(
                "scripts.environment.git_runtime.shutil.which", side_effect=self._which
            ),
            patch(
                "scripts.environment.git_runtime.subprocess.run",
                return_value=xcrun
                or subprocess.CompletedProcess([], 0, str(self.target) + "\n", ""),
            ) as run,
        ):
            result = prepend_prepared_git(
                self.root, path, declaration, platform=platform
            )
        return result, run

    def test_prepared_path_executes_and_toolchain_binds_real_target_and_hash(self):
        path, _ = self._run()
        self.assertEqual(Path(path.split(os.pathsep)[0]).resolve(), self.bin.resolve())
        env = {"PATH": path}
        output = subprocess.check_output(["git"], env=env, text=True).strip()
        stamp = resolve_toolchain(
            {"command": ["git"], "required_environment": ["git"]}, env
        )
        self.assertEqual(output, "synthetic-git")
        self.assertEqual(stamp["executor"]["realpath"], str(self.entry.resolve()))
        self.assertEqual(
            stamp["executor"]["prepared_target"]["realpath"],
            str(self.target.resolve()),
        )
        self.assertEqual(
            stamp["executor"]["prepared_target"]["sha256"],
            hashlib.sha256(self.target.read_bytes()).hexdigest(),
        )
        before = stamp
        self.target.write_text("#!/bin/sh\necho changed-git\n", encoding="utf-8")
        changed = resolve_toolchain(
            {"command": ["git"], "required_environment": ["git"]}, env
        )
        self.assertNotEqual(before, changed)
        self.assertEqual(
            changed,
            resolve_toolchain(
                {"command": ["git"], "required_environment": ["git"]}, env
            ),
        )

    def test_non_eligible_and_custom_git_selection_do_not_probe(self):
        for platform, decl, path in (
            (
                "darwin",
                {"transaction_reuse": True, "required_environment": []},
                "/usr/bin:/bin",
            ),
            (
                "linux",
                {"transaction_reuse": True, "required_environment": ["git"]},
                "/usr/bin:/bin",
            ),
            ("darwin", {"required_environment": ["git"]}, "/usr/bin:/bin"),
            (
                "darwin",
                {"transaction_reuse": False, "required_environment": ["git"]},
                "/usr/bin:/bin",
            ),
            (
                "darwin",
                {"transaction_reuse": True, "required_environment": ["git"]},
                "/custom:/usr/bin:/bin",
            ),
        ):
            result, run = self._run(platform=platform, check=decl, path=path)
            self.assertEqual(result, path)
            run.assert_not_called()

    def test_custom_git_selection_symlink_and_unprepared_are_not_optimized(self):
        with patch(
            "scripts.environment.git_runtime.shutil.which",
            return_value="/usr/local/bin/git",
        ):
            self.assertEqual(
                prepend_prepared_git(
                    self.root,
                    "/usr/bin:/bin",
                    {"transaction_reuse": True, "required_environment": ["git"]},
                    platform="darwin",
                ),
                "/usr/bin:/bin",
            )
        other = self.root / "wrapper"
        other.symlink_to(self.target)
        with patch(
            "scripts.environment.git_runtime.shutil.which", return_value=str(other)
        ):
            self.assertEqual(
                prepend_prepared_git(
                    self.root,
                    "/usr/bin:/bin",
                    {"transaction_reuse": True, "required_environment": ["git"]},
                    platform="darwin",
                ),
                "/usr/bin:/bin",
            )
        self.entry.unlink()
        self.entry.symlink_to(self.target)
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        run.assert_not_called()
        self.entry.unlink()
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        run.assert_not_called()

    def test_invalid_preparation_xcrun_and_target_fail_closed_without_mutation(self):
        original = self.entry.read_bytes()
        extra = self.bin / "other"
        extra.touch()
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        self.assertFalse(run.called)
        extra.unlink()
        for completed in (
            subprocess.CompletedProcess([], 1, "", "error"),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "relative/git", ""),
        ):
            result, _ = self._run(xcrun=completed)
            self.assertEqual(result, "/usr/bin:/bin")
        with (
            patch(
                "scripts.environment.git_runtime.subprocess.run",
                side_effect=subprocess.TimeoutExpired("xcrun", 5),
            ),
            patch(
                "scripts.environment.git_runtime.shutil.which", side_effect=self._which
            ),
        ):
            self.assertEqual(
                prepend_prepared_git(
                    self.root,
                    "/usr/bin:/bin",
                    {"transaction_reuse": True, "required_environment": ["git"]},
                    platform="darwin",
                ),
                "/usr/bin:/bin",
            )
        self.assertEqual(self.entry.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in self.bin.iterdir()), ["git"])

    def test_prepared_wrapper_target_change_disables_optimization(self):
        alternate = self.root / "alternate-git"
        alternate.write_text("#!/bin/sh\necho alternate\n", encoding="utf-8")
        alternate.chmod(0o755)
        self.entry.write_bytes(generate_prepared_git_wrapper(alternate))
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        self.assertTrue(run.called)

    def test_directory_symlink_permissions_and_non_executable_target_rejected(self):
        quality = self.root / "tmp/quality"
        moved = self.root / "quality-real"
        quality.rename(moved)
        quality.symlink_to(moved, target_is_directory=True)
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        run.assert_not_called()
        quality.unlink()
        moved.rename(quality)
        self.bin.chmod(0o777)
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        run.assert_not_called()
        self.bin.chmod(0o700)
        self.target.chmod(0o644)
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        run.assert_not_called()

    def test_fixed_wrapper_format_is_exact_and_rejects_custom_or_self_target(self):
        spaced = self.root / "空间 git 'quoted'"
        spaced.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        spaced.chmod(0o700)
        self.entry.write_bytes(generate_prepared_git_wrapper(spaced))
        self.assertEqual(parse_prepared_git_wrapper(self.entry), spaced)
        result, _ = self._run(xcrun=subprocess.CompletedProcess([], 0, str(spaced), ""))
        self.assertEqual(
            Path(result.split(os.pathsep)[0]).resolve(), self.bin.resolve()
        )

        self.entry.write_text(
            '#!/bin/sh\n# arbitrary wrapper\nexec /usr/bin/git "$@"\n'
        )
        self.entry.chmod(0o700)
        self.assertIsNone(parse_prepared_git_wrapper(self.entry))
        result, run = self._run()
        self.assertEqual(result, "/usr/bin:/bin")
        run.assert_not_called()

        self.entry.write_bytes(generate_prepared_git_wrapper(self.entry))
        self.entry.chmod(0o700)
        self.assertIsNone(parse_prepared_git_wrapper(self.entry))
        result, _ = self._run(
            xcrun=subprocess.CompletedProcess([], 0, str(self.entry), "")
        )
        self.assertEqual(result, "/usr/bin:/bin")

    def test_wrapper_preserves_arguments_and_rejects_nonregular_entry(self):
        self.target.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        arguments = ["two words", "", "single'quote", "dollar$star*", "中文"]
        output = subprocess.check_output([str(self.entry), *arguments], text=True)
        self.assertEqual(output.splitlines(), arguments)
        self.entry.unlink()
        os.mkfifo(self.entry, mode=0o700)
        self.assertIsNone(parse_prepared_git_wrapper(self.entry))

    def test_wrapper_preserves_real_git_resource_path_semantics(self):
        selected = shutil.which("git")
        if sys.platform == "darwin":
            probe = subprocess.run(
                ["/usr/bin/xcrun", "--find", "git"],
                capture_output=True,
                text=True,
                check=True,
            )
            selected = probe.stdout.strip()
        self.assertTrue(selected)
        real_git = Path(selected).resolve(strict=True)
        wrapper_dir = self.root / "actual-wrapper-bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_bytes(generate_prepared_git_wrapper(real_git))
        wrapper.chmod(0o700)
        env = {"PATH": str(wrapper_dir)}

        def git(args, cwd=None):
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )

        for option in ("--exec-path", "--html-path"):
            self.assertEqual(
                git([option]).stdout,
                subprocess.check_output([str(real_git), option], env=env, text=True),
            )
        symlink_dir = self.root / "symlink-bin"
        symlink_dir.mkdir()
        (symlink_dir / "git").symlink_to(real_git)
        symlink_env = {"PATH": str(symlink_dir)}
        if sys.platform == "darwin":
            for option in ("--exec-path", "--html-path"):
                linked = subprocess.run(
                    ["git", option],
                    env=symlink_env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertNotEqual(
                    linked.stdout,
                    subprocess.check_output(
                        [str(real_git), option], env=env, text=True
                    ),
                )
        direct_repo = self.root / "direct-repo"
        wrapped_repo = self.root / "wrapped-repo"
        direct_repo.mkdir()
        wrapped_repo.mkdir()
        direct = subprocess.run(
            [str(real_git), "init", "-q"],
            cwd=direct_repo,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
        wrapped = git(["init", "-q"], cwd=wrapped_repo)
        self.assertEqual(wrapped.returncode, direct.returncode)
        self.assertEqual(wrapped.stderr, direct.stderr)
        self.assertEqual(direct.stderr, "")

        def template_files(repo):
            return {
                str(path.relative_to(repo / ".git")): path.read_bytes()
                for directory in ("hooks", "info")
                for path in (repo / ".git" / directory).rglob("*")
                if path.is_file()
            }

        templates = template_files(direct_repo)
        self.assertTrue(templates, "real Git init must install default template files")
        self.assertEqual(templates, template_files(wrapped_repo))

    def test_runtime_applies_after_java_path_and_filters_xcrun_parent_environment(self):
        java = self.root / "jdk"
        (java / "bin").mkdir(parents=True)
        env = {
            "PATH": "/usr/bin:/bin",
            "DEVELOPER_DIR": "/poison",
            "SDKROOT": "/poison",
        }
        expected_java_path = str(java / "bin")
        with (
            patch(
                "scripts.environment.git_runtime.prepared_git_bin",
                side_effect=lambda root, path, **_: prepared_git_bin(
                    root, path, platform="darwin"
                ),
            ),
            patch("scripts.environment.runtime.resolve_java_home", return_value=java),
            patch(
                "scripts.environment.git_runtime.shutil.which",
                side_effect=lambda name, path=None: (
                    "/usr/bin/git"
                    if name == "git" and path == f"{expected_java_path}:/usr/bin:/bin"
                    else None
                ),
            ),
            patch(
                "scripts.environment.git_runtime.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, str(self.target), ""),
            ) as run,
        ):
            values = execution_environment(
                self.root,
                {
                    "required_environment": ["java-25-temurin", "git"],
                    "transaction_reuse": True,
                },
                env,
            )
        self.assertEqual(
            Path(values["PATH"].split(os.pathsep)[0]).resolve(), self.bin.resolve()
        )
        self.assertEqual(
            values["PATH"].split(os.pathsep)[1:],
            [expected_java_path, "/usr/bin", "/bin"],
        )
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertNotIn("DEVELOPER_DIR", values)
        self.assertNotIn("SDKROOT", values)
        self.assertEqual(
            run.call_args.kwargs["env"], {"PATH": f"{expected_java_path}:/usr/bin:/bin"}
        )

    def test_non_java_check_executes_prepared_git_and_report_rejects_environment_drift(
        self,
    ):
        from scripts.verification import (
            freeze_inputs,
            persist_report,
            read_report,
            verify_profile,
        )

        # 真正执行一个声明了 Git 的非 Java Check，并通过公开报告入口复核。
        harness = self.root / "harness"
        harness.mkdir()
        (self.root / "src").mkdir()
        (self.root / "src/input.txt").write_text("fixture")
        check = {
            "check_id": "fixture.git",
            "module": "fixture",
            "command": [
                "python3",
                "-c",
                "import subprocess,json; assert subprocess.check_output(['git'], text=True).strip() == 'synthetic-git'; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))",
            ],
            "executable": "python3",
            "cwd": ".",
            "timeout_seconds": 5,
            "scope": "repository-baseline",
            "triggers": [{"path": "src/"}],
            "module_dependencies": [],
            "required_environment": ["python3", "git"],
            "input_paths": ["src"],
            "transaction_reuse": True,
            "result_contract": {
                "type": "exit-code",
                "completeness_guarantee": "synthetic Git assertion",
            },
        }
        write_declarations(harness / "module-checks.yaml", [check])
        (harness / "ci-policy.yaml").write_text(
            yaml.safe_dump({"formal_only_check_ids": ["fixture.formal"]})
        )
        real_run = subprocess.run

        def run(argv, **kwargs):
            if argv == ["/usr/bin/xcrun", "--find", "git"]:
                self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin"})
                return subprocess.CompletedProcess(argv, 0, str(self.target), "")
            return real_run(argv, **kwargs)

        with (
            patch.dict(
                os.environ, {"PATH": "/usr/bin:/bin", "DEVELOPER_DIR": "/poison"}
            ),
            patch(
                "scripts.environment.git_runtime.prepared_git_bin",
                side_effect=lambda root, path, **_: prepared_git_bin(
                    root, path, platform="darwin"
                ),
            ),
            patch("scripts.environment.git_runtime.subprocess.run", side_effect=run),
        ):
            freeze = freeze_inputs(self.root, verification_scope="development-baseline")
            self.assertEqual(freeze["result"], "PASS", freeze)
            report = verify_profile(self.root, frozen_inputs=freeze)
            self.assertEqual(report["result"], "PASS", report)
            git_stamp = report["checks"][0]["toolchain"]["tools"]["git"]
            self.assertEqual(git_stamp["realpath"], str(self.entry.resolve()))
            self.assertEqual(
                git_stamp["prepared_target"]["realpath"], str(self.target.resolve())
            )
            persist_report(self.root, report)
            self.assertEqual(read_report(self.root, report["run_id"])[0], report)
            self.target.write_text("#!/bin/sh\necho changed-git\n")
            with self.assertRaises(ValueError):
                read_report(self.root, report["run_id"])
            self.target.write_text("#!/bin/sh\necho synthetic-git\n")
            self.assertEqual(read_report(self.root, report["run_id"])[0], report)
            self.entry.unlink()
            with self.assertRaises(ValueError):
                read_report(self.root, report["run_id"])


if __name__ == "__main__":
    unittest.main()
