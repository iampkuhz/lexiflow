"""真实本机 socket 探针夹具；非 Linux 单测仅替换 coreutils timeout 执行器。"""

import os
import pathlib
import socketserver
import subprocess
import sys
import tempfile
import threading


class HealthProbeFixture:
    """执行产品 Bash 传输，响应内容与延迟由测试决定，不提供产品覆盖入口。"""

    def __init__(self, script, chunks, env=None):
        self.directory = tempfile.TemporaryDirectory()
        self.stop = threading.Event()
        self.requests = []
        self.env = env or {}
        owner = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                request = b""
                try:
                    while b"\r\n\r\n" not in request and len(request) < 4096:
                        block = self.request.recv(1024)
                        if not block:
                            return
                        request += block
                    owner.requests.append(request)
                    for delay, data in chunks:
                        if owner.stop.wait(delay):
                            return
                        self.request.sendall(data)
                except OSError:
                    return

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        directory = pathlib.Path(self.directory.name)
        # macOS 不自带 GNU timeout。此 shim 验证固定参数及 deadline 接线；
        # GNU timeout 本身由隔离 Linux 容器的真实回归验证，不把 shim 当实现证据。
        timer = directory / "timeout"
        timer.write_text(
            f"#!{sys.executable}\n"
            "import os, signal, subprocess, sys\n"
            "if sys.argv[1:3] != ['--kill-after=1s', '2s']: sys.exit(126)\n"
            "p = subprocess.Popen(sys.argv[3:], start_new_session=True)\n"
            "try: sys.exit(p.wait(timeout=2))\n"
            "except subprocess.TimeoutExpired:\n"
            " os.killpg(p.pid, signal.SIGKILL)\n"
            " p.wait()\n"
            " sys.exit(124)\n"
        )
        timer.chmod(0o755)
        self.script = directory / "run-container-command.sh"
        self.script.write_text(
            script.read_text()
            .replace("/usr/bin/timeout", str(timer))
            .replace(
                "/dev/tcp/127.0.0.1/8080",
                f"/dev/tcp/127.0.0.1/{self.server.server_address[1]}",
            )
        )

    def run(self, args=()):
        return subprocess.run(
            ["/bin/sh", str(self.script), "health", *args],
            capture_output=True,
            text=True,
            env={**os.environ, **self.env},
            timeout=4,
            check=False,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.directory.cleanup()
