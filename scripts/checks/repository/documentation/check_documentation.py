"""检查文档链接、锚点、内嵌图源与规则入口；不声称完成语义或视觉审查。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit


LINK = re.compile(r'!?\[[^\]\n]*\]\(([^\s]+?)(?:\s+"[^"]*")?\)')
REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?", re.MULTILINE)
REFERENCE = re.compile(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
HTML_LINK = re.compile(
    r'<(?:a|img)\b[^>]*?\b(?:href|src)=["\']([^"\']+)["\']', re.IGNORECASE
)


def slug(text):
    """按文档锚点规则将标题归一化为可链接标识。"""
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").lower()
    text = re.sub(r"[^\w\-\s]", "", text)
    return re.sub(r"\s", "-", text.strip())


def parse(text):
    """解析 Markdown 的标题、链接与围栏信息以供静态检查。"""
    outside, diagrams, errors = [], [], []
    fence = None
    body = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if match:
            token, info = match.groups()
            if fence is None:
                fence = (token, info.strip(), number)
                body = []
            elif (
                token[0] == fence[0][0]
                and len(token) >= len(fence[0])
                and not info.strip()
            ):
                if fence[1] == "plantuml":
                    diagrams.append((fence[2], "\n".join(body) + "\n"))
                fence = None
            else:
                body.append(line)
        elif fence:
            body.append(line)
        else:
            outside.append((number, line))
    if fence:
        errors.append(f"第 {fence[2]} 行代码围栏未闭合")
    return outside, diagrams, errors


def anchors(text):
    """收集文档内可引用锚点并识别重复或失效定义。"""
    return anchors_from_parsed(parse(text))


class DocIndex:
    """仅本轮存活的文档解析与锚点索引，目标文档按需读取一次。"""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.documents = {}
        self.anchor_index = {}

    def document(self, file):
        """返回当前文档解析，不跨检查事务缓存。"""
        key = file.resolve()
        if key not in self.documents:
            self.documents[key] = parse(file.read_text())
        return self.documents[key]

    def anchors_for(self, file):
        """使用同一解析索引计算跨文件目标锚点。"""
        key = file.resolve()
        if key not in self.anchor_index:
            self.anchor_index[key] = anchors_from_parsed(self.document(file))
        return self.anchor_index[key]


def check_file(index, file):
    """检查单个 Markdown 文件的链接、标题与图源围栏。"""
    root = index.root
    issues = []
    outside, diagrams, errors = index.document(file)
    issues.extend(errors)
    prose = "\n".join(line for _, line in outside)
    prose = re.sub(r"(`+).*?\1", "", prose)
    explicit = re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', prose)
    if len(explicit) != len(set(explicit)):
        issues.append("显式锚点重复")
    definitions = {
        x.casefold() for x in re.findall(r"^\s*\[([^\]]+)\]:", prose, re.MULTILINE)
    }
    for label, reference in REFERENCE.findall(prose):
        if (reference or label).casefold() not in definitions:
            issues.append(f"未定义的引用链接：{reference or label}")
    targets = (
        [m[1] for m in LINK.finditer(prose)]
        + REF_DEF.findall(prose)
        + HTML_LINK.findall(prose)
    )
    for target in targets:
        target = target.strip("<>")
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        path = unquote(parsed.path)
        if any(x in path for x in ["<", ">", "*"]):
            issues.append(f"示例路径不能作为链接：{target}")
            continue
        dest = (file.parent / path).resolve() if path else file.resolve()
        if not dest.is_relative_to(root.resolve()):
            issues.append(f"仓库外本地链接：{target}")
            continue
        if not dest.exists():
            issues.append(f"失效链接：{target}")
            continue
        relative = dest.relative_to(root.resolve())
        if relative.parts and relative.parts[0] in {"tmp", ".local"}:
            issues.append(f"不随仓库提供的运行链接：{target}")
        if dest.suffix.lower() in {".puml", ".svg", ".png"}:
            issues.append(f"正文依赖生成图文件：{target}")
        if (
            parsed.fragment
            and dest.suffix == ".md"
            and unquote(parsed.fragment) not in index.anchors_for(dest)
        ):
            issues.append(f"失效锚点：{target}")
    for line, code in diagrams:
        starts = re.findall(r"^\s*@start(uml|mindmap)\b", code, re.MULTILINE)
        ends = re.findall(r"^\s*@end(uml|mindmap)\b", code, re.MULTILINE)
        if len(starts) != 1 or starts != ends:
            issues.append(f"第 {line} 行图源定界符不配对")
        if re.search(r"^\s*!include", code, re.MULTILINE):
            issues.append(f"第 {line} 行图源依赖外部 include，不是自包含图源")
    return issues, len(diagrams)


def anchors_from_parsed(parsed):
    """从已解析的 Markdown 计算锚点集合，复用重复标题编号规则。"""
    outside, _, _ = parsed
    prose = "\n".join(line for _, line in outside)
    found = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', prose))
    counts = {}
    for _, line in outside:
        match = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
        if match:
            base = slug(match[1])
            n = counts.get(base, 0)
            found.add(base if not n else f"{base}-{n}")
            counts[base] = n + 1
    return found


def check_heading_numbering(index, file):
    """检查 docs/ 渲染标题的层级十进制序号；代码围栏中的示例不算标题。"""
    issues = []
    counters = [0] * 7
    for line_number, line in index.document(file)[0]:
        match = re.match(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$", line)
        if not match:
            continue
        level = len(match[1])
        counters[level] += 1
        for index in range(level + 1, 7):
            counters[index] = 0
        expected = ".".join(str(counters[index]) for index in range(1, level + 1)) + "."
        if not match[2].startswith(expected + " "):
            issues.append(f"第 {line_number} 行标题缺少层级序号，应以 {expected} 开头")
    return issues


def check_selected_solution_only(index):
    """检查文档是否只陈述已确定的方案，不把未决选项交给读者。"""
    root = index.root
    heading_patterns = (
        re.compile(r"(?:候选|替代)方案|方案比较|方案\s*[A-ZＡ-Ｚ]", re.IGNORECASE),
    )
    prose_patterns = (
        re.compile(r"(?:候选|替代)方案|方案\s*[A-ZＡ-Ｚ]\s*[：:]", re.IGNORECASE),
        re.compile(r"^\s*\|\s*方案\s*\|"),
    )
    issues = []
    for relative_root in ("docs",):
        directory = root / relative_root
        if not directory.is_dir():
            continue
        for file in sorted(directory.rglob("*.md")):
            parsed = index.document(file)
            for line_number, line in parsed[0]:
                match = re.match(r"^#{1,6}\s+(.+?)(?:\s+#+)?$", line)
                if match and any(
                    pattern.search(match.group(1)) for pattern in heading_patterns
                ):
                    issues.append(
                        f"{file.relative_to(root)}: 第 {line_number} 行标题不得列出候选方案"
                    )
                elif any(pattern.search(line) for pattern in prose_patterns):
                    issues.append(
                        f"{file.relative_to(root)}: 第 {line_number} 行不得列出候选方案"
                    )
    return issues


def check_latest_only(index):
    """拒绝历史目录与日期快照；不读取或修改本地运行收据。"""
    root = index.root
    issues = []
    forbidden = {"history", "archive", "archives"}
    for path in sorted((root / "docs").rglob("*")):
        if path.is_dir() and path.name.casefold() in forbidden:
            issues.append(f"{path.relative_to(root)}: 文档只维护最新版，禁止历史目录")
        elif (
            path.is_file()
            and path.suffix == ".md"
            and re.search(r"(?:^|[-_])20\d{2}[-_]?\d{2}[-_]?\d{2}(?:$|[-_])", path.stem)
        ):
            issues.append(f"{path.relative_to(root)}: 文档只维护最新版，禁止日期快照")
    return issues


def check_version_comparisons(index):
    """拒绝普通文档中的版本对照；只有路径和标题均明确的对比页可例外。"""
    root = index.root
    roots = ("docs", "openspec")
    phrases = ("待审核目标", "收敛后的明确去处", "改前", "改后", "当前版本", "目标版本")
    path_tokens = ("comparison", "diff")
    title_tokens = ("对比", "差异", "comparison", "diff")
    issues = []
    for relative_root in roots:
        directory = root / relative_root
        if not directory.is_dir():
            continue
        for file in sorted(directory.rglob("*.md")):
            parsed = index.document(file)
            outside, _, _ = parsed
            prose = "\n".join(line for _, line in outside)
            title = next(
                (
                    match.group(1)
                    for _, line in outside
                    if (match := re.match(r"^#\s+(.+?)(?:\s+#+)?$", line))
                ),
                "",
            )
            path_allowed = any(
                token in str(file.relative_to(root)).casefold() for token in path_tokens
            )
            title_allowed = any(token in title.casefold() for token in title_tokens)
            if path_allowed and title_allowed:
                continue
            for phrase in phrases:
                # 双字阶段简称不能命中“修改/更改/整改”等动词的尾部；
                # 较长的禁止短语仍逐字检查，不用宽泛中文边界掩盖真实对照。
                pattern = (
                    re.compile(rf"(?<![修更整变悔涂篡批删]){re.escape(phrase)}")
                    if phrase in {"改前", "改后"}
                    else re.compile(re.escape(phrase))
                )
                if pattern.search(prose):
                    issues.append(
                        f"{file.relative_to(root)}: 普通文档禁止版本对照用语：{phrase}"
                    )
    return issues


def run(root):
    """遍历受治理文档并汇总可定位的静态问题；不评价语义和布局。"""
    root = Path(root).resolve()
    index = DocIndex(root)
    files = sorted((root / "docs").rglob("*.md"))
    files += [root / "AGENTS.md", root / "README.md"]
    files += sorted((root / "harness").rglob("*.md"))
    for file in files:
        index.document(file)
    issues = check_selected_solution_only(index)
    issues.extend(check_latest_only(index))
    issues.extend(check_version_comparisons(index))
    count = 0
    for file in files:
        errors, diagrams = check_file(index, file)
        count += diagrams
        issues.extend(f"{file.relative_to(root)}: {x}" for x in errors)
        if file.is_relative_to(root / "docs"):
            issues.extend(
                f"{file.relative_to(root)}: {x}"
                for x in check_heading_numbering(index, file)
            )
    if (root / "AGENTS.md").stat().st_size > 4000:
        issues.append("AGENTS.md 超出读取预算")
    if (root / ".git").exists():
        tracked = (
            subprocess.run(
                ["git", "ls-files", "-z", "--", "docs"],
                cwd=root,
                capture_output=True,
                check=True,
            )
            .stdout.decode()
            .split("\0")
        )
        for name in tracked:
            if name and Path(name).suffix.lower() in {".puml", ".svg", ".png"}:
                issues.append(f"生成图文件仍被跟踪：{name}")
    return {
        "status": "FAIL" if issues else "PASS",
        "files": len(files),
        "diagrams": count,
        "issues": issues,
        "scope": "静态链接、围栏与入口；不证明中文语义、图像布局或正式 Gate 验收",
    }


def main(argv=None, *, root: Path | None = None):
    """执行零参数文档检查并输出结构化结果。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        result = run(root or Path(__file__).resolve().parents[4])
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        result = {"status": "FAIL", "issues": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
