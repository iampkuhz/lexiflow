"""验证 YAML 解析缓存的内容键、容量与对象隔离。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.verification import yaml_input
from scripts.verification.risk import _risk_policy


class YamlInputTests(unittest.TestCase):
    def setUp(self) -> None:
        yaml_input._parse_cached.cache_clear()

    def test_same_content_parses_once_and_returns_independent_alias_cycle_trees(self):
        raw = b"root: &node\n  value: [1]\nleft: *node\nright: *node\nself: &loop [*loop]\n"
        original = yaml.safe_load
        with patch.object(yaml_input.yaml, "safe_load", wraps=original) as loader:
            first = yaml_input.parse_yaml_input(raw)
            second = yaml_input.parse_yaml_input(raw)
        self.assertEqual(loader.call_count, 1)
        self.assertIs(first["left"], first["right"])
        self.assertIs(first["self"], first["self"][0])
        self.assertIsNot(first["root"], second["root"])
        self.assertIs(second["root"], second["left"])
        first["root"]["value"].append(2)
        self.assertEqual(second["root"]["value"], [1])
        self.assertEqual(yaml_input.parse_yaml_input(raw)["root"]["value"], [1])

    def test_content_and_input_type_are_distinct_keys(self):
        with patch.object(yaml_input.yaml, "safe_load", wraps=yaml.safe_load) as loader:
            self.assertEqual(yaml_input.parse_yaml_input(b"x: 1\n"), {"x": 1})
            self.assertEqual(yaml_input.parse_yaml_input("x: 1\n"), {"x": 1})
            self.assertEqual(yaml_input.parse_yaml_input(b"x: 2\n"), {"x": 2})
        self.assertEqual(loader.call_count, 3)

    def test_lru_evicts_after_eight_entries(self):
        original = yaml.safe_load
        with patch.object(yaml_input.yaml, "safe_load", wraps=original) as loader:
            for i in range(8):
                yaml_input.parse_yaml_input(f"x: {i}\n")
            yaml_input.parse_yaml_input("x: 0\n")
            yaml_input.parse_yaml_input("x: 8\n")
            yaml_input.parse_yaml_input("x: 1\n")
        self.assertEqual(loader.call_count, 10)

    def test_large_bytes_and_strings_are_not_cached(self):
        data = b"x: " + b"a" * (128 * 1024) + b"\n"
        text = data.decode()
        with patch.object(yaml_input.yaml, "safe_load", wraps=yaml.safe_load) as loader:
            yaml_input.parse_yaml_input(data)
            yaml_input.parse_yaml_input(data)
            yaml_input.parse_yaml_input(text)
            yaml_input.parse_yaml_input(text)
        self.assertEqual(loader.call_count, 4)

    def test_errors_and_unsafe_tags_are_rejected_each_time(self):
        with patch.object(yaml_input.yaml, "safe_load", wraps=yaml.safe_load) as loader:
            for value in (b"[", b"[", b"!!python/object/apply:os.system ['id']", b"!!python/object/apply:os.system ['id']"):
                with self.assertRaises(yaml.YAMLError):
                    yaml_input.parse_yaml_input(value)
        self.assertEqual(loader.call_count, 4)

    def test_multibyte_size_limit_and_surrogate_errors_keep_loader_semantics(self):
        raw = "x: " + "中" * (64 * 1024)
        with patch.object(yaml_input.yaml, "safe_load", wraps=yaml.safe_load) as loader:
            yaml_input.parse_yaml_input(raw)
            yaml_input.parse_yaml_input(raw)
        self.assertEqual(loader.call_count, 2)
        invalid = "x: \ud800"
        with self.assertRaises(yaml.YAMLError) as expected:
            yaml.safe_load(invalid)
        for _ in range(2):
            with self.assertRaises(type(expected.exception)):
                yaml_input.parse_yaml_input(invalid)

    def test_cached_syntax_does_not_skip_consumer_semantic_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            path = root / "harness" / "agent-policy.manifest.yaml"
            path.write_text("risk_classification: {schema_version: invalid}\n")
            with patch.object(yaml_input.yaml, "safe_load", wraps=yaml.safe_load) as loader:
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, "risk-assessment-policy-invalid"):
                        _risk_policy(root)
            self.assertEqual(loader.call_count, 1)

    def test_consumer_reads_changed_policy_bytes_from_same_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "harness").mkdir()
            path = root / "harness" / "agent-policy.manifest.yaml"
            base = "schema_version: lexiflow.risk-classification.v1\nlevels: [mechanical, local-function, high-risk-engineering, formal-release]\nhigh_risk_paths: []\nlocal_owner_roots: {}\nsensitive_content_patterns: []\nprivate_paths: []\n"
            path.write_text("risk_classification:\n  " + base.replace("\n", "\n  "), encoding="utf-8")
            first = _risk_policy(root)
            path.write_text("risk_classification:\n  " + base.replace("\n", "\n  ").replace("high_risk_paths: []", "high_risk_paths: [scripts/**]"), encoding="utf-8")
            second = _risk_policy(root)
        self.assertEqual(first["high_risk_paths"], [])
        self.assertEqual(second["high_risk_paths"], ["scripts/**"])


if __name__ == "__main__":
    unittest.main()
