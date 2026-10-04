"""开发验证入口按实际风险冻结，不能用发行缺口或调用者等级绕过。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

import yaml

from scripts.verification.development import (
    prepare_development,
    execute_development,
    main,
)
from tests.delivery_gate.fixtures import DeliveryGateFixture


class DevelopmentTest(unittest.TestCase):
    def setUp(self):
        self.f = DeliveryGateFixture()
        self.addCleanup(self.f.cleanup)

    def test_unknown_engineering_diff_requires_two_views_without_formal_runtime(self):
        self.f.write("src/test.py", "source subject\n")
        prepared = prepare_development(self.f.root)
        self.assertEqual("high-risk-engineering", prepared["risk_assessment"]["level"])
        self.assertEqual(
            ["development-change", "development-baseline"],
            [item["verification_scope"] for item in prepared["frozen_profiles"]],
        )
        actual = execute_development(self.f.root, prepared=prepared)
        self.assertEqual("PASS", actual["result"], actual)
        self.assertEqual(2, len(actual["reports"]))
        self.assertFalse(actual["formal_receipt_issued"])
        for report in actual["reports"]:
            self.assertNotEqual("repository-baseline", report["scope"])
            self.assertFalse(
                any("release" in check["check_id"] for check in report["checks"])
            )

    def test_plain_user_document_is_single_view_and_unrelated_write_is_allowed(self):
        path = self.f.root / "harness/module-checks.yaml"
        declarations = yaml.safe_load(path.read_text())
        for check in declarations["checks"]:
            if check["check_id"] in {"fixture.change", "fixture.baseline"}:
                check["triggers"].append({"path": "docs/user/"})
                check["input_paths"].append("docs/user")
        path.write_text(yaml.safe_dump(declarations))
        self.f.write("docs/user/help.md", "原说明。\n")
        self.f._git("add", ".")
        self.f._git("commit", "-m", "user docs fixture")
        self.f.write("docs/user/help.md", "普通说明。\n")
        prepared = prepare_development(self.f.root)
        self.assertEqual("local-function", prepared["risk_assessment"]["level"])
        self.assertEqual(1, len(prepared["frozen_profiles"]))
        self.f.write("unrelated.txt", "unrelated\n")
        actual = execute_development(self.f.root, prepared=prepared)
        self.assertEqual("PASS", actual["result"], actual)
        self.f.write("docs/user/dependency.md", "新增的依赖。\n")
        self.assertEqual(
            "FAIL", execute_development(self.f.root, prepared=prepared)["result"]
        )

    def test_cli_has_no_scope_or_risk_override_and_clean_is_not_pass(self):
        for args in (
            ["--risk", "mechanical"],
            ["--root", "/tmp"],
            ["--check-id", "fixture.change"],
        ):
            with (
                self.subTest(args=args),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                main(args, root=self.f.root)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([], root=self.f.root)
        self.assertEqual(2, code)
        self.assertEqual("BLOCKED", json.loads(output.getvalue())["result"])

    def test_risk_or_scope_tampering_fails_before_execution(self):
        self.f.write("src/test.py", "source\n")
        prepared = prepare_development(self.f.root)
        prepared["risk_assessment"]["level"] = "mechanical"
        with patch("scripts.verification.verify_profiles") as execute:
            result = execute_development(self.f.root, prepared=prepared)
        self.assertEqual("FAIL", result["result"])
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
