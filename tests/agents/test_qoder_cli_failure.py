"""Offline failure classification and CLI-run provenance, with no live requests."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.agents.qoder.failure_diagnostics import (
    access_blocked, compact_failure_signal, summarize_cli_failure,
)


class QoderCliFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.stdout = self.root / "stdout.log"
        self.session = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        self.started = 1789575765.0
        self.pid = 3878
        self.logs = self.root / "runs"

    def result(self, **fields):
        value = {"type": "result", "subtype": "error_during_execution", "is_error": True, **fields}
        self.stdout.write_text(json.dumps(value) + "\n")

    def pricing(self):
        self.result(errors=[json.dumps({"pricingUrl": "https://qoder.com/pricing?client=qodercli"})],
                    usage={"input_tokens": 0, "output_tokens": 0})

    def transport(self, **manifest_updates):
        directory = self.logs / f"actual-p{self.pid}"
        directory.mkdir(parents=True)
        manifest = {"pid": self.pid, "cwd": str(self.root),
                    "started_at": datetime.fromtimestamp(self.started, timezone.utc).isoformat(),
                    "argv": ["qodercli", "-p", "SECRET_PROMPT", "--session-id", self.session],
                    **manifest_updates}
        (directory / "manifest.json").write_text(json.dumps(manifest))
        body = {"code": "112", "message": json.dumps({"pricingUrl": "https://qoder.com/pricing?client=qodercli"})}
        message = "Qoder API error: FORBIDDEN - " + json.dumps(body)
        (directory / "qodercli.log").write_text(
            f"ERROR [session={self.session} turn=example] model.request.attempt_failed "
            f"error_status=403 error_message={json.dumps(message)}\n"
        )
        return directory

    def summarize(self, exit_code=1, **extra):
        return summarize_cli_failure(self.stdout, exit_code, **extra)

    def matched(self):
        return self.summarize(pid=self.pid, session_id=self.session, cwd=self.root,
                              started_at=self.started, log_root=self.logs)

    def test_pricing_only_is_not_claimed_as_exhausted_credit(self):
        self.pricing()
        result = self.summarize()
        self.assertEqual(result["category"], "billing_check_required")
        self.assertIsNone(result["service_code"])
        self.assertFalse(result["retryable"])
        self.assertTrue(result["zero_token_turn"])

    def test_actual_403_112_is_recovered_without_raw_log_or_prompt(self):
        self.pricing()
        self.transport()
        result = self.matched()
        self.assertEqual((result["http_status"], result["service_code"]), (403, 112))
        self.assertEqual(result["category"], "billing_access_rejected")
        self.assertFalse(result["retryable"])
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertNotIn("pricingUrl", json.dumps(result))

    def test_provenance_mismatches_do_not_import_another_run(self):
        cases = [
            {"pid": 999}, {"cwd": str(self.root / "other")},
            {"started_at": datetime.fromtimestamp(self.started + 60, timezone.utc).isoformat()},
            {"argv": ["qodercli", "--session-id", "another-session"]},
        ]
        for update in cases:
            with self.subTest(update=update):
                self.pricing()
                self.transport(**update)
                self.assertIsNone(self.matched()["service_code"])
                shutil.rmtree(self.logs)

    def test_normalized_118_and_transport_112_are_preserved_separately(self):
        self.pricing()
        result = json.loads(self.stdout.read_text())
        result["error_code"] = 118
        self.stdout.write_text(json.dumps(result))
        self.transport()
        failure = self.matched()
        self.assertEqual(failure["category"], "quota")
        self.assertEqual((failure["result_error_code"], failure["service_code"], failure["http_status"]),
                         (118, 112, 403))

    def test_log_event_session_and_ambiguity_are_rejected(self):
        self.pricing()
        directory = self.transport()
        path = directory / "qodercli.log"
        original = path.read_text()
        path.write_text(original.replace(self.session, "another-session"))
        self.assertIsNone(self.matched()["service_code"])
        path.write_text(original)
        shutil.copytree(directory, self.logs / f"ambiguous-p{self.pid}")
        self.assertIsNone(self.matched()["service_code"])

    def test_symlinked_run_log_is_not_read(self):
        self.pricing()
        directory = self.transport()
        path = directory / "qodercli.log"
        target = self.root / "foreign-log"
        path.rename(target)
        path.symlink_to(target)
        self.assertIsNone(self.matched()["service_code"])

    def test_structured_quota_and_authentication_codes(self):
        for code, category in [(105, "authentication"), (118, "quota"), (100403, "access_denied")]:
            with self.subTest(code=code):
                self.result(error_code=code, errors=["SECRET_ACCESS_TOKEN"])
                result = self.summarize()
                self.assertEqual(result["category"], category)
                self.assertTrue(access_blocked(result))
                self.assertNotIn("SECRET", compact_failure_signal(result))

    def test_service_and_process_exit_namespaces_do_not_mix(self):
        self.result(error_code=41)
        self.assertEqual(self.summarize()["category"], "runtime_failure")
        self.stdout.write_text("")
        self.assertEqual(self.summarize(41)["category"], "authentication")

    def test_only_known_transient_failures_are_retryable(self):
        for code, expected in [(500, True), (10500, True), (80411, False), (123456, False)]:
            self.result(error_code=code)
            result = self.summarize()
            self.assertEqual(result["result_error_code"], code)
            self.assertEqual(result["retryable"], expected)

    def test_machine_json_errors_and_stream_output(self):
        self.result(errors=[json.dumps({"code": "113", "message": "unknown"})])
        raw = self.stdout.read_text()
        self.stdout.write_text('{"type":"system"}\n'+raw)
        result = self.summarize()
        self.assertEqual(result["service_code"], 113)
        self.assertIsNone(result["result_error_code"])
        self.assertEqual(result["category"], "runtime_failure")

    def test_malformed_or_injected_fields_do_not_enter_signal(self):
        self.result(subtype=["inject"], errors=["code 118: human text is not a machine code"])
        result = self.summarize()
        self.assertEqual(result["category"], "runtime_failure")
        signal = compact_failure_signal({"category": "quota\nINJECT", "service_code": "118\nINJECT",
                                         "action": "SECRET", "http_status": True})
        self.assertNotIn("INJECT", signal)
        self.assertNotIn("SECRET", signal)
        self.assertFalse(access_blocked({"category": []}))

    def test_unknown_link_and_missing_usage_are_not_billing_or_zero_work(self):
        self.result(errors=[json.dumps({"pricingUrl": "https://evil.example/pricing"})])
        result = self.summarize()
        self.assertEqual(result["category"], "runtime_failure")
        self.assertFalse(result["zero_token_turn"])


if __name__ == "__main__":
    unittest.main()
