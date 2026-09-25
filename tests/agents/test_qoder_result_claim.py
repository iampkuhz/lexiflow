"""Ruff 检出的未绑定 callback claim helper 的行为回归。"""

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.agents.qoder import runner as qoder_task
from scripts.agents.qoder.callback import _callback_claim_path


class ResultClaimTest(unittest.TestCase):
    def test_terminal_result_without_receipt_reports_existing_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id = "00000000-0000-4000-8000-000000000001"
            run = root / "tmp/qoder-tasks" / run_id
            run.mkdir(parents=True)
            (root / ".git").mkdir()
            (run / "completion.json").write_text('{"status":"failed"}')
            _callback_claim_path(run).write_text("{}")
            with (
                mock.patch.object(Path, "cwd", return_value=root),
                mock.patch.object(qoder_task, "_reject_parent_polling"),
                mock.patch("builtins.print") as output,
            ):
                qoder_task.cmd_result(argparse.Namespace(run_id=run_id))
            report = json.loads(output.call_args.args[0])
            self.assertEqual("unknown", report["callback"]["status"])
            self.assertFalse((run / "callback.json").exists())


if __name__ == "__main__":
    unittest.main()
