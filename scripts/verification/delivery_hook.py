"""在交付 Stop 串行执行 Verify；进度走 stderr，协议与有界失败决策走 stdout。"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import select
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = "harness/delivery-hooks.json"
INPUT_LIMIT = 65536


class Interrupted(BaseException):
    """必须穿过检查器的普通异常处理，确保超时或中断不会变成成功。"""


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


def read_policy(root: Path) -> dict:
    """读取 Stop 策略并校验模式、超时和中间回合标记。"""
    try:
        policy = json.loads((root / POLICY_PATH).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{POLICY_PATH} JSON 无效（第 {exc.lineno} 行、第 {exc.colno} 列）"
        ) from exc
    if not isinstance(policy, dict):
        raise ValueError(f"{POLICY_PATH} 顶层必须是对象")
    if policy.get("mode") == "off":
        return policy
    if policy.get("mode") != "enforce" or policy.get("event") != "Stop":
        raise ValueError(f"{POLICY_PATH} 要求 mode=enforce、event=Stop")
    if policy.get("disable_environment") != "LEXIFLOW_DELIVERY_HOOK_DISABLE":
        raise ValueError(f"{POLICY_PATH} disable_environment 必须使用固定逃生变量")
    if policy.get("intermediate_marker") != "<!-- lexiflow:intermediate -->":
        raise ValueError(f"{POLICY_PATH} intermediate_marker 无效")
    for key, maximum in (("timeout_seconds", 1800), ("heartbeat_seconds", 30)):
        value = policy.get(key)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{POLICY_PATH} {key} 必须是 1..{maximum} 的整数")
    return policy


@contextlib.contextmanager
def bounded(seconds: int):
    """POSIX 主线程信号边界；内核负责清理正在运行的独立 Check 进程组。"""

    def interrupt(signum, _frame):
        reason = (
            f"总执行超过 {seconds} 秒" if signum == signal.SIGALRM else "执行被中断"
        )
        raise Interrupted(reason)

    signals = (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.signal(sig, interrupt) for sig in signals}
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_with_progress(interval: int):
    """包装静态 Check 命令，定时报告进度但不改变执行结论。"""
    from scripts.verification.kernel import run_check_process

    def run(argv, cwd, env, timeout, executable):
        # argv 来自受审查的静态 Module Check；不打印环境或 Hook 输入。
        label = " ".join(argv)
        progress(f"开始 {label}（单项上限 {timeout}s）")
        done = threading.Event()
        started = time.monotonic()

        def heartbeat():
            while not done.wait(interval):
                progress(f"仍在执行 {label}，已用 {int(time.monotonic() - started)}s")

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            result = run_check_process(argv, cwd, env, timeout, executable)
            code = result["exit_code"]
            if result.get("timed_out"):
                outcome = f"命令执行失败：超时（单项上限 {timeout}s）"
            elif result.get("exit_reason") == "spawn-error":
                outcome = "命令执行失败：进程未能启动"
            elif code is not None and code < 0:
                outcome = f"命令执行失败：被信号 {-code} 终止"
            elif code == 0:
                outcome = "命令执行成功（退出码 0；检查结果及输入一致性待校验）"
            else:
                outcome = f"命令执行失败（退出码 {code}）"
            progress(f"{outcome}：{label}")
            return result
        finally:
            done.set()
            thread.join(timeout=1)

    return run


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


def execute_delivery(root: Path, policy: dict) -> tuple[str, str]:
    """同一输入上串行执行两种公开 Verify；不使用旧 PASS 或签发正式 receipt。"""
    from scripts.environment.test_services import isolated_test_services
    from scripts.verification import (
        freeze_inputs,
        persist_report,
        verify_changes,
        verify_repository,
    )
    from scripts.verification.scope import changed_paths, resolve_base

    progress(
        "开始交付检查：Change Verify → Repository Verify；按需准备隔离测试容器，不安装依赖或启动容器引擎"
    )
    before = freeze_inputs(root)
    if before["result"] != "PASS":
        return before["result"], report_detail(before)
    base = resolve_base(root)
    paths = changed_paths(root, base)
    required = {
        name
        for check in before["checks"]
        for name in check.get("required_environment", [])
    }
    with isolated_test_services(root, required, progress):
        runner = run_with_progress(policy["heartbeat_seconds"])
        publications = []
        for label, verify in (
            ("Change", verify_changes),
            ("Repository", verify_repository),
        ):
            progress(f"开始 {label} Verify")
            report = verify(root, runner=runner)
            report["publication"] = persist_report(root, report)
            report_progress(report)
            progress(
                f"{label} Verify {report['result']}：{report['publication']['locator']}"
            )
            if report["result"] != "PASS":
                return report["result"], report_detail(report, root)
            publications.append(report["publication"]["locator"])
        after = freeze_inputs(root)
        if (
            after.get("result") != "PASS"
            or before["input_fingerprint"] != after.get("input_fingerprint")
            or base != resolve_base(root)
            or paths != changed_paths(root, base)
        ):
            return (
                "FAIL",
                "input-drift：两种 Verify 期间源码、配置或 Git 范围变化，请稳定输入后重跑",
            )
        return "PASS", "Change/Repository Verify 均通过；报告 " + ", ".join(
            publications
        )


def evaluate(root: Path, event: object) -> dict:
    """对单次 Stop 事件决定是否运行交付检查；中间回合不签 PASS。"""
    if os.environ.get("LEXIFLOW_DELIVERY_HOOK_DISABLE") == "1":
        return {
            "systemMessage": "LexiFlow 交付 BLOCKED：Hook 已显式停用，未验证，不代表 PASS。"
        }
    if not isinstance(event, dict):
        return decision("BLOCKED", "Hook 输入必须是 JSON 对象", True)
    if event.get("hook_event_name") != "Stop":
        return {}
    active = event.get("stop_hook_active") is True
    try:
        policy = read_policy(root)
        if policy["mode"] == "off":
            return {
                "systemMessage": "LexiFlow 交付 BLOCKED：mode=off，未验证，不代表 PASS。"
            }
        message = event.get("last_assistant_message")
        if isinstance(message, str) and message.rstrip().endswith(
            "\n" + policy["intermediate_marker"]
        ):
            return {
                "systemMessage": "LexiFlow：中间回合，未执行交付检查；不代表 PASS。"
            }
        if message == policy["intermediate_marker"]:
            return {
                "systemMessage": "LexiFlow：中间回合，未执行交付检查；不代表 PASS。"
            }
        progress("收到交付 Stop，正在检查变更与并发状态")
        with bounded(policy["timeout_seconds"]):
            from scripts.verification.scope import changed_paths, resolve_base

            if not changed_paths(root, resolve_base(root)):
                return {
                    "systemMessage": "LexiFlow：无待检变更，未触发交付检查；未签发 PASS。"
                }
            state = root / "tmp/quality/delivery-hook"
            state.mkdir(parents=True, exist_ok=True)
            # LOCK_NB 不等待；不能 unlink 锁文件，否则另一个调用可创建第二把锁。
            with (state / "execution.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return decision(
                        "BLOCKED",
                        "同 checkout 已有交付 Hook 正在检查；本次未重复执行",
                        True,
                    )
                status, detail = execute_delivery(root, policy)
                return decision(status, detail, active)

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
    if os.environ.get("LEXIFLOW_DELIVERY_HOOK_DISABLE") == "1":
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
