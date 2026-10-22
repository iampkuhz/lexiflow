"""验证 CI 的触发、信任与最小权限边界，不伪装 GitHub 上的真实运行。"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader
    )


class WorkflowContractTest(unittest.TestCase):
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
            "node extension/scripts/packaging/package-extension-release.mjs",
            package["run"],
        )
        self.assertIn(
            'extension/scripts/checks/check-extension-archive.mjs "$ZIP" "$GITHUB_SHA"',
            package["run"],
        )
        self.assertIn('archive-smoke.mjs "$ZIP" "$GITHUB_SHA"', package["run"])
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
            {"submission_id", "release_tag", "publish_confirmation"},
        )
        self.assertEqual(value["permissions"], {"contents": "read"})
        source = value["jobs"]["source"]
        self.assertEqual(source["runs-on"], "ubuntu-24.04")
        self.assertIn("github.event_name == 'push'", source["if"])
        self.assertIn(
            'ops/release/integrity/source-build-identity.mjs --tag "$RELEASE_TAG"',
            "\n".join(step.get("run", "") for step in source["steps"]),
        )
        full = value["jobs"]["prepare"]
        self.assertEqual(full["needs"], "source")
        self.assertEqual(
            full["runs-on"], ["self-hosted", "macOS", "ARM64", "lexiflow-release"]
        )
        self.assertEqual(full["environment"], "lexiflow-release-validation")
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
        self.assertEqual(dry["permissions"], {"contents": "read"})
        self.assertEqual(
            publish["permissions"], {"contents": "write", "packages": "write"}
        )
        self.assertEqual(publish["needs"], "promotion-precheck")
        self.assertEqual(publish["environment"], "lexiflow-release-promotion")
        self.assertIn("inputs.release_tag == github.ref_name", dry["if"])
        self.assertIn("inputs.release_tag == github.ref_name", publish["if"])
        self.assertIn("refs/tags/v", dry["if"])
        self.assertIn("refs/tags/v", publish["if"])
        self.assertIn('resume "$SUBMISSION_ID" "$RELEASE_TAG"', str(dry["steps"]))
        self.assertIn(
            'resume "$SUBMISSION_ID" "$RELEASE_TAG" --publish', str(publish["steps"])
        )
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
            "LEXIFLOW_POSTGRES_TEST_JDBC_URL",
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
