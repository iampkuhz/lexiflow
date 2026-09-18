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
REF_DEF = re.compile(r'^\s*\[[^\]]+\]:\s*<?([^\s>]+)>?', re.M)
REFERENCE = re.compile(r'!?\[([^\]\n]+)\]\[([^\]\n]*)\]')
HTML_LINK = re.compile(r'<(?:a|img)\b[^>]*?\b(?:href|src)=["\']([^"\']+)["\']', re.I)


def slug(text):
    text = re.sub(r'<[^>]+>', '', text).replace('`', '').lower()
    text = re.sub(r'[^\w\-\s]', '', text)
    return re.sub(r'\s', '-', text.strip())


def parse(text):
    outside, diagrams, errors = [], [], []
    fence = None
    body = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if match:
            token, info = match.groups()
            if fence is None:
                fence = (token, info.strip(), number)
                body = []
            elif token[0] == fence[0][0] and len(token) >= len(fence[0]) and not info.strip():
                if fence[1] == 'plantuml':
                    diagrams.append((fence[2], '\n'.join(body) + '\n'))
                fence = None
            else:
                body.append(line)
        elif fence:
            body.append(line)
        else:
            outside.append((number, line))
    if fence:
        errors.append(f'第 {fence[2]} 行代码围栏未闭合')
    return outside, diagrams, errors


def anchors(text):
    outside, _, _ = parse(text)
    prose = '\n'.join(line for _, line in outside)
    found = set(re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', prose))
    counts = {}
    for _, line in outside:
        match = re.match(r'^#{1,6}\s+(.+?)(?:\s+#+)?$', line)
        if match:
            base = slug(match[1])
            n = counts.get(base, 0)
            found.add(base if not n else f'{base}-{n}')
            counts[base] = n + 1
    return found


def check_file(root, file, forbidden_extensions=('.puml', '.svg', '.png')):
    issues = []
    text = file.read_text()
    outside, diagrams, errors = parse(text)
    issues.extend(errors)
    prose = '\n'.join(line for _, line in outside)
    prose = re.sub(r'(`+).*?\1', '', prose)
    explicit = re.findall(r'<a\s+(?:id|name)=["\']([^"\']+)', prose)
    if len(explicit) != len(set(explicit)):
        issues.append('显式锚点重复')
    definitions = {x.casefold() for x in re.findall(r'^\s*\[([^\]]+)\]:', prose, re.M)}
    for label, reference in REFERENCE.findall(prose):
        if (reference or label).casefold() not in definitions:
            issues.append(f'未定义的引用链接：{reference or label}')
    targets = [m[1] for m in LINK.finditer(prose)] + REF_DEF.findall(prose) + HTML_LINK.findall(prose)
    for target in targets:
        target = target.strip('<>')
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            continue
        path = unquote(parsed.path)
        if any(x in path for x in ['<', '>', '*']):
            issues.append(f'示例路径不能作为链接：{target}')
            continue
        dest = (file.parent / path).resolve() if path else file.resolve()
        if not dest.is_relative_to(root.resolve()):
            issues.append(f'仓库外本地链接：{target}')
            continue
        if not dest.exists():
            issues.append(f'失效链接：{target}')
            continue
        relative = dest.relative_to(root.resolve())
        if relative.parts and relative.parts[0] in {'tmp', '.local'}:
            issues.append(f'不随仓库提供的运行链接：{target}')
        if dest.suffix.lower() in forbidden_extensions:
            issues.append(f'正文依赖生成图文件：{target}')
        if parsed.fragment and dest.suffix == '.md' and unquote(parsed.fragment) not in anchors(dest.read_text()):
            issues.append(f'失效锚点：{target}')
    for line, code in diagrams:
        starts = re.findall(r'^\s*@start(uml|mindmap)\b', code, re.M)
        ends = re.findall(r'^\s*@end(uml|mindmap)\b', code, re.M)
        if len(starts) != 1 or starts != ends:
            issues.append(f'第 {line} 行图源定界符不配对')
        if re.search(r'^\s*!include', code, re.M):
            issues.append(f'第 {line} 行图源依赖外部 include，不是自包含图源')
    return issues, len(diagrams)


def check_latest_only(root, policy):
    """拒绝历史目录与日期快照；不读取或修改本地运行收据。"""
    maintenance = policy.get('maintenance', {})
    if maintenance.get('mode') != 'latest-only':
        return []
    issues = []
    forbidden = set(maintenance['forbidden_directory_names'])
    for path in sorted((root / 'docs').rglob('*')):
        if path.is_dir() and path.name.casefold() in forbidden:
            issues.append(f'{path.relative_to(root)}: 文档只维护最新版，禁止历史目录')
        elif path.is_file() and path.suffix == '.md' and re.search(
                r'(?:^|[-_])20\d{2}[-_]?\d{2}[-_]?\d{2}(?:$|[-_])', path.stem):
            issues.append(f'{path.relative_to(root)}: 文档只维护最新版，禁止日期快照')
    return issues


def run(root):
    policy = yaml.safe_load((root / 'harness/documentation-policy.yaml').read_text())
    issues = check_latest_only(root, policy)
    count = 0
    files = sorted((root / 'docs').rglob('*.md'))
    files += [root / 'AGENTS.md', root / 'README.md', root / 'harness/README.md']
    for file in files:
        errors, diagrams = check_file(root, file, policy['forbidden_diagram_link_extensions'])
        count += diagrams
        issues.extend(f'{file.relative_to(root)}: {x}' for x in errors)
    if (root / 'AGENTS.md').stat().st_size > policy['root_instruction_budget_bytes']:
        issues.append('AGENTS.md 超出读取预算')
    if (root / '.git').exists():
        tracked = subprocess.run(['git', 'ls-files', '-z', '--', 'docs'], cwd=root,
                                 capture_output=True, check=True).stdout.decode().split('\0')
        for name in tracked:
            if name and Path(name).suffix.lower() in policy['forbidden_diagram_link_extensions']:
                issues.append(f'生成图文件仍被跟踪：{name}')
    return {'status': 'FAIL' if issues else 'PASS', 'files': len(files),
            'diagrams': count, 'issues': issues,
            'scope': '静态链接、围栏与入口；不证明中文语义、图像布局或正式 Gate 验收'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        result = run(args.root)
    except (OSError, ValueError, subprocess.CalledProcessError, yaml.YAMLError) as exc:
        result = {'status': 'FAIL', 'issues': [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
