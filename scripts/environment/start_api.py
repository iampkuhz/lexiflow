"""交互式本地 API 启动器；未取得确认前不终止监听进程。"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

from scripts.environment.java_exec import command_environment
from scripts.environment.java_runtime import JavaRuntimeError


class StartupBlocked(RuntimeError):
    """本次调用无法安全复用端口。"""


def listener_pids(port: int) -> set[int]:
    """仅检查 TCP 监听者，不读取出站连接或完整命令行。"""
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return set()
    if result.returncode != 0 or result.stderr:
        raise StartupBlocked("无法可靠查询端口监听进程；请手动检查 lsof。")
    lines = result.stdout.splitlines()
    if not lines or any(not line.isdigit() for line in lines):
        raise StartupBlocked("监听进程信息不完整，未终止任何进程。")
    pids = {int(line) for line in lines}
    if any(pid <= 1 or pid == os.getpid() for pid in pids):
        raise StartupBlocked("拒绝终止系统进程或启动器自身。")
    return pids


def process_identity(pid: int) -> str:
    """以 PID、启动时间、owner 和可执行文件绑定确认，不读取 argv。"""
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "uid=", "-o", "comm="],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    identity = result.stdout.strip()
    if result.returncode != 0 or not identity or result.stderr:
        raise StartupBlocked("进程身份已变化或无法读取，请重新启动后确认。")
    return identity


def assert_bindable(port: int) -> None:
    """同时探测 lsof 不可见的监听者；owner 未知时绝不授权终止。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise StartupBlocked(
                f"127.0.0.1:{port} 仍不可绑定；未启动，请手动检查占用或权限。"
            ) from exc


def prepare_port(port: int) -> None:
    """只询问一次；重验身份后仅向已批准 PID 发信号并等待端口释放。"""
    pids = listener_pids(port)
    if not pids:
        assert_bindable(port)
        return
    identities = {pid: process_identity(pid) for pid in sorted(pids)}
    print(f"端口 {port} 已被以下进程监听（可能不是 LexiFlow）：", flush=True)
    for pid, identity in identities.items():
        safe_identity = "".join(char for char in identity if char.isprintable())
        print(f"  PID {pid}: {safe_identity}", flush=True)
    if not sys.stdin.isatty():
        raise StartupBlocked("非交互环境不会终止进程，请在终端运行启动命令。")
    try:
        answer = input("是否终止上述进程（SIGTERM）并启动 API？[y/N] ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in {"y", "yes"}:
        raise StartupBlocked("已取消启动，保留原进程。")
    current = listener_pids(port)
    if not current.issubset(pids):
        raise StartupBlocked("端口出现新的监听进程，请重新启动并确认。")
    for pid in sorted(current):
        if process_identity(pid) != identities[pid]:
            raise StartupBlocked("进程身份发生变化，未终止该进程；请重新确认。")
    for pid in sorted(current):
        if pid not in listener_pids(port):
            continue
        if process_identity(pid) != identities[pid]:
            raise StartupBlocked("进程身份发生变化，停止处理。")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise StartupBlocked(f"无权终止 PID {pid}，不会提权或启动 API。") from exc
        print(f"已向 PID {pid} 发送 SIGTERM，等待端口释放…", flush=True)
    deadline = time.monotonic() + 10
    while remaining := listener_pids(port):
        if not remaining.issubset(pids):
            raise StartupBlocked("端口被新进程占用，未终止新进程，请重新确认。")
        if time.monotonic() >= deadline:
            raise StartupBlocked(
                "10 秒内端口未释放；不会强制 kill -9，请手动停止后重试。"
            )
        time.sleep(0.1)
    assert_bindable(port)


def main(arguments: Sequence[str] | None = None) -> int:
    """仅在端口预检成功后启动固定的本地 API 命令。"""
    parser = argparse.ArgumentParser(
        description="本机 API 启动；端口冲突时交互确认是否终止旧进程。"
    )
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument(
        "--database-url",
        help="可选 JDBC 地址；也可使用 SPRING_DATASOURCE_URL 环境变量。",
    )
    args = parser.parse_args(arguments)
    if not 1 <= args.port <= 65535:
        parser.error("--port 必须为 1 至 65535")
    root = Path(__file__).resolve().parents[2]
    try:
        environment = command_environment(root)
        if args.database_url is not None:
            environment["SPRING_DATASOURCE_URL"] = args.database_url
        prepare_port(args.port)
        print(f"启动 LexiFlow API：http://127.0.0.1:{args.port}", flush=True)
        command = [
            str(root / "backend/gradlew"),
            "-p",
            str(root / "backend"),
            ":apps:api:bootRun",
            f"--args=--server.address=127.0.0.1 --server.port={args.port}",
        ]
        os.execvpe(command[0], command, environment)
    except (
        StartupBlocked,
        JavaRuntimeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        # 不打印任意子进程输出、环境值或命令 argv。
        message = (
            str(exc)
            if isinstance(exc, (StartupBlocked, JavaRuntimeError))
            else "本机工具执行失败，请检查 lsof、ps、Gradle 与运行权限。"
        )
        print(f"BLOCKED: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("BLOCKED: 用户取消启动。", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
