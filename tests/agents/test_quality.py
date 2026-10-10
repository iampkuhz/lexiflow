"""直接复核 Agent 角色入口、权限与共享合同加载路径。"""

from __future__ import annotations

import unittest
from pathlib import Path

import tomllib
import yaml

from scripts.checks.tool_tests.run_agent_tool_tests import evaluate_suite


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
                    "harness/agent-policy.md",
                    "harness/README.md",
                    "codex_model_policy",
                ):
                    self.assertIn(entry, role["developer_instructions"])

    def test_qoder_preserves_manual_context_and_role_tools(self) -> None:
        entry = (self.root / ".qoder/AGENTS.md").read_text()
        for context in (
            "AGENTS.md",
            "harness/README.md",
            "harness/README.md",
            "harness/agent-policy.md",
            "harness/documentation-policy.md",
            "SKILL.md",
        ):
            self.assertIn(context, entry)
        self.assertIn("不提供仓库委派、回调或证据消费链", entry)
        for retired in ("harness_manifest", "required_context", "qoder_delegation"):
            self.assertNotIn(retired, entry)
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
                    "harness/agent-policy.md",
                    "harness/documentation-policy.md",
                    "SKILL.md",
                ):
                    self.assertIn(context, body)
                for retired in (
                    "harness_manifest",
                    "required_context",
                    "qoder_delegation",
                ):
                    self.assertNotIn(retired, body)
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
        self.assertIn("harness/agent-policy.md", entry)
        self.assertIn("codex_model_policy", entry)
        self.assertIn("harness/documentation-policy.md", entry)
        self.assertNotIn("显式传递", entry)
        self.assertNotIn("升级必须", entry)
        policy = (self.root / "harness/agent-policy.md").read_text()
        self.assertIn("codex_model_policy", policy)
        self.assertIn("独立性", policy)
        self.assertNotIn("qoder_delegation", policy)
        for retired in (
            "caller_required_input",
            "runner_bound_identity",
            "result_required_output",
            "caller_field_schema",
            "qoder_model_policy",
        ):
            self.assertNotIn(retired, policy)


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


class StrictDispatchPathTest(unittest.TestCase):
    def test_public_parser_and_intersection_share_one_strict_grammar(self):
        from scripts.agents.work_packages.work_package_contract_checks import (
            dispatch_paths_intersect,
            match_dispatch_path_v1,
            parse_dispatch_path_v1,
        )

        self.assertEqual(
            parse_dispatch_path_v1("scripts/**"), ("subtree", ("scripts",))
        )
        self.assertEqual(parse_dispatch_path_v1("scripts/*"), ("child", ("scripts",)))
        self.assertEqual(parse_dispatch_path_v1("AGENTS.md"), ("exact", ("AGENTS.md",)))
        self.assertTrue(match_dispatch_path_v1("scripts/a.py", "scripts/*"))
        self.assertFalse(match_dispatch_path_v1("scripts/nested/a.py", "scripts/*"))
        self.assertTrue(dispatch_paths_intersect("scripts/**", "scripts/nested/*"))
        self.assertFalse(dispatch_paths_intersect("scripts/*", "scripts/nested/*"))
        self.assertFalse(dispatch_paths_intersect("scripts/a.py", "scripts/a.py.extra"))

    def test_unsafe_partial_or_nonterminal_patterns_are_rejected(self):
        from scripts.agents.work_packages.work_package_contract_checks import AgentContractError, parse_dispatch_path_v1

        for pattern in (
            "/etc/passwd",
            "../scripts",
            "scripts/a*.py",
            "scripts/**/x",
            "scripts//x",
            "scripts/./x",
            "scripts/latest/x",
            "scripts/[x]",
            "scripts/a,b",
            "C:/scripts",
            "scripts/?",
            "scripts/{x}",
            "scripts/a\n",
            "scripts/a\x00",
            "scripts/e\u0301.py",
        ):
            with self.subTest(pattern=pattern), self.assertRaises(AgentContractError):
                parse_dispatch_path_v1(pattern)
