"""派发可见性的人类规则回归；静态文本不证明客户端渲染或真实启动。"""

import unittest
from pathlib import Path


class AgentDispatchVisibilityTests(unittest.TestCase):
    """聊天交接覆盖调用前、返回后与终态；不放宽证据和隐私边界。"""

    def test_parent_messages_cover_every_dispatch_route(self):
        root = Path(__file__).resolve().parents[2]
        policy = (root / "harness/agent-policy.md").read_text()
        for boundary in (
            "spawn 与 followup",
            "父代理在当前聊天正文",
            "不能代替",
            "调用前",
            "返回后",
            "终态后",
            "启动证据",
            "受理不是完成",
            "原生 agent 标识",
            "原生长事件等待",
            "原始日志、凭据",
            "本机私有路径或业务输入",
            "回调包含精确 run_id",
        ):
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, policy)
        self.assertNotIn("asynchronous_handoff", policy)
        self.assertNotIn("qoder_delegation", policy)
        self.assertNotIn("schema_version:", policy)


if __name__ == "__main__":
    unittest.main()
