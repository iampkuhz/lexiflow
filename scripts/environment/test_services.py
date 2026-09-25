"""交付专用临时 PostgreSQL/Redis lease；只管理本次资源，不接触开发服务。"""

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

POLICY_PATH = "harness/test-services.json"
OWNER_LABEL = "io.lexiflow.test-lease"
SERVICES = {
    "postgres-test-jdbc-url": "postgres",
    "redis-test-endpoint": "redis",
}
ENV_KEYS = {
    "postgres": "LEXIFLOW_POSTGRES_TEST_JDBC_URL",
    "redis": "LEXIFLOW_REDIS_TEST_ENDPOINT",
}


class TestServicesError(ValueError):
    """隔离资源无法准备或无法确认清理；调用方只能返回 BLOCKED，不能 PASS。"""


def _command(args: list[str], timeout: int = 10, *, check: bool = True):
    """取消时回收 Podman 客户端；服务端资源由 lease finally 按归属清理。"""
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


def _policy(root: Path) -> dict:
    try:
        data = json.loads((root / POLICY_PATH).read_text())
    except (OSError, ValueError) as exc:
        raise TestServicesError(f"无法读取 {POLICY_PATH}：{exc}") from exc
    if (
        not isinstance(data, dict)
        or data.get("provider") != "podman-ephemeral"
        or data.get("pull") != "never"
    ):
        raise TestServicesError(f"{POLICY_PATH} 只允许 podman-ephemeral / pull=never")
    if data.get("images") != {
        "postgres": "docker.io/library/postgres:17",
        "redis": "docker.io/library/redis:7",
    }:
        raise TestServicesError(
            f"{POLICY_PATH} 必须声明本项目 PostgreSQL 17 / Redis 7 镜像"
        )
    timeout = data.get("readiness_timeout_seconds")
    if type(timeout) is not int or not 1 <= timeout <= 30:
        raise TestServicesError(f"{POLICY_PATH} readiness_timeout_seconds 必须为 1..30")
    return data


def _local_engine() -> None:
    """只允许本机 Unix socket 或 loopback VM；不在远端引擎创建资源。"""
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


@contextlib.contextmanager
def _cleanup_grace():
    """短暂延后终止信号，以有界清理完成代替取消时泄漏测试容器。"""
    remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 0)
    started = time.monotonic()
    handlers = {
        sig: signal.signal(sig, signal.SIG_IGN)
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if remaining:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, remaining - (time.monotonic() - started)),
                interval,
            )


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
    ident = info["Id"]
    if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{64}", ident):
        raise TestServicesError(f"容器 {name} 未返回有效的精确 ID，拒绝删除")
    _command(["rm", "--force", "--time", "1", ident], 10)
    if _command(["container", "exists", ident], 3, check=False).returncode != 1:
        raise TestServicesError(f"无法确认临时容器 {name} 已删除")
    return {"name": name, "id": ident, "removed": True}


def _create(
    resource: dict,
    lease: str,
    directory: Path,
    timeout: int,
    progress: Callable[[str], None],
) -> str:
    service = resource["service"]
    port = 5432 if service == "postgres" else 6379
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
        str(directory / f"{service}.cid"),
        "--memory",
        "512m" if service == "postgres" else "128m",
        "--publish",
        f"127.0.0.1::{port}",
    ]
    if service == "postgres":
        args += [
            "--tmpfs",
            "/var/lib/postgresql/data:rw,size=256m",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e",
            "POSTGRES_DB=lexiflow_hook_test",
        ]
    args += [resource["image_id"]]
    if service == "redis":
        args += ["redis-server", "--save", "", "--appendonly", "no"]
    progress(f"创建本次隔离 {service} 测试容器（随机本机端口、无持久卷）")
    resource["id"] = _command(args).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", resource["id"]):
        raise TestServicesError(f"{service} 创建未返回有效 ID，进入归属清理")
    _command(["start", resource["id"]])
    mapping = _command(["port", resource["id"], str(port)]).stdout.strip()
    if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", mapping):
        raise TestServicesError(f"{service} 未返回唯一 loopback 端口，拒绝用于测试")
    resource["port"] = int(mapping.rsplit(":", 1)[1])
    if not 1 <= resource["port"] <= 65535:
        raise TestServicesError(f"{service} 返回端口越界，拒绝用于测试")
    probe = (
        ["pg_isready", "-U", "postgres", "-d", "lexiflow_hook_test"]
        if service == "postgres"
        else ["redis-cli", "ping"]
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _command(["exec", resource["id"], *probe], 3, check=False)
        if result.returncode == 0 and (
            service != "redis" or result.stdout.strip() == "PONG"
        ):
            resource["ready"] = True
            progress(f"隔离 {service} readiness PASS")
            if service == "postgres":
                return f"jdbc:postgresql://127.0.0.1:{resource['port']}/lexiflow_hook_test?user=postgres"
            return mapping
        time.sleep(0.2)
    raise TestServicesError(
        f"隔离 {service} 在 {timeout} 秒内未就绪（{resource['name']}）"
    )


@contextlib.contextmanager
def isolated_test_services(
    root: Path, required: set[str], progress: Callable[[str], None]
) -> Iterator[dict[str, str]]:
    """按检查声明准备服务；忽略外部测试地址，临时绑定环境，始终精确清理。"""
    needed = [
        service for requirement, service in SERVICES.items() if requirement in required
    ]
    if not needed:
        yield {}
        return
    policy = _policy(root)
    # 所有镜像都先确认；不因缺第二个镜像而先创建一个不必要的容器。
    images = {}
    _local_engine()
    for service in needed:
        tag = policy["images"][service]
        result = _command(
            ["image", "inspect", "--format", "{{.Id}}", tag], 5, check=False
        )
        ident = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", ident):
            raise TestServicesError(
                f"本机缺少可用镜像 {tag}；请显式准备镜像（Hook 不自动 pull）"
            )
        images[service] = ident
    lease = uuid.uuid4().hex
    directory = root / "tmp/quality/test-services" / lease
    directory.mkdir(parents=True)
    resources = []
    cleanup = []
    values = {}
    saved = {}
    failed = False
    try:
        for service in needed:
            resource = {
                "service": service,
                "name": f"lexiflow-check-{service}-{lease}",
                "image_id": images[service],
                "ready": False,
            }
            # 创建前保存归属；即使 CLI 在返回 ID 前中断，也可按名字和 lease 清理。
            resources.append(resource)
            (directory / "lease.json").write_text(
                json.dumps({"lease": lease, "resources": resources}, indent=2)
            )
            values[ENV_KEYS[service]] = _create(
                resource,
                lease,
                directory,
                policy["readiness_timeout_seconds"],
                progress,
            )
        saved = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        yield values
    except BaseException:
        failed = True
        raise
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        errors = []
        with _cleanup_grace():
            for resource in reversed(resources):
                try:
                    cleanup.append(_remove_owned(resource, lease))
                    progress(f"隔离 {resource['service']} 清理 PASS")
                except (OSError, ValueError, KeyError, IndexError) as exc:
                    errors.append(str(exc))
                    cleanup.append(
                        {"name": resource["name"], "removed": False, "error": str(exc)}
                    )
                    progress(f"隔离 {resource['service']} 清理 BLOCKED：{exc}")
            receipt = directory / "cleanup.json"
            receipt.write_text(
                json.dumps(
                    {
                        "lease": lease,
                        "resources": resources,
                        "cleanup": cleanup,
                        "cleanup_verified": not errors,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            progress(f"测试资源记录：{receipt.relative_to(root)}")
        if errors and not failed:
            raise TestServicesError("临时测试资源未确认清理；" + "；".join(errors))
