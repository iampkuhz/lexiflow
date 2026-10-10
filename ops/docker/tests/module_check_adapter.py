#!/usr/bin/env python3
"""执行 Docker 合同回归；不代表容器生命周期或目标平台验收。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import inspect
import io
import json
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
from scripts.verification.declarations import load_declarations

_TIMING_PREFIX = "LEXIFLOW_DOCKER_TIMING "
_GROUP_IDS = {
    name: f"eng.release.docker-{name}-on-change"
    for name in ("surface", "shell", "local", "node")
}
_TEST_ROOTS = ("ops/docker/tests", "ops/podman/tests", "ops/dataset/tests")
_SOURCE_ROOTS = ("ops/docker", "ops/podman", "ops/dataset", "ops/release")


def _timing_event(
    event: str, *, stream, test: str | None = None, seconds: float | None = None
) -> None:
    """向 stderr 即时写入不含运行参数的结构化计时证据。"""
    payload: dict[str, object] = {"event": event}
    if test is not None:
        payload["test"] = test
    if seconds is not None:
        payload["seconds"] = seconds
    stream.write(_TIMING_PREFIX + json.dumps(payload, separators=(",", ":")) + "\n")
    stream.flush()


class DiagnosticStream(io.StringIO):
    """即时输出测试诊断，同时保留摘要输入。"""

    def write(self, text: str) -> int:
        """逐段透传并刷新，超时前已执行部分不会丢失。"""
        written = super().write(text)
        sys.stderr.write(text)
        sys.stderr.flush()
        return written


class _TimedResult(unittest.TextTestResult):
    """记录用例计时并保持 unittest 原始诊断。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.per_test: dict[str, float] = {}
        self._start: float | None = None
        self._current_id: str | None = None

    def startTest(self, test):
        super().startTest(test)
        self._current_id, self._start = test.id(), time.monotonic()

    def stopTest(self, test):
        if self._start is not None and self._current_id is not None:
            seconds = round(time.monotonic() - self._start, 6)
            self.per_test[self._current_id] = seconds
            _timing_event(
                "test_completed",
                stream=self.stream,
                test=self._current_id,
                seconds=seconds,
            )
        self._start = self._current_id = None
        super().stopTest(test)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _declarations(root: Path) -> dict[str, dict[str, object]]:
    data = load_declarations(root)
    return {item["check_id"]: item for item in data["checks"]}


def _is_python_test(path: str) -> bool:
    return (
        path.startswith("ops/docker/tests/")
        and Path(path).name.startswith("test_")
        and path.endswith(".py")
    )


def _is_node_test(path: str) -> bool:
    return path.startswith(
        ("ops/podman/tests/", "ops/dataset/tests/")
    ) and path.endswith(".test.mjs")


def _is_test_source(path: str) -> bool:
    return (
        Path(path).name.startswith("test_")
        and path.endswith(".py")
        or Path(path).name.endswith(".test.mjs")
    )


def _members(declarations: dict[str, dict[str, object]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for name, check_id in _GROUP_IDS.items():
        if check_id not in declarations:
            raise ValueError(f"missing group declaration: {check_id}")
        paths = declarations[check_id]["input_paths"]
        members = [
            path for path in paths if _is_python_test(path) or _is_node_test(path)
        ]
        if len(members) != len(set(members)) or not members:
            raise ValueError(f"duplicate or empty group members: {name}")
        groups[name] = members
    flat = [path for members in groups.values() for path in members]
    if len(flat) != len(set(flat)):
        raise ValueError("test members overlap between groups")
    return groups


def _inventory(
    root: Path, include_sources: bool = True
) -> tuple[set[str], set[str], set[str]]:
    """读取 Git 可见库存；忽略文件不属于可交付测试库存。"""
    roots = _SOURCE_ROOTS if include_sources else _TEST_ROOTS
    proc = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *roots,
        ],
        cwd=root,
        capture_output=True,
        check=True,
    )
    files = {
        item.decode()
        for item in proc.stdout.split(b"\0")
        if item and (root / item.decode()).is_file()
    }
    py = {path for path in files if _is_python_test(path)}
    node = {path for path in files if _is_node_test(path)}
    source = (
        {
            path
            for path in files
            if path.startswith(_SOURCE_ROOTS) and not _is_test_source(path)
        }
        if include_sources
        else set()
    )
    return py, node, source


def _covers(paths: list[str], source: str) -> bool:
    return any(
        source == item.rstrip("/") or source.startswith(item.rstrip("/") + "/")
        for item in paths
    )


def _source_edges(root: Path, member: str, seen: set[str]) -> set[str]:
    """审计本组可解析的仓库源码引用和静态 JS import 闭包。"""
    if member in seen:
        return set()
    seen.add(member)
    content = (root / member).read_text(encoding="utf-8")
    edges: set[str] = set()
    # Full repository-relative literals used by copy/read/exec fixtures.  A new
    # literal is checked against the consuming group, not only the surface guard.
    for source in re.findall(
        r"(?<![\w/])(?:ops/(?:docker|podman|dataset|release)|infra/(?:postgres|local)|scripts/environment)/[\w./-]+",
        content,
    ):
        source = source.rstrip("./")
        if (root / source).is_file() and source != member:
            edges.add(source)
    for source in re.findall(
        r"(?:\.\./)+((?:ops/(?:docker|podman|dataset|release)|infra/(?:postgres|local)|scripts/environment)/[\w./-]+)",
        content,
    ):
        source = source.rstrip("./")
        if (root / source).is_file() and source != member:
            edges.add(source)
    if member.endswith(".py"):
        for node in ast.walk(ast.parse(content, filename=member)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module]
                if isinstance(node, ast.ImportFrom) and node.module
                else []
            )
            for name in modules:
                relative = name.replace(".", "/") + ".py"
                candidates = [relative, f"ops/docker/tests/{relative}"]
                for candidate in candidates:
                    if (root / candidate).is_file() and candidate != member:
                        edges.add(candidate)
    if member.endswith(".mjs"):
        # A fixture copy may split the directory and file list instead of
        # spelling each repository path as one literal.
        for loop in re.finditer(r"for\s*\(const\s+(\w+)\s+of\s+\[([^]]+)\]\)", content):
            variable, raw_files = loop.groups()
            body = content[loop.end() : loop.end() + 400].split(";", 1)[0]
            for directory in re.findall(
                r"path\.join\(root,\s*['\"]((?:ops|infra)/[^'\"]+)['\"],\s*"
                + re.escape(variable)
                + r"\)",
                body,
            ):
                if not re.search(r"(?:copyFile|readFile)", body):
                    continue
                for filename in re.findall(r"['\"]([\w.-]+)['\"]", raw_files):
                    candidate = f"{directory}/{filename}"
                    if (root / candidate).is_file():
                        edges.add(candidate)
        for target in re.findall(
            r"\b(?:from\s*|import\s*\(|import\s*)['\"]([^'\"]+)['\"]", content
        ):
            if target.startswith("."):
                resolved = (root / member).parent.joinpath(target).resolve()
                try:
                    relative = resolved.relative_to(root.resolve()).as_posix()
                except ValueError as error:
                    raise ValueError(
                        f"source import escapes repository: {member}: {target}"
                    ) from error
                if not resolved.is_file():
                    raise ValueError(f"missing source import: {member}: {relative}")
                edges.add(relative)
                edges.update(_source_edges(root, relative, seen))
    return edges


def _dynamic_target(root: Path, source: str, content: str, expression: str) -> str:
    """仅接受当前可静态绑定的变量/argv 动态 import，其余失败关闭。"""
    if "process.argv[1]" in expression:
        if not re.search(r"\bhelper\s*,\s*modulePath\b", content):
            raise ValueError(f"unresolved dynamic source edge: {source}")
        variable = "modulePath"
    else:
        match = re.search(r"JSON\.stringify\(([A-Za-z_]\w*)", expression)
        if match is None:
            match = re.search(r"\$\{([A-Za-z_]\w*)", expression)
        variable = match.group(1) if match else expression.strip()
    if not re.fullmatch(r"[A-Za-z_]\w*", variable):
        raise ValueError(f"unresolved dynamic source edge: {source}")
    assignment = re.search(rf"\bconst\s+{re.escape(variable)}\s*=\s*([^;]+);", content)
    if assignment is None:
        raise ValueError(f"unresolved dynamic source edge: {source}")
    literals = re.findall(r"['\"]([^'\"]+\.mjs)['\"]", assignment.group(1))
    if len(literals) != 1:
        raise ValueError(f"unresolved dynamic source edge: {source}")
    literal = literals[0]
    if literal.startswith("ops/"):
        target = root / literal
    elif literal.startswith("."):
        target = root / source
        target = target.parent / literal
    elif re.search(
        r"['\"]\.\.['\"]\s*,\s*['\"]" + re.escape(literal) + r"['\"]",
        assignment.group(1),
    ):
        target = (root / source).parent.parent / literal
    else:
        raise ValueError(f"unresolved dynamic source edge: {source}")
    try:
        relative = target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"unresolved dynamic source edge: {source}") from error
    if not target.is_file():
        raise ValueError(f"missing dynamic source target: {source} -> {relative}")
    return relative


def _audit_consumption(
    root: Path,
    declarations: dict[str, dict[str, object]],
    groups: dict[str, list[str]],
    selected: tuple[str, ...],
) -> None:
    for name in selected:
        check = declarations[_GROUP_IDS[name]]
        inputs = check["input_paths"]
        triggers = [item["path"] for item in check["triggers"]]
        for member in groups[name]:
            visited: set[str] = set()
            for source in _source_edges(root, member, visited):
                if not _covers(inputs, source) or not _covers(triggers, source):
                    raise ValueError(
                        f"source consumption gap: {name}: {member} -> {source}"
                    )
            for source in visited:
                if not source.endswith(".mjs"):
                    continue
                content = (root / source).read_text(encoding="utf-8")
                for expression in re.findall(r"\bimport\s*\(([^)]*)\)", content):
                    if expression.lstrip().startswith(("'", '"')):
                        continue
                    target = _dynamic_target(root, source, content, expression)
                    if not _covers(inputs, target) or not _covers(triggers, target):
                        raise ValueError(
                            f"source consumption gap: {name}: {source} -> {target}"
                        )


def _preflight(
    root: Path,
    declarations: dict[str, dict[str, object]],
    groups: dict[str, list[str]],
    group: str | None = None,
) -> None:
    py, node, source = _inventory(
        root, include_sources=group is None or group == "surface"
    )
    classified_py = {
        path for paths in groups.values() for path in paths if _is_python_test(path)
    }
    classified_node = {
        path for paths in groups.values() for path in paths if _is_node_test(path)
    }
    if py != classified_py or node != classified_node:
        raise ValueError(
            f"test inventory mismatch: missing={sorted((py | node) - (classified_py | classified_node))}, stale={sorted((classified_py | classified_node) - (py | node))}"
        )
    selected = tuple(_GROUP_IDS) if group is None else (group,)
    if "surface" in selected:
        surface = declarations[_GROUP_IDS["surface"]]
        declared_sources = {
            path
            for path in surface["input_paths"]
            if path.startswith(_SOURCE_ROOTS)
            and not _is_test_source(path)
            and path not in _SOURCE_ROOTS
            and not (root / path).is_dir()
        }
        if source != declared_sources:
            raise ValueError(
                f"surface source inventory mismatch: missing={sorted(source - declared_sources)}, stale={sorted(declared_sources - source)}"
            )
    # Only the selected check consumes bytes; all-group filename inventory above
    # is needed solely to prove the partition is complete.
    for name in selected:
        check_id = _GROUP_IDS[name]
        for item in declarations[check_id]["input_paths"]:
            path = root / item
            if not path.exists():
                raise ValueError(f"missing declared input for {check_id}: {item}")
    _audit_consumption(root, declarations, groups, selected)


def _load_python(root: Path, paths: list[str]) -> unittest.TestSuite:
    suite = unittest.TestSuite()
    test_directory = str(root / "ops/docker/tests")
    if test_directory not in sys.path:
        # unittest discovery historically inserts the directory so local support
        # modules (for example podman_fake_transport) remain importable.
        sys.path.insert(0, test_directory)
    for relative in paths:
        # Preserve unittest's original module-qualified test.id() identities.
        spec = importlib.util.spec_from_file_location(
            Path(relative).stem, root / relative
        )
        if spec is None or spec.loader is None:
            raise ValueError(f"cannot load selected test: {relative}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        loaded = unittest.defaultTestLoader.loadTestsFromModule(module)
        identities = [case.id() for case in _iter_cases(loaded)]
        expected = [
            case.id()
            for _, cls in inspect.getmembers(module, inspect.isclass)
            if issubclass(cls, unittest.TestCase) and cls.__module__ == spec.name
            for case in _iter_cases(
                unittest.defaultTestLoader.loadTestsFromTestCase(cls)
            )
        ]
        if (
            not identities
            or len(identities) != len(set(identities))
            or set(identities) != set(expected)
            or any(not item.startswith(spec.name + ".") for item in identities)
        ):
            raise ValueError(f"selected test identity mismatch: {relative}")
        suite.addTests(loaded)
    return suite


def _iter_cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_cases(item)
        else:
            yield item


def _top_slow(per_test: dict[str, float], limit: int = 10) -> list[dict[str, float]]:
    """成功时只输出前 N 慢用例摘要。"""
    return [
        {"test": name, "seconds": seconds}
        for name, seconds in sorted(
            per_test.items(), key=lambda item: item[1], reverse=True
        )[:limit]
    ]


def _node_run(
    root: Path, paths: list[str]
) -> tuple[subprocess.CompletedProcess, float, dict[str, object]]:
    runner = (
        "import {runTests} from './ops/release/check.mjs';"
        "const r=await runTests({timeoutMs:170_000,args:['--test','--test-reporter=tap',...process.argv.slice(1)]});"
        "console.log(JSON.stringify(r)); if(r.status!=='PASS') process.exitCode=1;"
    )
    started = time.monotonic()
    result = subprocess.run(
        ["node", "--input-type=module", "-e", runner, *paths],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    duration = round(time.monotonic() - started, 6)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = {"status": "FAIL", "checks_run": 0, "errors": 1}
    if not isinstance(report, dict):
        report = {"status": "FAIL", "checks_run": 0, "errors": 1}
    return result, duration, report


def _run(group: str | None) -> dict[str, object]:
    total_started = time.monotonic()
    root = _repo_root()
    declarations = _declarations(root)
    groups = _members(declarations)
    _preflight(root, declarations, groups, group)
    if group is None:
        py_paths = sorted(
            path for paths in groups.values() for path in paths if _is_python_test(path)
        )
        node_paths = sorted(
            path for paths in groups.values() for path in paths if _is_node_test(path)
        )
    else:
        py_paths = sorted(path for path in groups[group] if _is_python_test(path))
        node_paths = sorted(path for path in groups[group] if _is_node_test(path))
    stream = DiagnosticStream()
    py_duration = 0.0
    result = None
    if py_paths:
        py_started = time.monotonic()
        _timing_event("python_started", stream=stream)
        suite = _load_python(root, py_paths)
        result = unittest.TextTestRunner(
            stream=stream, verbosity=2, resultclass=_TimedResult
        ).run(suite)
        py_duration = round(time.monotonic() - py_started, 6)
        _timing_event("python_completed", stream=stream, seconds=py_duration)
    node_report: dict[str, object] = {"status": "NOT_RUN", "checks_run": 0, "errors": 0}
    node_duration: float | None = None
    node_exit: int | None = None
    node_ok: bool | None = None
    if node_paths:
        _timing_event("node_started", stream=stream)
        node, node_duration, node_report = _node_run(root, node_paths)
        if not isinstance(node_report, dict):
            node_report = {"status": "FAIL", "checks_run": 0, "errors": 1}
        node_exit = node.returncode
        node_ok = (
            node.returncode == 0
            and node_report.get("status") == "PASS"
            and type(node_report.get("checks_run")) is int
            and node_report["checks_run"] > 0
            and all(
                type(node_report.get(key)) is int and node_report[key] == 0
                for key in ("failures", "errors", "skipped")
            )
        )
        _timing_event("node_completed", stream=stream, seconds=node_duration)
        stream.write(node.stdout + node.stderr)
        stream.flush()
    py_count = result.testsRun if result is not None else 0
    py_ok = result is None or (
        result.wasSuccessful() and py_count > 0 and not result.skipped
    )
    node_count = node_report.get("checks_run", 0)
    if type(node_count) is not int or node_count < 0:
        node_count = 0
    passed = py_ok and (node_ok is not False) and py_count + node_count > 0
    output = stream.getvalue()
    report: dict[str, object] = {
        "status": "PASS" if passed else "FAIL",
        "checks_run": py_count + node_count,
        "failures": (
            len(result.failures) + len(result.errors) if result is not None else 0
        )
        + (0 if node_ok is not False else 1),
        "errors": len(result.errors) if result is not None else 0,
        "skipped": len(result.skipped) if result is not None else 0,
        "node_transaction_exit_code": node_exit,
        "node_transaction_no_skips": node_ok,
        "python_unittest_duration_seconds": py_duration,
        "node_suite_duration_seconds": node_duration,
        "total_duration_seconds": round(time.monotonic() - total_started, 6),
        "reason": "" if passed else "docker-contract-tests-incomplete",
        "tool_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
    }
    per_test = getattr(result, "per_test", {})
    if per_test:
        report["per_test_timing"] = per_test
        if passed:
            report["top_slow_tests"] = _top_slow(per_test)
    return report


def run_tests() -> dict[str, object]:
    """保留无参完整 Docker 合同入口。"""
    return _run(None)


def main() -> int:
    """运行无参完整集或固定日常组，输出 Verify JSON contract。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=tuple(_GROUP_IDS))
    args = parser.parse_args()
    try:
        report = run_tests() if args.group is None else _run(args.group)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        report = {
            "status": "FAIL",
            "checks_run": 0,
            "failures": 1,
            "errors": 0,
            "skipped": 0,
            "reason": f"docker-contract-preflight-failed: {error}",
            "tool_output_sha256": hashlib.sha256(b"").hexdigest(),
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
