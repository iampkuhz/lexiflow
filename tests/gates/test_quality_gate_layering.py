"""Regression checks for the ownership boundary between Java delivery and Python control-plane gates."""

from __future__ import annotations

from pathlib import Path
import unittest

import yaml


REPO = Path(__file__).resolve().parents[2]


class TestQualityGateLayering(unittest.TestCase):
    def test_java_rules_have_one_gradle_java_delivery_owner(self):
        manifest = yaml.safe_load((REPO / "harness/java-product.manifest.yaml").read_text())
        verification = manifest["verification"]
        self.assertEqual(verification["execution_owner"], "gradle-java")
        self.assertEqual(verification["gate_orchestration"], "receipt-only")
        self.assertEqual(
            verification["incremental_delivery"],
            "python3 scripts/toolchain/java_gradle.py check",
        )
        self.assertEqual(
            verification["full_delivery"],
            "python3 scripts/toolchain/java_gradle.py deliveryFull",
        )
        self.assertEqual(
            verification["gate_selection"],
            {
                "incremental": ["check"],
                "full": ["deliveryFull"],
                "excluded_diagnostics": ["architectureTest", "verifyProductLanguage"],
            },
        )
        self.assertEqual(
            set(verification["rule_owners"]),
            {"formatting", "style", "static_analysis", "java_source_policy", "architecture", "behavior"},
        )

    def test_control_plane_stays_fail_closed_and_review_layers_do_not_execute_delivery(self):
        registry = yaml.safe_load((REPO / "harness/gate-check-registry.yaml").read_text())
        self.assertEqual(registry["execution"]["source_scan"], "forbidden")
        self.assertEqual(registry["execution"]["independent_review"], "consumes-immutable-receipts")
        self.assertEqual(registry["execution"]["catalog_decision"], "consumes-immutable-receipts")


if __name__ == "__main__":
    unittest.main()
