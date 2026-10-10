"""在交付 Stop 串行执行 Verify；进度走 stderr，协议与有界失败决策走 stdout。"""

from __future__ import annotations

import json
import os
import select
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DELIVERY_TIMEOUT_SECONDS = 1800
HEARTBEAT_SECONDS = 30
STOP_EVENT = "Stop"
DISABLE_ENVIRONMENT = "LEXIFLOW_DELIVERY_HOOK_DISABLE"
INTERMEDIATE_MARKER = "<!-- lexiflow:intermediate -->"
INPUT_LIMIT = 65536


def progress(message: str) -> None:
    """只向 stderr 输出有界执行进度，不泄漏 Hook 原始输入。"""
    print(f"LexiFlow: {message}", file=sys.stderr, flush=True)


def decision(status: str, detail: str, active: bool = False) -> dict:
    """一次自动修复后仍失败就终止；结束不是 PASS，不锁编辑或 Git。"""
    message = f"LexiFlow 交付 {status}：{detail}"
    if status == "PASS":
        return {"systemMessage": message}
    if active:
        return {
            "continue": False,
            "stopReason": message,
            "systemMessage": message + "；已停止自动重试，请人工处理后重新交付。",
        }
    return {
        "decision": "block",
        "reason": message
        + "；请修复后再交付；无法修复则明确报告 FAIL/BLOCKED，禁止宣称 PASS。",
        "systemMessage": message,
    }


def progress_observer():
    """只消费公开执行事件；不能访问环境、stdin、日志或替换结果。"""
    last_heartbeat = [0.0]

    def observe(event: dict) -> None:
        label = " ".join(event["argv"])
        if event["event"] == "started":
            last_heartbeat[0] = time.monotonic()
            progress(f"开始 {label}（单项上限 {event['timeout_seconds']}s）")
        elif (
            event["event"] == "heartbeat"
            and time.monotonic() - last_heartbeat[0] >= HEARTBEAT_SECONDS
        ):
            last_heartbeat[0] = time.monotonic()
            progress(f"仍在执行 {label}，已用 {event['elapsed_seconds']}s")
        elif event["event"] == "finished":
            progress(
                f"命令结束：{label} ({event['exit_reason']}, exit={event['exit_code']})；判定待校验"
            )

    return observe


def input_drift_detail(check: dict) -> str:
    """从报告内的冻结快照解释漂移；不拿现在的工作树猜测过去的变化。"""
    snapshots = check.get("input_snapshot", {})
    original = {
        item["locator"]: item["sha256"]
        for item in snapshots.get("pre", {}).get("files", [])
    }
    changes = []
    for stage, label in (
        ("before_execution", "执行前"),
        ("post", "执行后"),
        ("final", "收尾复核"),
    ):
        snapshot = snapshots.get(stage, {})
        if not snapshot:
            continue
        observed = {
            item["locator"]: item["sha256"] for item in snapshot.get("files", [])
        }
        for path in sorted(original.keys() | observed.keys()):
            before, after = original.get(path), observed.get(path)
            if before != after:
                changes.append(
                    f"{label} {path}：{before[:12] if before else '不存在'}"
                    f" → {after[:12] if after else '不存在'}"
                )
    detail = "；".join(changes[:12]) or "配置或输入描述符变化，详见报告快照"
    if len(changes) > 12:
        detail += f"；另有 {len(changes) - 12} 项变化，详见报告"
    return (
        "验证输入发生变化："
        + detail
        + "。即使测试自身通过，也不能证明变化后的输入通过；请停止源码/配置写入后重新验证。"
    )


def report_detail(report: dict, root: Path | None = None) -> str:
    """从冻结报告提炼失败、缺环境和输入漂移诊断。"""
    parts = [f"{report.get('scope', 'Verify')}：{report.get('reason', 'unknown')}"]
    if report.get("detail"):
        parts.append(str(report["detail"])[:1200])
    for check in report.get("checks", []):
        if check.get("status") == "PASS":
            continue
        item = f"{check['check_id']} {check.get('status')} ({check.get('reason')})"
        missing = check.get("environment", {}).get("missing", [])
        if missing:
            item += "；缺少环境 " + ", ".join(missing)
        artifacts = check.get("process", {}).get("output_artifacts", {})
        locators = [
            a.get("locator", "") for a in artifacts.values() if isinstance(a, dict)
        ]
        if locators:
            item += "；诊断日志 " + ", ".join(locators)
        parts.append(item)
        if check.get("reason") == "input-drift":
            parts.append(input_drift_detail(check))
            continue
        typed = check.get("result_contract", {}).get("report", {})
        if typed.get("detail"):
            parts.append(str(typed["detail"])[-1600:])
        elif root:
            for locator in locators:
                path = (root / locator).resolve()
                if root.resolve() in path.parents and path.is_file():
                    with path.open("rb") as stream:
                        stream.seek(max(0, path.stat().st_size - 1600))
                        tail = stream.read().decode("utf-8", errors="replace").strip()
                    if tail:
                        parts.append(tail)
    publication = report.get("publication", {}).get("locator")
    if publication:
        parts.append(f"报告 {publication}")
    return "\n".join(parts)


def report_progress(report: dict) -> None:
    """只显示 Verify 最终判定，明确区分未执行、去重与真实检查通过。"""
    for check in report.get("checks", []):
        label = check["check_id"]
        if label.startswith("eng.backend.delivery"):
            label = "Java/Gradle deliveryFull [" + label + "]"
        elif "python-quality" in label:
            label = "Python/Ruff [" + label + "]"
        detail = check.get("reason", "")
        missing = check.get("environment", {}).get("missing", [])
        if missing:
            detail += "；未执行，缺少 " + ", ".join(missing)
        progress(f"{label} {check['status']}" + (f"：{detail}" if detail else ""))


def _execute_delivery(root: Path, window) -> tuple[str, str]:
    """按真实风险执行完整开发视图，准备所需隔离服务；不签发 Formal 或发行 PASS。"""
    from scripts.environment.services.isolated_postgres_redis import (
        isolated_test_services,
    )
    from scripts.verification.commands.run_development_checks import (
        prepare_development,
        execute_development,
    )

    prepared = prepare_development(root)
    profile = prepared["frozen_profile"]
    scopes = [profile["verification_scope"]]
    progress("开始风险所选开发验证：" + " → ".join(scopes))
    required = {
        name
        for check in profile["checks"]
        for name in check.get("required_environment", [])
    }
    with isolated_test_services(root, required, progress):
        result = execute_development(
            root,
            prepared=prepared,
            progress_observer=progress_observer(),
            _window=window,
        )
        publications = []
        failures = []
        for report in [result["report"]] if result.get("report") else []:
            report_progress(report)
            publication = report.get("publication", {}).get("locator")
            if publication:
                publications.append(publication)
            if report.get("result") != "PASS":
                failures.append(report_detail(report, root))
        if result["result"] != "PASS":
            detail = "\n".join(failures) or result.get(
                "reason", "development-incomplete"
            )
            if result.get("detail"):
                detail += "；" + result["detail"]
            return result["result"], detail
        return "PASS", (
            "风险所选开发验证全部通过（"
            + ", ".join(scopes)
            + "）；不是完整发行基线或独立 Formal 验收；报告 "
            + ", ".join(publications)
        )


def execute_delivery(root: Path) -> tuple[str, str]:
    """与独立 CLI、工作包和 Formal validation 共用窗口；两种 Verify 期间不交出锁。"""
    from scripts.verification.execution.checkout_verification_window import (
        WindowError,
        verification_window,
    )

    try:
        with verification_window(root) as window:
            return _execute_delivery(root, window)
    except WindowError as exc:
        return exc.status, f"{exc.code}：{exc}；本次不声明交付 PASS"


def evaluate(root: Path, event: object) -> dict:
    """对单次 Stop 事件决定是否运行交付检查；中间回合不签 PASS。"""
    from scripts.verification.execution.check_process_control import (
        Interrupted,
        bounded,
    )

    if os.environ.get(DISABLE_ENVIRONMENT) == "1":
        return {
            "systemMessage": "LexiFlow 交付 BLOCKED：Hook 已显式停用，未验证，不代表 PASS。"
        }
    if not isinstance(event, dict):
        return decision("BLOCKED", "Hook 输入必须是 JSON 对象", True)
    if event.get("hook_event_name") != STOP_EVENT:
        return {}
    active = event.get("stop_hook_active") is True
    try:
        message = event.get("last_assistant_message")
        if isinstance(message, str) and message.rstrip().endswith(
            "\n" + INTERMEDIATE_MARKER
        ):
            return {
                "systemMessage": "LexiFlow：中间回合，未执行交付检查；不代表 PASS。"
            }
        if message == INTERMEDIATE_MARKER:
            return {
                "systemMessage": "LexiFlow：中间回合，未执行交付检查；不代表 PASS。"
            }
        progress("收到交付 Stop，正在检查变更与并发状态")
        with bounded(DELIVERY_TIMEOUT_SECONDS):
            from scripts.verification.planning.check_scope_selection import (
                changed_paths,
                resolve_base,
            )

            if not changed_paths(root, resolve_base(root)):
                return {
                    "systemMessage": "LexiFlow：无待检变更，未触发交付检查；未签发 PASS。"
                }
            status, detail = execute_delivery(root)
            return decision(
                status, detail, active or detail.startswith("verification-window-")
            )

    except Interrupted as exc:
        return decision("BLOCKED", str(exc) + "；已清理正在执行的检查进程", True)
    except ModuleNotFoundError as exc:
        return decision(
            "BLOCKED",
            f"Python 依赖缺失：{exc.name}；请准备 requirements-dev.txt 对应 venv",
            active,
        )
    except (OSError, ValueError) as exc:
        return decision("BLOCKED", f"{type(exc).__name__}: {exc}", active)


def read_event() -> object:
    """有界读取标准输入的 Stop JSON，拒绝无 EOF 或超长输入。"""
    if sys.stdin.isatty():
        raise ValueError(
            "标准输入是交互终端；请通过管道传入 Stop JSON（不会等待终端 EOF）"
        )
    # 管道写入者不关闭时也有硬期限，不能再次无限等待 EOF。
    deadline = time.monotonic() + 3
    raw = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
            raise ValueError("Hook 输入超过 3 秒未收到 EOF；请关闭管道写端")
        chunk = os.read(sys.stdin.fileno(), INPUT_LIMIT + 1 - len(raw))
        if not chunk:
            break
        raw.extend(chunk)
        if len(raw) > INPUT_LIMIT:
            raise ValueError(f"Hook 输入超过 {INPUT_LIMIT} 字节上限")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Hook 输入 JSON 无效（第 {exc.lineno} 行、第 {exc.colno} 列）"
        ) from exc


def main() -> int:
    """适配 Codex/Qoder Stop 协议，输出有界继续或阻断决策。"""
    if sys.argv[1:] not in ([], ["--client", "codex"], ["--client", "qoder"]):
        print(
            json.dumps(
                decision("BLOCKED", "入口仅接受 --client codex|qoder", True),
                ensure_ascii=False,
            )
        )
        return 0
    if os.environ.get(DISABLE_ENVIRONMENT) == "1":
        result = evaluate(ROOT, {})
    else:
        try:
            event = read_event()
            result = evaluate(ROOT, event)
        except (OSError, ValueError, UnicodeError) as exc:
            result = decision("BLOCKED", str(exc), True)
    # Qoder CLI/IDE 用 deny，Codex 用 block；检查策略不因客户端改变。
    if sys.argv[1:] == ["--client", "qoder"] and result.get("decision") == "block":
        result["decision"] = "deny"
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    # 先切到已显式准备的本仓 venv，随后才加载依赖 PyYAML 的 Verification。
    python = ROOT / ".local/lexiflow-python/bin/python"
    if (
        python.is_file()
        and Path(sys.prefix).resolve() != python.parent.parent.resolve()
    ):
        os.execv(
            str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]]
        )
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
