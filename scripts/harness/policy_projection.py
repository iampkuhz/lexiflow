"""检查或显式更新共享策略的兼容投影；不改变消费者字段形状。"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import yaml


def lookup(value, path):
    for key in path.split('.'):
        value = value[key]
    return value


def project(policy, rule):
    if 'source' in rule:
        return copy.deepcopy(lookup(policy, rule['source']))
    return {key: source[8:] if source.startswith('literal:') else copy.deepcopy(lookup(policy, source))
            for key, source in rule['fields'].items()}


def replace_block(text, path, value):
    """基于 YAML 节点定位替换一个字段，保留其余注释和手写内容。"""
    node = yaml.compose(text)
    for key in path.split('.'):
        pair = next((pair for pair in node.value if pair[0].value == key), None)
        if pair is None:
            raise ValueError(f'投影目标缺失：{path}')
        key_node, node = pair
    start = key_node.start_mark.line
    # Block collection ends at the next sibling token, not after that sibling's line.
    end = node.end_mark.line + (1 if isinstance(node, yaml.ScalarNode) or node.flow_style else 0)
    indent = key_node.start_mark.column
    block = yaml.safe_dump({key: value}, sort_keys=False, allow_unicode=True, width=100)
    rendered = ''.join(' ' * indent + line + '\n' for line in block.splitlines())
    lines = text.splitlines(keepends=True)
    return ''.join(lines[:start]) + rendered + ''.join(lines[end:])


def run(root: Path, write=False):
    spec = yaml.safe_load((root / 'harness/policy-projections.yaml').read_text())
    policy = yaml.safe_load((root / spec['source']).read_text())
    texts = {}
    documents = {}
    drift = []
    for rule in spec['projections']:
        name = rule['file']
        if name not in texts:
            texts[name] = (root / name).read_text()
            documents[name] = yaml.safe_load(texts[name])
        text, current = texts[name], documents[name]
        expected = project(policy, rule)
        if lookup(current, rule['target']) != expected:
            drift.append(f"{name}#{rule['target']}")
            if write:
                texts[name] = replace_block(text, rule['target'], expected)
                documents[name] = yaml.safe_load(texts[name])
    if write:
        for name, text in texts.items():
            if text != (root / name).read_text():
                (root / name).write_text(text)
    return drift


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--write', action='store_true')
    args = parser.parse_args()
    try:
        drift = run(args.root, args.write)
        ok = not drift or args.write
        print(json.dumps({'status': 'PASS' if ok else 'FAIL', 'drift': drift,
                          'updated': bool(args.write and drift)}, ensure_ascii=False))
        return 0 if ok else 1
    except (OSError, KeyError, ValueError, TypeError, yaml.YAMLError) as exc:
        print(json.dumps({'status': 'FAIL', 'reason': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
