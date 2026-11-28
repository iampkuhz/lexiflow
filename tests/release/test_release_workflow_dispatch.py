"""发行工作流只能从已验证的来源进入受限专用执行器。"""

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class ReleaseWorkflowDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / ".github/workflows/release.yml").open(encoding="utf-8") as stream:
            cls.workflow = yaml.load(stream, Loader=yaml.BaseLoader)
        cls.jobs = cls.workflow["jobs"]
        cls.admission = next(
            step for step in cls.jobs["source"]["steps"] if step.get("id") == "admit"
        )

    def test_only_dispatch_can_trigger_release(self):
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        inputs = self.workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["operation"]["options"], ["prepare", "publish"])
        self.assertEqual(self.jobs["source"]["runs-on"], "ubuntu-24.04")

    def test_prepare_binds_protected_main_ci_and_unissued_tag(self):
        checks = self.admission["run"]
        for required in (
            'test "$EVENT_REF_PROTECTED" = true',
            'test "$EVENT_REF" = refs/heads/main',
            'test "$EXPECTED_COMMIT" = "$EVENT_SHA"',
            'test "$EXPECTED_COMMIT" = "$(git rev-parse refs/remotes/origin/main)"',
            "actions/workflows/ci.yml/runs?branch=main&event=push&head_sha=$EXPECTED_COMMIT",
            'run.conclusion === "success"',
            'source-build-identity.mjs --plan "$RELEASE_TAG"',
            'test -z "$REMOTE_TAGS"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, checks)
        prepare = self.jobs["prepare"]
        self.assertEqual(prepare["needs"], "source")
        self.assertEqual(prepare["permissions"], {"contents": "read"})
        self.assertEqual(
            prepare["environment"], {"name": "release-internal", "deployment": "false"}
        )
        self.assertNotIn("secrets.", str(prepare))
        self.assertIn(
            'podman --url "$LEXIFLOW_RELEASE_ARM64_DOCKER_HOST" info',
            prepare["steps"][0]["run"],
        )

    def test_source_or_precheck_failure_never_dispatches_dependent_jobs(self):
        for name, operation in (
            ("prepare", "prepare"),
            ("promotion-precheck", "publish"),
            ("promotion", "publish"),
        ):
            self.assertEqual(
                self.jobs[name]["if"],
                f"success() && needs.source.outputs.operation == '{operation}'",
            )
        self.assertEqual(self.jobs["promotion-precheck"]["needs"], "source")
        self.assertEqual(
            self.jobs["promotion"]["needs"], ["source", "promotion-precheck"]
        )

    def test_exclusive_public_job_requires_actual_tag_and_can_write(self):
        checks = self.admission["run"]
        self.assertIn('test "$EVENT_REF" = "refs/tags/$RELEASE_TAG"', checks)
        self.assertIn('test "$PUBLISH_CONFIRMATION" = PUBLISH', checks)
        self.assertIn('source-build-identity.mjs --tag "$RELEASE_TAG"', checks)
        self.assertEqual(self.jobs["promotion"]["environment"], "release-public")
        self.assertEqual(
            [
                job
                for job, config in self.jobs.items()
                if "write" in config.get("permissions", {}).values()
            ],
            ["promotion"],
        )


if __name__ == "__main__":
    unittest.main()
