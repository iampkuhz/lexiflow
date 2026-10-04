"""检查派发可见性合同；不把静态合同检查解释为客户端渲染验收。"""

from pathlib import Path
import unittest

import yaml


class AgentDispatchVisibilityTests(unittest.TestCase):
    """保证聊天提示覆盖交接阶段，且不放宽真实启动和隐私边界。"""

    def test_parent_messages_cover_every_dispatch_route(self):
        root = Path(__file__).resolve().parents[2]
        policy = yaml.safe_load(
            (root / "harness/agent-policy.manifest.yaml").read_text()
        )
        progress = policy["execution_progress"]
        visible = progress["user_visible_dispatch"]
        self.assertEqual("parent-agent", visible["owner"])
        self.assertEqual(
            "current-conversation-assistant-message", visible["surface"]
        )
        self.assertEqual(
            {"qoder-start", "qoder-resume", "codex-spawn", "codex-followup", "fallback"},
            set(visible["applies_to"]),
        )
        for phase in (
            "before_dispatch", "after_dispatch", "asynchronous_handoff",
            "after_completion", "privacy",
        ):
            with self.subTest(phase=phase):
                self.assertTrue(visible[phase].strip())
        self.assertEqual(
            {"tool-output-only", "terminal-stdout-only", "local-log-only", "openspec-status-only"},
            set(visible["not_a_substitute"]),
        )
        self.assertIn("启动证据", visible["after_dispatch"])
        self.assertIn("尚未验收", visible["asynchronous_handoff"])
        self.assertIn("不为展示进度增加轮询", visible["asynchronous_handoff"])
        self.assertIn("started.json", progress["dispatch_evidence"])
        self.assertTrue(policy["qoder_delegation"]["lifecycle"]["llm_polling_forbidden"])


if __name__ == "__main__":
    unittest.main()
