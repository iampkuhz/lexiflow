"""缺失或冲突的 producer 身份在 CLI 和送验边界保留原三态。"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agents.work_packages.codex_work_package_publisher import CodexWorkPackageError
from scripts.agents.work_packages.verify_codex_work_package import main as codex_main
from scripts.acceptance.__main__ import main as gate_main
from scripts.acceptance.evidence.implementation_run_evidence import ProducerError, _codex
from scripts.acceptance.task_operations.submit_task_for_acceptance import _err, submit
from tests.delivery_gate.fixtures import (
    DeliveryGateFixture,
    PRODUCER_SESSION,
    make_mock_runtime,
)


class StatusPropagationTest(unittest.TestCase):
    def test_codex_cli_keeps_non_pass_status(self):
        for status in ("BLOCKED", "FAIL"):
            with (
                self.subTest(status=status),
                patch(
                    "scripts.agents.work_packages.codex_work_package_publisher.CodexWorkPackagePublisher.verify",
                    side_effect=CodexWorkPackageError(
                        "runtime-test", "fixture", status=status
                    ),
                ),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertNotEqual(codex_main(["verify", "--run-id", "fixture"]), 0)
                self.assertEqual(json.loads(output.getvalue())["status"], status)

    def test_producer_and_submission_wrapping_keeps_non_pass_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            completion = (
                root / "tmp/quality/codex-work-packages/fixture/package-completion.json"
            )
            completion.parent.mkdir(parents=True)
            completion.write_text("{}")
            for status in ("BLOCKED", "FAIL"):
                with (
                    self.subTest(status=status),
                    patch(
                        "scripts.agents.work_packages.codex_work_package_publisher.CodexWorkPackagePublisher.verify",
                        side_effect=CodexWorkPackageError(
                            "runtime-test", "fixture", status=status
                        ),
                    ),
                    self.assertRaises(ProducerError) as caught,
                ):
                    _codex(root, "fixture", {})
                self.assertEqual(caught.exception.status, status)
                self.assertEqual(_err(caught.exception).status, status)

    def test_submit_and_public_cli_do_not_flatten_producer_result(self):
        fixture = DeliveryGateFixture()
        self.addCleanup(fixture.cleanup)
        fixture.create_verification_report()
        args = dict(
            task_id="LF-TSK-TEST-0001",
            change_report_id=fixture.report_id,
        )
        with patch(
            "scripts.agents.identity.codex_actor_identity.discover",
            return_value=make_mock_runtime(PRODUCER_SESSION),
        ):
            for status, exit_code in (("BLOCKED", 2), ("FAIL", 1)):
                with (
                    self.subTest(status=status),
                    patch(
                        "scripts.acceptance.task_operations.submit_task_for_acceptance.resolve_producer",
                        side_effect=ProducerError(
                            "producer-run-invalid", "fixture", status=status
                        ),
                    ),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    with self.assertRaises(ValueError) as caught:
                        submit(fixture.root, **args)
                    self.assertEqual(caught.exception.status, status)
                    code = gate_main(
                        [
                            "submit",
                            "--task-id",
                            args["task_id"],
                            "--change-report-id",
                            fixture.report_id,
                        ],
                        root=fixture.root,
                    )
                    self.assertEqual(code, exit_code)
                    self.assertEqual(json.loads(output.getvalue())["result"], status)
