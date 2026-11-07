"""证明同一证据遍历复用解码且不缓存来源新鲜度。"""

from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verification import freeze_inputs, verify_profiles
from scripts.verification.reports import verification_report_store as store
from tests.verification.declaration_fixtures import write_declarations


OUTPUT = (
    "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,"
    "'errors':0,'skipped':0,'reason':''}))"
)


class EvidenceTraversalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src/input.txt").write_text("original")
        checks = []
        for check_id in ("fixture.one", "fixture.two"):
            checks.append(
                {
                    "check_id": check_id,
                    "command": ["python3", "-c", OUTPUT],
                    "timeout_seconds": 5,
                    "required_environment": [],
                    "input_paths": ["src"],
                    "transaction_reuse": True,
                    "module_dependencies": [],
                    "scenarios": ["repository-baseline", "change-targeted"],
                    "triggers": [{"path": "src"}],
                }
            )
        write_declarations(self.root / "config/engineering/module-checks.yaml", checks)
        frozen = freeze_inputs(self.root, verification_scope="development-baseline")
        self.assertEqual(frozen["result"], "PASS", frozen)
        self.reports = verify_profiles(
            self.root, frozen_profiles=[frozen, copy.deepcopy(frozen)]
        )
        self.assertEqual(
            [report["result"] for report in self.reports], ["PASS", "PASS"]
        )

    def test_same_exact_source_bytes_are_decoded_once_but_reread_per_edge(self):
        source, target = self.reports
        locator = source["publication"]["locator"]
        original_read = store._read
        source_reads = 0
        source_json_decodes = 0
        source_check_validations = 0
        original_loads = store.json.loads
        original_check = store._pass_check_current

        def counted_read(root, relative):
            nonlocal source_reads
            if relative == locator:
                source_reads += 1
            return original_read(root, relative)

        def counted_loads(data, *args, **kwargs):
            nonlocal source_json_decodes
            if isinstance(data, bytes) and data.startswith(b"{"):
                source_json_decodes += 1
            return original_loads(data, *args, **kwargs)

        def counted_check(*args, **kwargs):
            nonlocal source_check_validations
            if args[4] == source["run_id"]:
                source_check_validations += 1
            return original_check(*args, **kwargs)

        with (
            patch.object(store, "_read", side_effect=counted_read),
            patch.object(store.json, "loads", side_effect=counted_loads),
            patch.object(store, "_pass_check_current", side_effect=counted_check),
        ):
            store.validate_report(self.root, target)

        self.assertEqual(source_reads, 2)
        self.assertEqual(source_json_decodes, 1)
        self.assertEqual(source_check_validations, 4)
        self.assertEqual(len(target["checks"]), 2)

    def test_input_drift_between_references_is_not_cached(self):
        source, target = self.reports
        original = store._pass_check_current
        calls = 0

        def drift_after_target_check(*args, **kwargs):
            nonlocal calls
            answer = original(*args, **kwargs)
            run_id = args[4]
            expected = args[2]
            if run_id == target["run_id"]:
                calls += 1
            if run_id == target["run_id"] and expected["check_id"] == "fixture.one":
                (self.root / "src/input.txt").write_text("changed between edges")
            return answer

        with patch.object(
            store, "_pass_check_current", side_effect=drift_after_target_check
        ):
            with self.assertRaises(ValueError):
                store.validate_report(self.root, target)
        self.assertEqual(calls, 2)

    def test_log_drift_between_references_is_not_cached(self):
        source, target = self.reports
        locator = source["publication"]["locator"]
        artifact = source["checks"][0]["process"]["output_artifacts"]["stdout"][
            "locator"
        ]
        original_read = store._read
        source_reads = 0

        def mutate_after_second_source_read(root, relative):
            nonlocal source_reads
            data = original_read(root, relative)
            if relative == locator:
                source_reads += 1
                if source_reads == 2:
                    (self.root / artifact).write_bytes(b"tampered")
            return data

        with patch.object(store, "_read", side_effect=mutate_after_second_source_read):
            with self.assertRaises(ValueError):
                store.validate_report(self.root, target)
        self.assertEqual(source_reads, 2)

    def test_environment_drift_between_references_is_not_cached(self):
        _source, target = self.reports
        original = store._pass_check_current
        changed = os.environ.get("LANG", "") + ".changed"

        def drift_after_first_target_check(*args, **kwargs):
            answer = original(*args, **kwargs)
            if args[4] == target["run_id"] and args[2]["check_id"] == "fixture.one":
                os.environ["LANG"] = changed
            return answer

        with patch.dict(os.environ, {"LANG": os.environ.get("LANG", "")}):
            with patch.object(
                store,
                "_pass_check_current",
                side_effect=drift_after_first_target_check,
            ):
                with self.assertRaises(ValueError):
                    store.validate_report(self.root, target)

    def test_traversal_context_is_reset_after_exception(self):
        with self.assertRaises(ValueError):
            store.read_report(self.root, "10000000-0000-4000-8000-000000000001")
        self.assertIsNone(store._TRAVERSAL.get())
        store.validate_report(self.root, self.reports[0])
        self.assertIsNone(store._TRAVERSAL.get())

    def test_source_edge_does_not_refreeze_already_authenticated_source(self):
        """逐边来源仍完整核验，只删除随后为取操作定义的重复冻结。"""
        from scripts.verification.execution import check_plan_runner as plans

        original = plans.freeze_inputs
        with patch.object(plans, "freeze_inputs", wraps=original) as calls:
            store.validate_report(self.root, self.reports[1])
        # 一次目标冻结，加上两条边各自完整读取来源中的冻结；不再额外加二。
        self.assertEqual(calls.call_count, 3)

    def test_decode_cache_budget_and_caller_mutation_do_not_change_content(self):
        """纯解码缓存有界且不允许调用方对象污染下一次同字节读取。"""
        data = b'{"value": [1]}'
        token = store._TRAVERSAL.set({"decoded": {}, "bytes": 0})
        try:
            first = store._decode_report(self.root, "fixture.json", data)
            first["value"].append(2)
            self.assertEqual(
                store._decode_report(self.root, "fixture.json", data), {"value": [1]}
            )
            with patch.object(store, "_MAX_DECODED_BYTES", 0):
                store._decode_report(self.root, "uncached.json", b'{"other": 1}')
            self.assertEqual(len(store._TRAVERSAL.get()["decoded"]), 1)
        finally:
            store._TRAVERSAL.reset(token)

    def test_transient_input_drift_at_second_reference_is_rejected_after_restore(self):
        """第二引用采样到的短暂漂移即使随后恢复，也不能通过。"""
        source, target = self.reports
        original = store._pass_check_current
        input_path = self.root / "src/input.txt"
        original_bytes = input_path.read_bytes()
        source_first_checks = 0

        def transient_drift(*args, **kwargs):
            nonlocal source_first_checks
            if args[4] == source["run_id"] and args[2]["check_id"] == "fixture.one":
                source_first_checks += 1
                if source_first_checks == 2:
                    input_path.write_text("transient drift")
                    try:
                        return original(*args, **kwargs)
                    finally:
                        input_path.write_bytes(original_bytes)
            return original(*args, **kwargs)

        with patch.object(store, "_pass_check_current", side_effect=transient_drift):
            with self.assertRaises(ValueError):
                store.validate_report(self.root, target)
        self.assertEqual(input_path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
