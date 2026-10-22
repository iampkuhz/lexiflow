"""Docker 分组适配器的选择与 fail-closed 回归。"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.checks.deployment import run_deployment_contract_tests as adapter


def _synthetic_case(outcome="success"):
    def run_test(self):
        if outcome == "failure":
            self.fail("synthetic failure")
        if outcome == "skip":
            self.skipTest("synthetic skip")

    return type("SyntheticCase", (unittest.TestCase,), {"runTest": run_test})()


class _FlushStream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flushed = []

    def flush(self):
        self.flushed.append(self.getvalue())
        super().flush()


class DockerGroupContractTest(unittest.TestCase):
    def setUp(self):
        self.root = adapter._repo_root()
        self.declarations = adapter._declarations(self.root)

    def test_manifest_members_are_exact_partition_and_complete_inventory(self):
        groups = adapter._members(self.declarations)
        flat = [name for members in groups.values() for name in members]
        py, node, _ = adapter._inventory(self.root)
        self.assertEqual(set(flat), py | node)
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(
            {"surface": 3, "shell": 6, "local": 6, "node": 22},
            {k: len(v) for k, v in groups.items()},
        )
        adapter._preflight(self.root, self.declarations, groups)

    def test_non_surface_inventory_reads_only_test_names(self):
        py_all, node_all, source_all = adapter._inventory(self.root)
        py_only, node_only, source_only = adapter._inventory(
            self.root, include_sources=False
        )
        self.assertEqual((py_only, node_only), (py_all, node_all))
        self.assertTrue(source_all)
        self.assertEqual(set(), source_only)

    def test_unknown_missing_duplicate_and_overlap_members_fail_closed(self):
        adapter._members(self.declarations)
        declarations = {key: dict(value) for key, value in self.declarations.items()}
        surface_id = adapter._GROUP_IDS["surface"]
        original = declarations[surface_id]["input_paths"]
        declarations[surface_id]["input_paths"] = list(original) + [
            "ops/docker/tests/test_unknown.py"
        ]
        with self.assertRaisesRegex(ValueError, "test inventory mismatch"):
            adapter._preflight(self.root, declarations, adapter._members(declarations))
        declarations[surface_id]["input_paths"] = list(original) + [
            "ops/docker/tests/test_contract.py"
        ]
        with self.assertRaisesRegex(ValueError, "duplicate or empty"):
            adapter._members(declarations)
        declarations[surface_id]["input_paths"] = [
            x for x in original if x != "ops/docker/tests/test_contract.py"
        ]
        with self.assertRaisesRegex(ValueError, "test inventory mismatch"):
            adapter._preflight(self.root, declarations, adapter._members(declarations))
        declarations[surface_id]["input_paths"] = original
        node_id = adapter._GROUP_IDS["node"]
        declarations[node_id]["input_paths"] += ["ops/docker/tests/test_contract.py"]
        with self.assertRaisesRegex(ValueError, "overlap"):
            adapter._members(declarations)

    def test_surface_inventory_detects_unknown_or_deleted_sources(self):
        groups = adapter._members(self.declarations)
        clone = {key: dict(value) for key, value in self.declarations.items()}
        sid = adapter._GROUP_IDS["surface"]
        clone[sid]["input_paths"] = list(clone[sid]["input_paths"]) + [
            "ops/docker/new-unknown.sh"
        ]
        with self.assertRaisesRegex(ValueError, "surface source inventory mismatch"):
            adapter._preflight(self.root, clone, groups)
        clone[sid]["input_paths"] = [
            path
            for path in self.declarations[sid]["input_paths"]
            if path != "ops/docker"
        ]
        with self.assertRaisesRegex(ValueError, "surface source inventory mismatch"):
            adapter._preflight(self.root, clone, groups)

    def test_directory_source_membership_changes_are_frozen_and_stale_files_rejected(
        self,
    ):
        from scripts.verification.planning.source_file_snapshot import snapshot_inputs

        source = self.root / "ops/docker/zz-docker-group-source-audit.tmp"
        self.assertFalse(source.exists())
        groups = adapter._members(self.declarations)
        clone = {key: dict(value) for key, value in self.declarations.items()}
        sid = adapter._GROUP_IDS["surface"]
        clone[sid]["input_paths"] = [
            *clone[sid]["input_paths"],
            source.relative_to(self.root).as_posix(),
        ]
        before, missing = snapshot_inputs(self.root, self.declarations[sid])
        self.assertEqual(missing, [])
        try:
            source.write_text("synthetic source\n")
            after, missing = snapshot_inputs(self.root, self.declarations[sid])
            self.assertEqual(missing, [])
            self.assertNotEqual(before, after)
            self.assertIn(
                source.relative_to(self.root).as_posix(),
                {item["locator"] for item in after},
            )
            adapter._preflight(self.root, self.declarations, groups, "surface")
            adapter._preflight(self.root, clone, groups, "surface")
        finally:
            source.unlink(missing_ok=True)
        restored, missing = snapshot_inputs(self.root, self.declarations[sid])
        self.assertEqual((restored, missing), (before, []))
        with self.assertRaisesRegex(ValueError, "surface source inventory mismatch"):
            adapter._preflight(self.root, clone, groups, "surface")

    def test_real_filesystem_new_and_deleted_test_fail_closed(self):
        member = self.root / "ops/docker/tests/test_zz_group_inventory.py"
        self.assertFalse(member.exists())
        groups = adapter._members(self.declarations)
        clone = {key: dict(value) for key, value in self.declarations.items()}
        sid = adapter._GROUP_IDS["surface"]
        clone[sid]["input_paths"] = [
            *clone[sid]["input_paths"],
            member.relative_to(self.root).as_posix(),
        ]
        try:
            member.write_text(
                "import unittest\nclass Probe(unittest.TestCase):\n def test_ok(self): pass\n"
            )
            with self.assertRaisesRegex(ValueError, "test inventory mismatch"):
                adapter._preflight(self.root, self.declarations, groups, "surface")
            adapter._preflight(self.root, clone, adapter._members(clone), "surface")
        finally:
            member.unlink(missing_ok=True)
        with self.assertRaisesRegex(ValueError, "test inventory mismatch"):
            adapter._preflight(self.root, clone, adapter._members(clone), "surface")

    def test_source_import_and_copy_gaps_fail_consuming_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            member = root / "ops/podman/tests/probe.test.mjs"
            source = root / "ops/release/new.mjs"
            member.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            source.write_text("export const x = 1;\n")
            member.write_text("import {x} from '../../release/new.mjs';\n")
            check = {
                "input_paths": ["ops/podman/tests/probe.test.mjs"],
                "triggers": [{"path": "ops/podman/tests/probe.test.mjs"}],
            }
            declarations = {adapter._GROUP_IDS["node"]: check}
            groups = {"node": ["ops/podman/tests/probe.test.mjs"]}
            with self.assertRaisesRegex(ValueError, "source consumption gap: node"):
                adapter._audit_consumption(root, declarations, groups, ("node",))
            check["input_paths"].append("ops/release/new.mjs")
            with self.assertRaisesRegex(ValueError, "source consumption gap: node"):
                adapter._audit_consumption(root, declarations, groups, ("node",))
            check["triggers"].append({"path": "ops/release/new.mjs"})
            adapter._audit_consumption(root, declarations, groups, ("node",))
            member.write_text(
                "const target = process.env.TARGET; await import(target);\n"
            )
            with self.assertRaisesRegex(ValueError, "unresolved dynamic source edge"):
                adapter._audit_consumption(root, declarations, groups, ("node",))
            check["input_paths"].append("ops/podman")
            check["triggers"].append({"path": "ops/podman/"})
            with self.assertRaisesRegex(ValueError, "unresolved dynamic source edge"):
                adapter._audit_consumption(root, declarations, groups, ("node",))
            docker_source = root / "ops/docker/base-images.json"
            docker_source.parent.mkdir(parents=True)
            docker_source.write_text("{}\n")
            member.write_text(
                "for (const name of ['base-images.json']) fs.copyFileSync(path.join(root, 'ops/docker', name), target);\n"
            )
            with self.assertRaisesRegex(ValueError, "source consumption gap: node"):
                adapter._audit_consumption(root, declarations, groups, ("node",))
            py = root / "ops/docker/tests/test_probe.py"
            py.parent.mkdir(parents=True)
            py.write_text("SOURCE = ROOT / 'ops/release/new.mjs'\n")
            declarations[adapter._GROUP_IDS["local"]] = {
                "input_paths": ["ops/docker/tests/test_probe.py"],
                "triggers": [{"path": "ops/docker/tests/test_probe.py"}],
            }
            groups["local"] = ["ops/docker/tests/test_probe.py"]
            with self.assertRaisesRegex(ValueError, "source consumption gap: local"):
                adapter._audit_consumption(root, declarations, groups, ("local",))

    def test_only_selected_python_group_is_loaded_and_node_is_not_claimed(self):
        suite = unittest.TestSuite([_synthetic_case()])
        stderr, stdout = io.StringIO(), io.StringIO()
        with (
            patch.object(adapter, "_load_python", return_value=suite) as loader,
            patch.object(
                adapter, "_node_run", side_effect=AssertionError("node must not run")
            ),
            patch.object(adapter, "_preflight"),
            patch.object(adapter.time, "monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6]),
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(stdout),
        ):
            report = adapter._run("surface")
        self.assertEqual("PASS", report["status"])
        self.assertIsNone(report["node_transaction_exit_code"])
        self.assertIsNone(report["node_transaction_no_skips"])
        self.assertEqual(1, report["checks_run"])
        self.assertEqual(
            hashlib.sha256(stderr.getvalue().encode()).hexdigest(),
            report["tool_output_sha256"],
        )
        loader.assert_called_once_with(
            adapter._repo_root(), adapter._members(self.declarations)["surface"]
        )

    def test_loader_imports_only_explicitly_selected_python_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "ops/docker/tests"
            tests.mkdir(parents=True)
            (tests / "test_selected.py").write_text(
                "import unittest\nclass Selected(unittest.TestCase):\n def test_ok(self): pass\n"
            )
            (tests / "test_unselected.py").write_text(
                "raise RuntimeError('unselected module imported')\n"
            )
            suite = adapter._load_python(root, ["ops/docker/tests/test_selected.py"])
            if str(tests) in sys.path:
                sys.path.remove(str(tests))
        self.assertEqual(1, suite.countTestCases())

    def test_loader_rejects_imported_or_duplicate_case_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "ops/docker/tests"
            tests.mkdir(parents=True)
            (tests / "test_selected.py").write_text(
                "import unittest\nclass Selected(unittest.TestCase):\n def test_ok(self): pass\n"
            )
            (tests / "test_other.py").write_text("from test_selected import Selected\n")
            try:
                with self.assertRaisesRegex(
                    ValueError, "selected test identity mismatch"
                ):
                    adapter._load_python(root, ["ops/docker/tests/test_other.py"])
            finally:
                if str(tests) in sys.path:
                    sys.path.remove(str(tests))

    def test_loader_rejects_omitted_case_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tests = root / "ops/docker/tests"
            tests.mkdir(parents=True)
            (tests / "test_omission.py").write_text(
                "import unittest\nclass Cases(unittest.TestCase):\n"
                " def test_one(self): pass\n def test_two(self): pass\n"
                "def load_tests(loader, suite, pattern):\n return loader.loadTestsFromName('Cases.test_one', __import__(__name__))\n"
            )
            with self.assertRaisesRegex(ValueError, "selected test identity mismatch"):
                adapter._load_python(root, ["ops/docker/tests/test_omission.py"])
            if str(tests) in sys.path:
                sys.path.remove(str(tests))

    def test_current_expanded_identity_counts_are_not_synthetic(self):
        groups = adapter._members(self.declarations)
        for name in ("surface", "shell", "local"):
            paths = [path for path in groups[name] if adapter._is_python_test(path)]
            suite = adapter._load_python(self.root, paths)
            identities = [case.id() for case in adapter._iter_cases(suite)]
            self.assertEqual(len(identities), len(set(identities)), name)
            self.assertFalse(
                any("SyntheticCase" in identity for identity in identities), name
            )
        # 包含真实安装对照、来源模板隔离及终端关闭/真实超时的回归用例。
        self.assertEqual(
            47,
            adapter._load_python(
                self.root, ["ops/docker/tests/test_podman_local.py"]
            ).countTestCases(),
        )
        self.assertEqual(
            13,
            adapter._load_python(
                self.root, ["ops/docker/tests/test_podman_fake_transport.py"]
            ).countTestCases(),
        )

    def test_unknown_group_is_rejected_by_cli_and_stdout_is_single_json(self):
        stdout = io.StringIO()
        with (
            patch(
                "sys.argv", ["run_deployment_contract_tests.py", "--group", "arbitrary"]
            ),
            contextlib.redirect_stdout(stdout),
            self.assertRaises(SystemExit),
        ):
            adapter.main()
        self.assertEqual("", stdout.getvalue())

    def test_node_only_positive_and_invalid_node_reports(self):
        good = {
            "status": "PASS",
            "checks_run": 130,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
        }
        cases = [
            (good, "PASS"),
            ({**good, "checks_run": 0}, "FAIL"),
            ({**good, "skipped": 1}, "FAIL"),
            ({**good, "errors": 1}, "FAIL"),
            ({**good, "failures": 1}, "FAIL"),
            ({**good, "checks_run": "130"}, "FAIL"),
            ({**good, "checks_run": -1}, "FAIL"),
            ({"status": "PASS", "checks_run": 130}, "FAIL"),
            (["not-a-report"], "FAIL"),
        ]
        for node_report, expected in cases:
            with (
                self.subTest(node_report=node_report),
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(adapter, "_preflight"),
                patch.object(
                    adapter,
                    "_load_python",
                    side_effect=AssertionError("no Python suite"),
                ),
                (
                    patch.object(
                        adapter,
                        "_node_run",
                        return_value=(
                            subprocess.CompletedProcess(
                                [], 0, json.dumps(node_report), ""
                            ),
                            0.1,
                            node_report,
                        ),
                    )
                ),
            ):
                report = adapter._run("node")
            self.assertEqual(expected, report["status"])
            self.assertEqual(0, report["python_unittest_duration_seconds"])
            self.assertNotIn("per_test_timing", report)

    def test_python_only_does_not_claim_node_pass_and_rejects_empty(self):
        for suite, expected in (
            (unittest.TestSuite([_synthetic_case()]), "PASS"),
            (unittest.TestSuite(), "FAIL"),
        ):
            with (
                self.subTest(expected=expected),
                contextlib.redirect_stderr(io.StringIO()),
                patch.object(adapter, "_preflight"),
                patch.object(adapter, "_load_python", return_value=suite),
                (
                    patch.object(
                        adapter,
                        "_node_run",
                        side_effect=AssertionError("node must not run"),
                    )
                ),
            ):
                report = adapter._run("shell")
            self.assertEqual(expected, report["status"])
            self.assertIsNone(report["node_transaction_no_skips"])
            self.assertIsNone(report["node_transaction_exit_code"])


class ModuleCheckAdapterTimingTest(unittest.TestCase):
    def _run(
        self, outcome="success", *, node_returncode=0, node_report=None, main=False
    ):
        suite = unittest.TestSuite([_synthetic_case(outcome)])
        if node_report is None:
            node_report = {
                "status": "PASS",
                "checks_run": 2,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
            }
        completed = subprocess.CompletedProcess(
            ["node"], node_returncode, json.dumps(node_report), ""
        )
        stderr, stdout = _FlushStream(), io.StringIO()

        def run_node(*args, **kwargs):
            self.assertEqual(stderr.flushed[-1], stderr.getvalue())
            self.assertEqual(
                [event["event"] for event in self._events(stderr.flushed[-1])],
                [
                    "python_started",
                    "test_completed",
                    "python_completed",
                    "node_started",
                ],
            )
            return completed, 2, node_report

        with (
            patch.object(adapter, "_preflight"),
            patch.object(adapter, "_load_python", return_value=suite),
            patch.object(adapter, "_node_run", side_effect=run_node),
            patch.object(
                adapter,
                "time",
                SimpleNamespace(monotonic=Mock(side_effect=[0, 1, 2, 3.2345678, 4, 8])),
            ),
            contextlib.redirect_stderr(stderr),
            contextlib.redirect_stdout(stdout),
        ):
            if main:
                with patch("sys.argv", ["run_deployment_contract_tests.py"]):
                    self.assertEqual(adapter.main(), 0)
                self.assertEqual(stdout.getvalue().count("\n"), 1)
                report = json.loads(stdout.getvalue())
            else:
                report = adapter.run_tests()
                self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            report["tool_output_sha256"],
            hashlib.sha256(stderr.getvalue().encode()).hexdigest(),
        )
        self.assertEqual(stderr.getvalue().count(completed.stdout), 1)
        return report, stderr.getvalue()

    @staticmethod
    def _events(stderr):
        return [
            json.loads(line[len(adapter._TIMING_PREFIX) :])
            for line in stderr.splitlines()
            if line.startswith(adapter._TIMING_PREFIX)
        ]

    def test_completed_case_is_flushed_and_matches_final_timing(self):
        report, stderr = self._run()
        events = self._events(stderr)
        case_event = next(item for item in events if item["event"] == "test_completed")
        self.assertEqual(
            case_event["test"], "test_module_check_adapter.SyntheticCase.runTest"
        )
        self.assertEqual(case_event["seconds"], 1.234568)
        self.assertEqual(
            report["per_test_timing"][case_event["test"]], case_event["seconds"]
        )
        self.assertEqual(
            report["top_slow_tests"],
            [{"test": case_event["test"], "seconds": case_event["seconds"]}],
        )
        self.assertEqual(report["python_unittest_duration_seconds"], 3)
        self.assertEqual(report["node_suite_duration_seconds"], 2)
        self.assertEqual(report["total_duration_seconds"], 8)
        self.assertEqual(
            [
                item["event"]
                for item in events
                if item["event"].startswith(("python_", "node_"))
            ],
            ["python_started", "python_completed", "node_started", "node_completed"],
        )

    def test_node_timeout_keeps_completed_case_and_has_no_node_completion(self):
        suite = unittest.TestSuite([_synthetic_case()])
        stderr = io.StringIO()
        with (
            patch.object(adapter, "_preflight"),
            patch.object(adapter, "_load_python", return_value=suite),
            patch.object(
                adapter, "_node_run", side_effect=subprocess.TimeoutExpired("node", 180)
            ),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            adapter.run_tests()
        events = self._events(stderr.getvalue())
        self.assertTrue(any(item["event"] == "test_completed" for item in events))
        self.assertTrue(any(item["event"] == "node_started" for item in events))
        self.assertFalse(any(item["event"] == "node_completed" for item in events))

    def test_existing_success_failure_skip_and_node_failure_semantics(self):
        success, _ = self._run()
        failure, _ = self._run("failure")
        skipped, _ = self._run("skip")
        node_failure, _ = self._run(
            node_returncode=1, node_report={"status": "FAIL", "checks_run": 0}
        )
        self.assertEqual(success["status"], "PASS")
        self.assertEqual(success["checks_run"], 3)
        self.assertEqual(failure["status"], "FAIL")
        self.assertEqual(failure["failures"], 1)
        self.assertEqual(skipped["status"], "FAIL")
        self.assertEqual(skipped["skipped"], 1)
        self.assertEqual(node_failure["status"], "FAIL")
        self.assertEqual(node_failure["node_transaction_exit_code"], 1)

    def test_main_stdout_remains_only_final_json(self):
        report, stderr = self._run(main=True)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["checks_run"], 3)
        self.assertEqual(len(self._events(stderr)), 5)


if __name__ == "__main__":
    unittest.main()
