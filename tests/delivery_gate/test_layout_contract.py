"""Acceptance 目录与单一场景入口不得因移动而失去覆盖。"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.acceptance.__main__ import main

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE = ROOT / "scripts" / "acceptance"


class AcceptanceLayoutContractTest(unittest.TestCase):
    def test_old_namespace_is_not_kept_as_compatibility(self):
        self.assertFalse((ROOT / "scripts" / "delivery_gate").exists())

    def test_only_package_main_is_a_cli_and_no_check_adapter_is_imported(self):
        sources = list(ACCEPTANCE.rglob("*.py"))
        self.assertTrue(sources)
        cli_sources = []
        for source in sources:
            tree = ast.parse(source.read_text(), filename=str(source))
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
            self.assertFalse(
                any(name == "scripts.checks" or name.startswith("scripts.checks.")
                    for name in imports),
                source,
            )
            if "argparse" in imports:
                cli_sources.append(source.relative_to(ACCEPTANCE).as_posix())
        self.assertEqual(cli_sources, ["__main__.py"])

    def test_candidate_integrity_process_uses_release_boundary_and_repo_root(self):
        from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import _node_proof

        request = {"fixture": "request"}
        expected = {"fixture": "proof"}

        def run(argv, cwd, env, timeout, *, limit, input_stream):
            self.assertEqual(argv, ["node", str(ROOT / "ops/release/integrity/candidate-source-proof.mjs")])
            self.assertTrue(Path(argv[1]).is_file())
            self.assertEqual(cwd, ROOT)
            self.assertEqual((timeout, limit), (60, 65536))
            self.assertEqual(json.loads(input_stream.read()), request)
            return json.dumps(expected).encode(), b""

        with patch("scripts.acceptance.evidence.verify_candidate_acceptance_evidence.run_bounded", side_effect=run):
            self.assertEqual(_node_proof(ROOT, request), expected)

    def test_candidate_integrity_process_error_still_fails_closed(self):
        from ops.release.host_resources.release_process_control import ProcessError
        from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import CandidateProofError, _node_proof

        with (
            patch("scripts.acceptance.evidence.verify_candidate_acceptance_evidence.run_bounded",
                  side_effect=ProcessError("FAIL", "process-cleanup-failed")),
            self.assertRaisesRegex(CandidateProofError, "candidate-integrity-check-failed"),
        ):
            _node_proof(ROOT, {})

    def test_default_cli_repository_root_survives_relocation(self):
        response = {"result": "PASS", "submission_id": "fixture"}
        with (
            patch("scripts.acceptance.task_operations.show_task_acceptance_status.query_status",
                  return_value=response) as query,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main(["status", "--submission-id", "fixture"]), 0)
        query.assert_called_once_with(str(ROOT), submission_id="fixture")
        self.assertEqual(json.loads(output.getvalue()), response)
