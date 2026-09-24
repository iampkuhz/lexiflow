"""显式检查或链接本机 skill；不下载、不覆盖、不修改上游实现。"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import yaml


def inspect(root: Path, source: Path):
    policy = yaml.safe_load((root / "harness/documentation-policy.yaml").read_text())
    if policy["skill_links"] != ".agents/skills":
        raise ValueError("skill 链接只能写入原生仓库目录 .agents/skills")
    base = root / policy["skill_links"]
    if (root / ".agents").is_symlink() or base.is_symlink():
        raise ValueError("skill 容器必须为仓库内真实目录，不能通过父链接写入其他位置")
    results = []
    for item in policy["skills"]:
        name = item["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
            raise ValueError("非法 skill 名称")
        target, link = source / name, base / name
        entry = target / "SKILL.md"
        status = "ready"
        if not entry.is_file():
            status = "source-missing"
        else:
            text = entry.read_text()
            parts = text.split("---", 2)
            metadata = (
                yaml.safe_load(parts[1])
                if len(parts) == 3 and not parts[0].strip()
                else None
            )
            if not isinstance(metadata, dict) or metadata.get("name") != name:
                status = "source-invalid"
        if link.is_symlink():
            if link.resolve() != target.resolve():
                status = "target-conflict"
            elif status == "ready":
                status = "linked"
        elif link.exists():
            status = "target-conflict"
        results.append(
            {
                "name": name,
                "status": status,
                "entry": str(entry.resolve()),
                "source_directory": str(target.resolve()),
                "link": str(link),
            }
        )
    return results


def run(root: Path, source: Path, mode: str):
    results = inspect(root, source)
    bad = any(x["status"] not in {"ready", "linked"} for x in results)
    if mode == "link" and not bad:
        for item in results:
            if item["status"] == "ready":
                link = Path(item["link"])
                link.parent.mkdir(parents=True, exist_ok=True)
                # symlink_to 使用排他创建；竞争或已有目标不覆盖。
                link.symlink_to(
                    Path(item["source_directory"]), target_is_directory=True
                )
                item["status"] = "linked"
    complete = all(x["status"] == "linked" for x in results)
    return {
        "status": (
            "FAIL"
            if any(x["status"] == "target-conflict" for x in results)
            else "PASS" if complete else "BLOCKED"
        ),
        "skills": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "link"])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills",
    )
    args = parser.parse_args()
    try:
        result = run(args.root.resolve(), args.source.expanduser().resolve(), args.mode)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        result = {"status": "FAIL", "reason": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"PASS": 0, "BLOCKED": 1, "FAIL": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
