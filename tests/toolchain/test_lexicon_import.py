"""Unit tests for the Python-only Java importer launcher."""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.toolchain import lexicon_import


class LexiconImportLauncherTest(unittest.TestCase):
    def test_publish_forwards_explicit_jdbc_arguments_to_java(self) -> None:
        with patch("scripts.toolchain.lexicon_import.subprocess.run") as run:
            run.return_value.returncode = 0
            outcome = lexicon_import.main(
                [
                    "publish",
                    "--input",
                    "fixture.csv",
                    "--database-url",
                    "postgresql://postgres@127.0.0.1:15432/lexiflow",
                    "--batch-source-id",
                    "fixture",
                    "--batch-license-id",
                    "MIT",
                ]
            )

        self.assertEqual(0, outcome)
        command = run.call_args.args[0]
        self.assertEqual(":platform:adapters:lexiconImport", command[2])
        forwarded = command[3].split("=", 1)[1].split("\x1f")
        self.assertEqual("publish", forwarded[0])
        self.assertIn("jdbc:postgresql://127.0.0.1:15432/lexiflow?user=postgres", forwarded)
        self.assertIn(str((Path.cwd() / "fixture.csv").resolve()), forwarded)

    def test_rejects_embedded_database_password(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            lexicon_import.main(
                [
                    "publish",
                    "--input",
                    "fixture.csv",
                    "--database-url",
                    "postgresql://postgres:secret@127.0.0.1:15432/lexiflow",
                    "--batch-source-id",
                    "fixture",
                    "--batch-license-id",
                    "MIT",
                ]
            )

    def test_validate_rejects_database_options(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            lexicon_import.main(
                [
                    "validate",
                    "--input",
                    "fixture.csv",
                    "--database-url",
                    "postgresql://postgres@127.0.0.1:15432/lexiflow",
                ]
            )


if __name__ == "__main__":
    unittest.main()
