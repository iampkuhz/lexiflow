"""Client-neutral identity facade fails closed without a registered native adapter."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.agents.identity.execution_identity import (
    ExecutionIdentityError,
    discover,
    verify_proof,
)
from scripts.acceptance.evidence.acceptance_actor_identity import (
    AuthorityError,
    discover_authority,
)


class ExecutionIdentityFacadeTest(unittest.TestCase):
    def test_missing_or_unknown_native_provider_is_blocked(self):
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "", "OTHER_AGENT_THREAD": "x"}):
            with self.assertRaises(ExecutionIdentityError) as caught:
                discover(".")
        self.assertEqual(caught.exception.status, "BLOCKED")
        self.assertEqual(caught.exception.code, "runtime-adapter-unavailable")

    def test_unregistered_proof_adapter_is_blocked(self):
        with self.assertRaises(ExecutionIdentityError) as caught:
            verify_proof(".", {"schema_version": "unregistered-proof.v1"}, {})
        self.assertEqual(caught.exception.status, "BLOCKED")

    def test_non_string_proof_schema_is_a_typed_failure(self):
        for schema in ([], {}):
            with self.subTest(schema=schema):
                with self.assertRaises(ExecutionIdentityError) as caught:
                    verify_proof(".", {"schema_version": schema}, {})
                self.assertEqual(caught.exception.status, "FAIL")
                self.assertEqual(caught.exception.code, "runtime-proof-invalid")

    def test_authority_error_preserves_native_status_for_default_and_custom_errors(self):
        class ConsumerError(ValueError):
            pass

        for status in ("BLOCKED", "FAIL"):
            native_error = ExecutionIdentityError("native-test", "detail", status=status)
            with patch(
                "scripts.agents.identity.execution_identity.discover",
                side_effect=native_error,
            ):
                with self.assertRaises(AuthorityError) as caught:
                    discover_authority(Path("."))
            self.assertEqual(caught.exception.status, status)
            with patch(
                "scripts.agents.identity.execution_identity.discover",
                side_effect=native_error,
            ):
                with self.assertRaises(ConsumerError) as caught:
                    discover_authority(Path("."), ConsumerError)
            self.assertEqual(caught.exception.status, status)


if __name__ == "__main__":
    unittest.main()
