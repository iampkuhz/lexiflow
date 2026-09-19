"""读取正文中的验收案例标题，兼容十进制章节编号并排除代码围栏。"""

import re

_HEADING = re.compile(r"^#{2,6}\s+(?:\d+(?:\.\d+)*\.?\s+)?(LF-[A-Z0-9-]+)\b")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")


def case_ids(text: str) -> list[str]:
    result = []
    fence = None
    for line in text.splitlines():
        marker = _FENCE.match(line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        if fence is None:
            heading = _HEADING.match(line)
            if heading:
                result.append(heading[1])
    return result
