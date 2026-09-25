"""跨 Agent 委派的 Qoder 公开命令入口；派发与生命周期由 Qoder 模块执行。"""

from __future__ import annotations

from scripts.agents.qoder.runner import main as run_command


def main(argv: list[str] | None = None) -> int:
    """将显式动作与证据 ID 交给 Qoder runner，不推断最新运行或放宽身份合同。"""
    return run_command(argv)


if __name__ == "__main__":
    raise SystemExit(main())
