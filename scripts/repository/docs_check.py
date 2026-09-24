"""检查文档链接、锚点、内嵌图源与规则入口；不声称完成语义或视觉审查。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

LINK = re.compile(r'!?\[[^\]\n]*\]\(([^\s]+?)(?:\s+"[^"]*")?\)')
REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?", re.M)
REFERENCE = re.compile(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]")
HTML_LINK = re.compile(r'<(?:a|img)\b[^>]*?\b(?:href|src)=["\']([^"\']+)["\']', re.I)


def slug(text):
    text = re.sub(r"<[^>]+>", "", text).replace("`", "").lower()
    text = re.sub(r"[^\w\-\s]", "", text)
    return re.sub(r"\s", "-", text.strip())


def parse(text):
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
    outside, _, _ = parse(text)
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


def check_file(root, file, forbidden_extensions=(".puml", ".svg", ".png")):
    issues = []
    text = file.read_text()
    outside, diagrams, errors = parse(text)
    issues.extend(errors)
    prose = "\n".join(line for _, line in outside)
    prose = re.sub(r"(`+).*?\1", "", prose)
    explicit = re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', prose)
    if len(explicit) != len(set(explicit)):
        issues.append("显式锚点重复")
    definitions = {x.casefold() for x in re.findall(r"^\s*\[([^\]]+)\]:", prose, re.M)}
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
        if dest.suffix.lower() in forbidden_extensions:
            issues.append(f"正文依赖生成图文件：{target}")
        if (
            parsed.fragment
            and dest.suffix == ".md"
            and unquote(parsed.fragment) not in anchors(dest.read_text())
        ):
            issues.append(f"失效锚点：{target}")
    for line, code in diagrams:
        starts = re.findall(r"^\s*@start(uml|mindmap)\b", code, re.M)
        ends = re.findall(r"^\s*@end(uml|mindmap)\b", code, re.M)
        if len(starts) != 1 or starts != ends:
            issues.append(f"第 {line} 行图源定界符不配对")
        if re.search(r"^\s*!include", code, re.M):
            issues.append(f"第 {line} 行图源依赖外部 include，不是自包含图源")
    return issues, len(diagrams)


def check_heading_numbering(file, policy):
    """检查 docs/ 渲染标题的层级十进制序号；代码围栏中的示例不算标题。"""
    numbering = policy.get("heading_numbering", {})
    if not numbering.get("required"):
        return []
    issues = []
    counters = [0] * 7
    for line_number, line in parse(file.read_text())[0]:
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


def check_selected_solution_only(root, policy):
    """检查文档是否只陈述已确定的方案，不把未决选项交给读者。"""
    config = policy.get("selected_solution_only", {})
    heading_patterns = [
        re.compile(pattern, re.I)
        for pattern in config.get("prohibited_heading_patterns", [])
    ]
    prose_patterns = [
        re.compile(pattern, re.I)
        for pattern in config.get("prohibited_prose_patterns", [])
    ]
    issues = []
    for relative_root in config.get("documentation_roots", []):
        directory = root / relative_root
        if not directory.is_dir():
            continue
        for file in sorted(directory.rglob("*.md")):
            for line_number, line in parse(file.read_text())[0]:
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


def check_latest_only(root, policy):
    """拒绝历史目录与日期快照；不读取或修改本地运行收据。"""
    maintenance = policy.get("maintenance", {})
    if maintenance.get("mode") != "latest-only":
        return []
    issues = []
    forbidden = set(maintenance["forbidden_directory_names"])
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


def check_version_comparisons(root, policy):
    """拒绝普通文档中的版本对照；只有路径和标题均明确的对比页可例外。"""
    config = policy.get("maintenance", {}).get("version_comparison", {})
    roots = config.get("documentation_roots", [])
    phrases = config.get("prohibited_phrases", [])
    path_tokens = tuple(
        token.casefold() for token in config.get("allowed_path_tokens", [])
    )
    title_tokens = tuple(
        token.casefold() for token in config.get("allowed_title_tokens", [])
    )
    issues = []
    for relative_root in roots:
        directory = root / relative_root
        if not directory.is_dir():
            continue
        for file in sorted(directory.rglob("*.md")):
            outside, _, _ = parse(file.read_text())
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
                if phrase in prose:
                    issues.append(
                        f"{file.relative_to(root)}: 普通文档禁止版本对照用语：{phrase}"
                    )
    return issues


def run(root):
    policy = yaml.safe_load((root / "harness/documentation-policy.yaml").read_text())
    issues = check_selected_solution_only(root, policy)
    issues.extend(check_latest_only(root, policy))
    issues.extend(check_version_comparisons(root, policy))
    count = 0
    files = sorted((root / "docs").rglob("*.md"))
    files += [root / "AGENTS.md", root / "README.md", root / "harness/README.md"]
    for file in files:
        errors, diagrams = check_file(
            root, file, policy["forbidden_diagram_link_extensions"]
        )
        count += diagrams
        issues.extend(f"{file.relative_to(root)}: {x}" for x in errors)
        if file.is_relative_to(root / "docs"):
            issues.extend(
                f"{file.relative_to(root)}: {x}"
                for x in check_heading_numbering(file, policy)
            )
    if (root / "AGENTS.md").stat().st_size > policy["root_instruction_budget_bytes"]:
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
            if (
                name
                and Path(name).suffix.lower()
                in policy["forbidden_diagram_link_extensions"]
            ):
                issues.append(f"生成图文件仍被跟踪：{name}")
    return {
        "status": "FAIL" if issues else "PASS",
        "files": len(files),
        "diagrams": count,
        "issues": issues,
        "scope": "静态链接、围栏与入口；不证明中文语义、图像布局或正式 Gate 验收",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args()
    try:
        result = run(args.root)
    except (OSError, ValueError, subprocess.CalledProcessError, yaml.YAMLError) as exc:
        result = {"status": "FAIL", "issues": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
