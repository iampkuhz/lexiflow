"""原生 Agent 共享模型、工作包和独立验收策略的直接回归。"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

import tomllib


class NativeAgentPolicyTest(unittest.TestCase):
    def test_production_consumers_do_not_import_retired_executor(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for source in (root / "scripts").rglob("*.py"):
            tree = ast.parse(source.read_text(), filename=str(source))
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
                    imported.extend(
                        f"{node.module}.{alias.name}" for alias in node.names
                    )
            with self.subTest(source=source.relative_to(root)):
                self.assertFalse(
                    any(
                        name.startswith("scripts.agents.qoder")
                        or name == "scripts.agents.dispatch_fallback"
                        for name in imported
                    )
                )

    def test_human_model_choices_and_task_boundaries_are_explicit(self) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = (root / "harness/agent-policy.md").read_text()
        # 阅读规则的决策边界；不把 Markdown 解析成运行时配置。
        for required in (
            "codex_model_policy",
            "完整工作包",
            "gpt-6-luna` / `low",
            "gpt-6-luna` / `medium",
            "gpt-6-sol",
            "具体证据与父代理批准",
            "gpt-6-astra",
            "仅 human 授权",
            "不改默认路由",
            "不按角色、继承模型",
            "10 分钟内",
            "20–90 分钟",
            "10–360 分钟",
            "并行 ownership 不重叠",
            "最多返工 1 轮",
            "调用者不得注入 runner 身份",
            "先发布真实产物",
            "最多 3 项 blocking findings",
            "禁止带完整上下文或日志",
        ):
            with self.subTest(rule=required):
                self.assertIn(required, policy)
        self.assertFalse((root / "harness/agent-policy.manifest.yaml").exists())
        self.assertFalse((root / "harness/manifest.yaml").exists())

    def test_client_defaults_do_not_override_per_package_choices(self) -> None:
        root = Path(__file__).resolve().parents[2]
        config = tomllib.loads((root / ".codex/config.toml").read_text())
        self.assertEqual(config["agents"]["default_subagent_model"], "gpt-6-luna")
        self.assertEqual(
            config["agents"]["default_subagent_reasoning_effort"], "medium"
        )
        self.assertEqual(config["agents"]["max_concurrent_threads_per_session"], 6)
        self.assertNotIn("model", config)
        entry = root / ".codex" / config["model_instructions_file"]
        self.assertIn("harness/agent-policy.md", entry.read_text())
        self.assertNotRegex(entry.read_text(), "gpt-\\d+(?:\\.\\d+)?-")
        profiles = list((root / ".codex/agents").glob("*.toml"))
        self.assertGreaterEqual(len(profiles), 5)
        for profile in profiles:
            with self.subTest(profile=profile.name):
                self.assertNotIn("model", tomllib.loads(profile.read_text()))

    def test_independent_acceptance_is_a_human_rule_and_real_actor_boundary(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        policy = (root / "harness/agent-policy.md").read_text()
        for required in (
            "集成验证串行",
            "输入漂移失败关闭",
            "全部 producer/submitter",
            "reviewer 不同于 validator 与实现者",
            "review 只读冻结 diff/evidence",
            "CATALOG_DECISION 只核 receipt/hash DAG",
            "开发自检不得复用为 Formal",
            "必需检查未运行不能通过",
            "来源缺失 BLOCKED",
            "伪造或冲突 FAIL",
        ):
            with self.subTest(rule=required):
                self.assertIn(required, policy)
        spec = (root / "openspec/specs/agent-execution/spec.md").read_text()
        self.assertIn("harness/agent-policy.md", spec)
        self.assertIn("集成验证 SHALL 串行", spec)
        self.assertIn("实现者 MAY 执行静态、编译和直接测试作为自检", spec)
        self.assertIn("MUST NOT 签发 Formal validation 或 independent review", spec)
