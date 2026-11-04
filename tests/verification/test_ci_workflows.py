"""验证 CI 的触发、信任与最小权限边界，不伪装 GitHub 上的真实运行。"""

import re
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader
    )


def safe_workflow(name):
    return yaml.safe_load((ROOT / ".github/workflows" / name).read_text())


class WorkflowContractTest(unittest.TestCase):
    def test_release_regressions_are_consumed_by_real_quick_plan(self):
        """真实声明、依赖闭包及输入快照均参与；不运行第二套发布流水线。"""
        from scripts.triggers import run_ci_quick_checks as ci

        base = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        for path, required in (
            (
                "ops/release/workflow/promote_verified_release.py",
                {"eng.release.build", "eng.verification.module-tests"},
            ),
            (
                "ops/release/tests/workflow.test.mjs",
                {"eng.release.build", "eng.verification.module-tests"},
            ),
            (
                "tests/verification/test_release_promotion.py",
                {"eng.verification.module-tests"},
            ),
            (
                "tests/verification/test_ci_workflows.py",
                {"eng.verification.module-tests"},
            ),
        ):
            with (
                self.subTest(path=path),
                patch.object(ci, "changed_paths", return_value=[path]),
            ):
                plan = ci.make_plan(ROOT, base)
            self.assertEqual(plan["status"], "PASS")
            self.assertTrue(required <= set(plan["selected_check_ids"]))
            self.assertEqual(plan["checks_executed"], 0)
            self.assertFalse(plan["full_repository_executed"])
            self.assertFalse(plan["formal_eligible"])
            self.assertNotIn(
                "eng.release.lifecycle-runtime", plan["selected_check_ids"]
            )
            self.assertNotIn(
                "eng.release.candidate-runtime", plan["selected_check_ids"]
            )

    def test_real_promotion_shell_forwards_exactly_one_command(self):
        """执行 YAML 原始 shell；替换工具运输层，不访问发行宿主或 GitHub。"""
        jobs = workflow("release.yml")["jobs"]
        with tempfile.TemporaryDirectory(prefix="release-shell-") as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            python = root / ".local/lexiflow-python/bin/python3"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o700)
            tools = root / "tools"
            tools.mkdir()
            capture = root / "arguments.jsonl"
            node = tools / "node"
            # argv 原样落盘（含空格），而非用 shell 字符串模拟正确结果。
            import sys

            node.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "if sys.argv[1:2] == ['-e']:\n"
                " value = json.load(sys.stdin)\n"
                " assert value['status'] == 'PASS' and value['releaseBody']\n"
                " with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as out:\n"
                "  out.write(value['releaseBody'] + '\\n')\n"
                " sys.exit(0)\n"
                "with open(os.environ['CAPTURE'], 'a') as out:\n"
                " out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                "print(json.dumps({'status': 'PASS', 'releaseBody': '# 1. 1.2.3\\n\\n合成预览'}))\n"
            )
            node.chmod(0o700)
            git = tools / "git"
            git.write_text('#!/bin/sh\nprintf "%s\\n" "$EXPECTED_COMMIT"\n')
            git.chmod(0o700)
            cases = (
                ("promotion-precheck", "false", []),
                ("promotion", "false", ["--publish"]),
                ("promotion", "true", ["--publish", "--confirmed-recovery"]),
            )
            for job, recovery, flags in cases:
                with self.subTest(job=job, recovery=recovery):
                    capture.unlink(missing_ok=True)
                    summary = root / "summary.md"
                    summary.write_text("")
                    step = next(
                        x for x in jobs[job]["steps"] if "resume" in x.get("run", "")
                    )
                    env = {
                        "PATH": str(tools) + os.pathsep + os.defpath,
                        "WORKSPACE": str(root),
                        "LEXIFLOW_RELEASE_NODE_BIN": str(tools),
                        "SUBMISSION_ID": "synthetic submission",
                        "RELEASE_TAG": "v1.2.3",
                        "CONFIRMED_RECOVERY": recovery,
                        "EXPECTED_COMMIT": "a" * 40,
                        "CAPTURE": str(capture),
                        "GITHUB_STEP_SUMMARY": str(summary),
                    }
                    result = subprocess.run(
                        ["/bin/sh", "-eu", "-c", step["run"]],
                        cwd=root,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        summary.read_text(),
                        "# 1. 1.2.3\n\n合成预览\n"
                        if job == "promotion-precheck"
                        else "",
                    )
                    self.assertEqual(
                        [json.loads(line) for line in capture.read_text().splitlines()],
                        [
                            [
                                "ops/release/workflow/run-release-workflow.mjs",
                                "resume",
                                "synthetic submission",
                                "v1.2.3",
                                *flags,
                            ]
                        ],
                    )

    def test_human_visible_workflow_labels_are_chinese(self):
        chinese = re.compile(r"[\u3400-\u9fff]")
        for filename in ["ci.yml", "release.yml"]:
            value = workflow(filename)
            self.assertRegex(value["name"], chinese)
            self.assertRegex(value["run-name"], chinese)
            for job in value["jobs"].values():
                self.assertRegex(job.get("name", ""), chinese)
                for step in job["steps"]:
                    self.assertRegex(step.get("name", ""), chinese)
                    if "uses" in step:
                        self.assertRegex(step["name"], chinese)
        release = workflow("release.yml")
        for field in release["on"]["workflow_dispatch"]["inputs"].values():
            self.assertRegex(field["description"], chinese)

    def test_quick_only_branches_pr_and_manual_on_hosted_runner(self):
        value = workflow("ci.yml")
        self.assertEqual(
            set(value["on"]), {"push", "pull_request", "workflow_dispatch"}
        )
        self.assertEqual(value["on"]["push"], {"branches": ["**"]})
        self.assertEqual(value["permissions"], {"contents": "read"})
        job = value["jobs"]["quick"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertNotIn("environment", job)
        run = "\n".join(step.get("run", "") for step in job["steps"])
        self.assertIn(
            'scripts.triggers.run_ci_quick_checks quick --base "$BASE_SHA"', run
        )
        self.assertIn("scripts.triggers.run_ci_quick_checks quick --all", run)
        for banned in [
            "gh release",
            "run_repository_checks.py",
            "check_changes.py",
            "release_runtime_check",
        ]:
            self.assertNotIn(banned, run)
        self.assertNotIn("secrets.", (ROOT / ".github/workflows/ci.yml").read_text())

    def test_archive_is_checked_and_loaded_before_upload(self):
        steps = workflow("ci.yml")["jobs"]["quick"]["steps"]
        package = next(step for step in steps if step.get("id") == "package")
        self.assertIn(
            "node extension/scripts/packaging/package-extension-release.mjs --development",
            package["run"],
        )
        self.assertIn(
            'extension/scripts/checks/check-extension-archive.mjs --development "$ZIP" "$GITHUB_SHA"',
            package["run"],
        )
        self.assertIn(
            'archive-smoke.mjs --development "$ZIP" "$GITHUB_SHA"', package["run"]
        )
        upload = next(
            step for step in steps if "upload-artifact@" in step.get("uses", "")
        )
        self.assertLess(steps.index(package), steps.index(upload))
        self.assertNotIn("if", package)
        self.assertNotIn("if", upload)

    def test_release_requires_clean_tag_before_protected_native_runner(self):
        value = workflow("release.yml")
        self.assertEqual(set(value["on"]), {"push", "workflow_dispatch"})
        self.assertEqual(value["on"]["push"], {"tags": ["v*"]})
        self.assertEqual(
            set(value["on"]["workflow_dispatch"]["inputs"]),
            {
                "submission_id",
                "release_tag",
                "publish_confirmation",
                "confirmed_recovery",
            },
        )
        recovery = value["on"]["workflow_dispatch"]["inputs"]["confirmed_recovery"]
        self.assertEqual(recovery["type"], "boolean")
        self.assertEqual(recovery["default"], "false")
        self.assertEqual(recovery["required"], "false")
        self.assertEqual(value["permissions"], {"contents": "read"})
        source = value["jobs"]["source"]
        self.assertEqual(source["runs-on"], "ubuntu-24.04")
        self.assertIn("github.event_name == 'workflow_dispatch'", source["if"])
        source_run = "\n".join(step.get("run", "") for step in source["steps"])
        self.assertIn(
            "git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main",
            source_run,
        )
        self.assertNotIn("+refs/heads/main:refs/remotes/origin/main", source_run)
        self.assertIn(
            'git merge-base --is-ancestor "$(git rev-parse HEAD)" refs/remotes/origin/main',
            source_run,
        )
        self.assertIn(
            'ops/release/integrity/source-build-identity.mjs --tag "$RELEASE_TAG"',
            "\n".join(step.get("run", "") for step in source["steps"]),
        )
        full = value["jobs"]["prepare"]
        self.assertEqual(full["needs"], "source")
        self.assertEqual(
            full["runs-on"], ["self-hosted", "macOS", "ARM64", "lexiflow-release"]
        )
        self.assertEqual(
            full["environment"], {"name": "release-internal", "deployment": "false"}
        )
        self.assertIs(
            safe_workflow("release.yml")["jobs"]["prepare"]["environment"][
                "deployment"
            ],
            False,
        )
        self.assertEqual(value["concurrency"]["cancel-in-progress"], "false")
        self.assertEqual(value["concurrency"]["group"], "lexiflow-release-host")
        run = "\n".join(step.get("run", "") for step in full["steps"])
        self.assertIn('git -C "$WORKSPACE" rev-parse HEAD', run)
        self.assertIn(
            '"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs prepare "$REQUEST_FILE"',
            run,
        )
        self.assertNotIn("scripts/check_changes.py", run)
        self.assertNotIn("run_repository_checks.py", run)
        self.assertNotIn("scripts.acceptance validate", run)
        self.assertNotIn("actions/checkout", str(full))
        self.assertNotIn("contents: write", str(full))
        for banned in [
            "prune",
            "machine init",
            "machine set",
            "gh release",
            "git clean",
            "git reset",
            "continue-on-error",
        ]:
            self.assertNotIn(banned, run)
        dry = value["jobs"]["promotion-precheck"]
        publish = value["jobs"]["promotion"]
        self.assertEqual(
            dry["environment"], {"name": "release-internal", "deployment": "false"}
        )
        self.assertEqual(dry["needs"], "source")
        self.assertNotIn("continue-on-error", str(dry))
        self.assertNotIn("GITHUB_TOKEN", str(dry))
        dry_run = "\n".join(step.get("run", "") for step in dry["steps"])
        dry_step = next(
            step for step in dry["steps"] if "resume" in step.get("run", "")
        )
        self.assertEqual(
            dry_step["env"]["EXPECTED_COMMIT"], "${{ needs.source.outputs.commit }}"
        )
        self.assertIn(
            'test "$(git -C "$WORKSPACE" rev-parse HEAD)" = "$EXPECTED_COMMIT"', dry_run
        )
        self.assertEqual(dry["permissions"], {"contents": "read"})
        self.assertEqual(
            publish["permissions"],
            {"actions": "read", "contents": "write", "packages": "write"},
        )
        self.assertEqual(publish["needs"], ["source", "promotion-precheck"])
        self.assertEqual(publish["environment"], "release-public")
        safe_jobs = safe_workflow("release.yml")["jobs"]
        self.assertIs(
            safe_jobs["promotion-precheck"]["environment"]["deployment"], False
        )
        self.assertEqual(safe_jobs["promotion"]["environment"], "release-public")
        self.assertNotIn(
            "deployment: false", str(safe_jobs["promotion"]["environment"]).lower()
        )
        self.assertNotIn("continue-on-error", str(publish))
        self.assertNotIn("GITHUB_TOKEN", str(dry))
        self.assertIn("inputs.release_tag == github.ref_name", dry["if"])
        self.assertIn("inputs.release_tag == github.ref_name", publish["if"])
        self.assertIn("refs/tags/v", dry["if"])
        self.assertIn("refs/tags/v", publish["if"])
        self.assertIn('resume "$SUBMISSION_ID" "$RELEASE_TAG"', str(dry["steps"]))
        self.assertIn(
            'resume "$SUBMISSION_ID" "$RELEASE_TAG" --publish', str(publish["steps"])
        )
        self.assertNotIn("confirmed_recovery", str(dry))
        self.assertNotIn("--confirmed-recovery", str(dry))
        promotion_step = publish["steps"][-1]
        self.assertEqual(
            promotion_step["env"]["CONFIRMED_RECOVERY"],
            "${{ inputs.confirmed_recovery }}",
        )
        promotion_run = promotion_step["run"]
        self.assertIn('if [ "$CONFIRMED_RECOVERY" = "true" ]; then', promotion_run)
        self.assertIn(
            'resume "$SUBMISSION_ID" "$RELEASE_TAG" --publish --confirmed-recovery',
            promotion_run,
        )
        self.assertIn(
            'else\n  "$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs resume "$SUBMISSION_ID" "$RELEASE_TAG" --publish\nfi',
            promotion_run,
        )
        self.assertNotIn("eval", promotion_run)
        self.assertNotIn("GITHUB_TOKEN", str(dry))
        self.assertNotIn("LEXIFLOW_RELEASE_CONFIG_TOKEN", str(dry))
        self.assertIn("GITHUB_TOKEN", str(publish["steps"][-1]))
        self.assertIn("LEXIFLOW_RELEASE_CONFIG_TOKEN", str(publish["steps"][-1]))
        for job in [full, dry, publish]:
            self.assertNotIn("actions/checkout", str(job))
            self.assertNotIn("upload-artifact", str(job))

    def test_all_actions_pinned_and_checkout_credentials_not_persisted(self):
        for name in ["ci.yml", "release.yml"]:
            value = workflow(name)
            for job in value["jobs"].values():
                self.assertNotIn("continue-on-error", job)
                for step in job["steps"]:
                    if "uses" in step:
                        self.assertRegex(
                            step["uses"], r"^actions/[a-z-]+@[a-f0-9]{40}$"
                        )
                        if "upload-artifact" in step["uses"]:
                            self.assertEqual(name, "ci.yml")
                            self.assertEqual(step["with"]["retention-days"], "14")
                            self.assertEqual(step["with"]["if-no-files-found"], "error")
                            self.assertEqual(
                                step["with"]["include-hidden-files"], "false"
                            )
                            self.assertEqual(
                                step["with"]["path"].splitlines(),
                                [
                                    "${{ steps.package.outputs.zip }}",
                                    "${{ steps.package.outputs.zip }}.sha256",
                                ],
                            )
                    if step.get("uses", "").startswith("actions/checkout@"):
                        self.assertEqual(step["with"]["persist-credentials"], "false")
                        self.assertEqual(step["with"]["fetch-depth"], "0")
                    self.assertNotRegex(
                        step.get("run", ""), r"\$\{\{\s*github\.event\."
                    )

    def test_native_release_commands_require_the_dedicated_python_environment(self):
        jobs = workflow("release.yml")["jobs"]
        selected = {}
        for name in ["prepare", "promotion-precheck", "promotion"]:
            for step in jobs[name]["steps"]:
                run = step.get("run", "")
                if (
                    "run-release-workflow.mjs" in run
                    or "run_repository_checks.py" in run
                ):
                    self.assertNotIn(name, selected)
                    selected[name] = run
        self.assertEqual(set(selected), {"prepare", "promotion-precheck", "promotion"})
        self.assertIn(' prepare "$REQUEST_FILE"', selected["prepare"])
        self.assertIn(
            '"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs resume "$SUBMISSION_ID" "$RELEASE_TAG"',
            selected["promotion-precheck"],
        )
        self.assertNotIn("--publish", selected["promotion-precheck"])
        self.assertIn(
            '"$LEXIFLOW_RELEASE_NODE_BIN/node" ops/release/workflow/run-release-workflow.mjs resume "$SUBMISSION_ID" "$RELEASE_TAG" --publish',
            selected["promotion"],
        )
        prerequisite = 'test -x "$WORKSPACE/.local/lexiflow-python/bin/python3"'
        activate = 'export PATH="$WORKSPACE/.local/lexiflow-python/bin:$LEXIFLOW_RELEASE_NODE_BIN:$PATH"'
        for run in selected.values():
            self.assertIn('test -x "$LEXIFLOW_RELEASE_NODE_BIN/node"', run)
        for run in selected.values():
            with self.subTest(run=run):
                self.assertIn(prerequisite, run)
                self.assertIn(activate, run)
                self.assertLess(run.index(prerequisite), run.index(activate))
                self.assertLess(run.index(activate), run.index('cd "$WORKSPACE"'))
                self.assertNotIn("pip install", run)
                self.assertNotIn("python3 -m venv", run)

        jobs = workflow("release.yml")["jobs"]
        private_names = {
            "LEXIFLOW_RELEASE_NODE_BIN",
            "LEXIFLOW_JAVA_HOME",
            "LEXIFLOW_REDIS_TEST_ENDPOINT",
            "LEXIFLOW_RELEASE_ARM64_DOCKER_HOST",
            "LEXIFLOW_RELEASE_GRADLE_CACHE",
            "LEXIFLOW_RELEASE_NPM_CACHE",
        }
        for name in ["prepare", "promotion-precheck", "promotion"]:
            self.assertTrue(private_names.issubset(jobs[name]["env"]))
        for name in private_names:
            self.assertEqual(
                jobs["prepare"]["env"][name], jobs["promotion-precheck"]["env"][name]
            )
            self.assertEqual(
                jobs["prepare"]["env"][name], jobs["promotion"]["env"][name]
            )


if __name__ == "__main__":
    unittest.main()
