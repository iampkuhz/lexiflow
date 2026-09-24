"""python3 -m scripts.repository 的 CLI 转接，不增加独立 Gate 阶段。"""

import sys
from scripts.repository.planning_check import main

if __name__ == "__main__":
    raise SystemExit(main())
