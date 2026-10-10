"""直接复核 Agent 角色入口、权限与共享合同加载路径。"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

import yaml

from scripts.agents.module_tests import evaluate_suite


class AgentRoleEntryTest(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_codex_role_names_permissions_and_policy_entry(self) -> None:
        names = {
            "architecture-reviewer",
            "backend-implementer",
            "domain-designer",
            "extension-implementer",
            "qa-verifier",
        }
        profiles = self.root / ".codex/agents"
        self.assertEqual(names, {path.stem for path in profiles.glob("*.toml")})
        for name in names:
            with self.subTest(role=name):
                role = tomllib.loads((profiles / f"{name}.toml").read_text())
                self.assertEqual(name, role["name"])
                self.assertTrue(role["description"])
                self.assertNotIn("model", role)
                if name in {"architecture-reviewer", "qa-verifier"}:
                    self.assertEqual("read-only", role["sandbox_mode"])
                else:
                    self.assertNotIn("sandbox_mode", role)
                for entry in (
                    "AGENTS.md",
                    "harness/agent-policy.manifest.yaml",
                    "subagent_protocol",
                    "codex_dispatch",
                ):
                    self.assertIn(entry, role["developer_instructions"])

    def test_qoder_preserves_complete_context_and_role_tools(self) -> None:
        entry = (self.root / ".qoder/AGENTS.md").read_text()
        for context in (
            "AGENTS.md",
            ".qoder/AGENTS.md",
            "harness_manifest",
            "required_context",
            ".qoder/agents/<agent_profile>.md",
            "harness/agent-policy.manifest.yaml",
            "subagent_protocol",
            "qoder_delegation",
            "harness/documentation-policy.yaml",
            "SKILL.md",
        ):
            self.assertIn(context, entry)
        for name in (
            "backend-implementer",
            "extension-implementer",
            "quality-verifier",
        ):
            with self.subTest(role=name):
                text = (self.root / f".qoder/agents/{name}.md").read_text()
                _, header, body = text.split("---", 2)
                role = yaml.safe_load(header)
                self.assertEqual(name, role["name"])
                self.assertTrue(role["description"])
                expected_tools = (
                    "Read, Bash, Glob, Grep"
                    if name == "quality-verifier"
                    else "Read, Write, Edit, Bash, Glob, Grep"
                )
                self.assertEqual(expected_tools, role["tools"])
                for context in (
                    ".qoder/AGENTS.md",
                    "AGENTS.md",
                    "harness_manifest",
                    "required_context",
                    "harness/agent-policy.manifest.yaml",
                    "subagent_protocol",
                    "qoder_delegation",
                ):
                    self.assertIn(context, body)
                if name == "quality-verifier":
                    self.assertIn("只读", body)
                    self.assertIn("不修复", body)
                    self.assertIn("不重跑", body)

    def test_extension_roles_keep_only_current_thin_client_scope(self) -> None:
        for path in (
            ".codex/agents/extension-implementer.toml",
            ".qoder/agents/extension-implementer.md",
        ):
            with self.subTest(path=path):
                text = (self.root / path).read_text()
                for current_scope in (
                    "字幕",
                    "叠加层",
                    "用户显式本机私有偏好",
                    "薄客户端",
                    "已发布资料",
                    "陈旧结果拒绝",
                ):
                    self.assertIn(current_scope, text)
                for obsolete_scope in (
                    "服务端档案",
                    "服务端个人词汇档案",
                    "事件发件箱",
                    "同步或",
                ):
                    self.assertNotIn(obsolete_scope, text)

    def test_model_instructions_remain_loaded_and_point_to_live_policy(self) -> None:
        config = tomllib.loads((self.root / ".codex/config.toml").read_text())
        entry = (self.root / ".codex" / config["model_instructions_file"]).read_text()
        self.assertIn("AGENTS.md", entry)
        self.assertIn("harness/agent-policy.manifest.yaml", entry)
        self.assertIn("subagent_protocol.codex_model_policy", entry)
        self.assertIn("harness/documentation-policy.yaml", entry)
        self.assertNotIn("显式传递", entry)
        self.assertNotIn("升级必须", entry)
        policy = yaml.safe_load(
            (self.root / "harness/agent-policy.manifest.yaml").read_text()
        )
        self.assertIsInstance(policy["subagent_protocol"]["codex_model_policy"], dict)
        for section in ("agent_dispatch", "codex_dispatch", "qoder_delegation"):
            self.assertIsInstance(policy[section], dict)


class AgentQualityResultTest(unittest.TestCase):
    def test_empty_suite_cannot_pass(self) -> None:
        self.assertEqual("FAIL", evaluate_suite(unittest.TestSuite())["status"])

    def test_skipped_test_cannot_pass(self) -> None:
        class Skipped(unittest.TestCase):
            @unittest.skip("fixture")
            def test_skip(self) -> None:
                pass

        report = evaluate_suite(
            unittest.defaultTestLoader.loadTestsFromTestCase(Skipped)
        )
        self.assertEqual("FAIL", report["status"])
        self.assertEqual(1, report["skipped"])
