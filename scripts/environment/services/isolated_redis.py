"""交付专用临时 Redis lease；只管理本次资源，不接触开发服务。"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

IMAGE = "docker.io/library/redis:7"
OWNER_LABEL = "io.lexiflow.test-lease"
SERVICE = "redis-test-endpoint"
ENV_KEY = "LEXIFLOW_REDIS_TEST_ENDPOINT"


class TestServicesError(ValueError):
    """隔离资源无法准备或无法确认清理；调用方只能返回 BLOCKED，不能 PASS。"""


def _command(args: list[str], timeout: float = 10, *, check: bool = True):
    try:
        process = subprocess.Popen(
            ["podman", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise TestServicesError(
            f"无法启动 podman：{exc.strerror}；请显式准备容器运行时"
        ) from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as exc:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=2)
        if isinstance(exc, subprocess.TimeoutExpired):
            raise TestServicesError(f"podman {args[0]} 超过 {timeout} 秒") from exc
        raise
    result = subprocess.CompletedProcess(
        ["podman", *args], process.returncode, stdout, stderr
    )
    if check and result.returncode:
        raise TestServicesError(
            f"podman {args[0]} 退出 {result.returncode}：{stderr.strip()[-1200:]}"
        )
    return result


def _local_engine() -> None:
    uri = os.environ.get("CONTAINER_HOST", "")
    if not uri:
        connections = json.loads(
            _command(["system", "connection", "list", "--format", "json"], 5).stdout
        )
        if not isinstance(connections, list) or not all(
            isinstance(item, dict) for item in connections
        ):
            raise TestServicesError("podman 未返回有效连接列表，未创建测试资源")
        selected = os.environ.get("CONTAINER_CONNECTION")
        candidates = (
            [item for item in connections if item.get("Name") == selected]
            if selected
            else [item for item in connections if item.get("Default")]
        )
        if not candidates and sys.platform == "linux" and not selected:
            _command(["info", "--format", "{{.Host.Hostname}}"], 5)
            return
        if len(candidates) != 1:
            raise TestServicesError(
                "无法确定本机 Podman 连接；请显式准备并启动本机容器引擎"
            )
        uri = candidates[0].get("URI", "")
    if not isinstance(uri, str):
        raise TestServicesError("podman 连接 URI 无效，未创建测试资源")
    address = urlsplit(uri)
    local = (
        address.scheme == "unix" and not address.netloc and bool(address.path)
    ) or (
        address.scheme == "ssh"
        and address.hostname in {"127.0.0.1", "::1", "localhost"}
    )
    if not local:
        raise TestServicesError(
            "当前 Podman 连接不是本机 Unix socket 或 loopback VM；拒绝在远端创建测试资源"
        )
    _command(["info", "--format", "{{.Host.Hostname}}"], 5)


def _remove_owned(resource: dict, lease: str) -> dict:
    name = resource["name"]
    exists = _command(["container", "exists", name], 3, check=False)
    if exists.returncode == 1:
        return {"name": name, "removed": True, "already_absent": True}
    if exists.returncode != 0:
        raise TestServicesError(
            f"无法确认临时容器 {name} 的状态：{exists.stderr[-800:]}"
        )
    info = json.loads(_command(["container", "inspect", name], 3).stdout)[0]
    if info.get("Config", {}).get("Labels", {}).get(OWNER_LABEL) != lease:
        raise TestServicesError(f"容器 {name} 的 lease 标签不匹配，拒绝删除")
    ident = info.get("Id")
    if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{64}", ident):
        raise TestServicesError(f"容器 {name} 未返回有效的精确 ID，拒绝删除")
    _command(["rm", "--force", "--time", "1", ident], 10)
    if _command(["container", "exists", ident], 3, check=False).returncode != 1:
        raise TestServicesError(f"无法确认临时容器 {name} 已删除")
    return {"name": name, "id": ident, "removed": True}


def _create(
    resource: dict, lease: str, directory: Path, progress: Callable[[str], None]
) -> str:
    args = [
        "create",
        "--pull=never",
        "--image-volume=ignore",
        "--restart=no",
        "--name",
        resource["name"],
        "--label",
        f"{OWNER_LABEL}={lease}",
        "--cidfile",
        str(directory / "redis.cid"),
        "--memory",
        "128m",
        "--publish",
        "127.0.0.1::6379",
        resource["image_id"],
        "redis-server",
        "--save",
        "",
        "--appendonly",
        "no",
    ]
    progress("创建本次隔离 Redis 测试容器（随机本机端口、无持久卷）")
    resource["id"] = _command(args).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", resource["id"]):
        raise TestServicesError("redis 创建未返回有效 ID，进入归属清理")
    _command(["start", resource["id"]])
    mapping = _command(["port", resource["id"], "6379"]).stdout.strip()
    if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", mapping):
        raise TestServicesError("redis 未返回唯一 loopback 端口，拒绝用于测试")
    port = int(mapping.rsplit(":", 1)[1])
    if not 1 <= port <= 65535:
        raise TestServicesError("redis 返回端口越界，拒绝用于测试")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = _command(
            ["exec", resource["id"], "redis-cli", "ping"],
            min(3, deadline - time.monotonic()),
            check=False,
        )
        if (
            result.returncode == 0
            and result.stdout.strip() == "PONG"
            and time.monotonic() < deadline
        ):
            resource["ready"] = True
            progress("隔离 redis readiness PASS")
            return mapping
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    raise TestServicesError(f"隔离 redis 在 30 秒内未就绪（{resource['name']}）")


@contextlib.contextmanager
def isolated_test_services(
    root: Path, required: set[str], progress: Callable[[str], None]
) -> Iterator[dict[str, str]]:
    """仅按声明启动 Redis，忽略外部端点，始终按 lease 所有权精确清理。"""
    if SERVICE not in required:
        yield {}
        return
    _local_engine()
    result = _command(
        ["image", "inspect", "--format", "{{.Id}}", IMAGE], 5, check=False
    )
    image_id = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", image_id):
        raise TestServicesError(
            f"本机缺少可用镜像 {IMAGE}；请显式准备镜像（Hook 不自动 pull）"
        )
    lease = uuid.uuid4().hex
    directory = root / "tmp/quality/test-services" / lease
    directory.mkdir(parents=True)
    resource = {
        "service": "redis",
        "name": f"lexiflow-check-redis-{lease}",
        "image_id": image_id,
        "ready": False,
    }
    (directory / "lease.json").write_text(
        json.dumps({"lease": lease, "resources": [resource]}, indent=2)
    )
    saved = os.environ.get(ENV_KEY)
    failed = False
    cleanup = []
    try:
        value = _create(resource, lease, directory, progress)
        os.environ[ENV_KEY] = value
        yield {ENV_KEY: value}
    except BaseException:
        failed = True
        raise
    finally:
        if saved is None:
            os.environ.pop(ENV_KEY, None)
        else:
            os.environ[ENV_KEY] = saved
        error = None
        try:
            cleanup.append(_remove_owned(resource, lease))
            progress("隔离 redis 清理 PASS")
        except (OSError, ValueError, KeyError, IndexError) as exc:
            error = str(exc)
            cleanup.append({"name": resource["name"], "removed": False, "error": error})
            progress(f"隔离 redis 清理 BLOCKED：{exc}")
        (directory / "cleanup.json").write_text(
            json.dumps(
                {
                    "lease": lease,
                    "resources": [resource],
                    "cleanup": cleanup,
                    "cleanup_verified": error is None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        progress(f"测试资源记录：{(directory / 'cleanup.json').relative_to(root)}")
        if error and not failed:
            raise TestServicesError("临时测试资源未确认清理；" + error)
