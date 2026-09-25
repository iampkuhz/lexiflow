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
            "python3 -m scripts.environment.java_exec backend/gradlew -p backend check",
        )
        self.assertEqual(
            verification["full_delivery"],
            "python3 -m scripts.environment.java_exec backend/gradlew -p backend deliveryFull",
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
            {"formatting", "style", "static_analysis", "java_source_policy", "architecture", "behavior", "public_api_javadoc_contract"},
        )

    def test_control_plane_layering_principle_retired_with_registry(self):
        """gate-check-registry.yaml was retired; layering is now enforced by
        scripts.delivery_gate module architecture (submit/validate/review/check)."""
        self.assertFalse(
            (REPO / "harness" / "gate-check-registry.yaml").exists(),
            "gate-check-registry.yaml must remain retired",
        )


if __name__ == "__main__":
    unittest.main()
