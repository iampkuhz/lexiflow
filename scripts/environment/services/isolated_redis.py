"""交付专用临时 Redis lease；只管理本次资源，不接触开发服务。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

_ACTIVE_BINDINGS: dict[tuple[str, str], dict] = {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _open_lease_directory(root: Path, lease: str, *, create: bool = False) -> int:
    if not re.fullmatch(r"[0-9a-f]{32}", lease):
        raise TestServicesError("lease 格式无效")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(root, flags)
    try:
        for part in ("tmp", "quality", "test-services", lease):
            if create:
                try:
                    os.mkdir(part, 0o700 if part == lease else 0o755, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    if part == lease:
                        raise TestServicesError(
                            "lease receipt directory already exists"
                        )
            child = os.open(part, flags, dir_fd=fd)
            info = os.fstat(child)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child)
                raise TestServicesError("lease receipt path is not a directory")
            if part == lease and (
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700
            ):
                os.close(child)
                raise TestServicesError(
                    "lease receipt directory ownership or mode invalid"
                )
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _publish_receipt(root: Path, lease: str, name: str, value: dict) -> str:
    """原子、不可覆盖地发布由内部生命周期产生的收据。"""
    if name not in {"binding.json", "cleanup-receipt.json"}:
        raise TestServicesError("receipt name is not registered")
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    dfd = _open_lease_directory(root, lease)
    temp = f".{name}.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=dfd,
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temp, name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
        os.fsync(dfd)
        os.unlink(temp, dir_fd=dfd)
        os.fsync(dfd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp, dir_fd=dfd)
        os.close(dfd)
    return hashlib.sha256(data).hexdigest()


def _read_receipt(root: Path, lease: str, name: str) -> bytes:
    if name not in {"binding.json", "cleanup-receipt.json"}:
        raise TestServicesError("receipt name is not registered")
    dfd = _open_lease_directory(root, lease)
    try:
        fd = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=dfd,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o400
                or info.st_nlink != 1
                or info.st_size > 4096
            ):
                raise TestServicesError(
                    "receipt file type, owner, mode, link count or size invalid"
                )
            chunks = bytearray()
            while len(chunks) <= 4096:
                block = os.read(fd, min(1024, 4097 - len(chunks)))
                if not block:
                    return bytes(chunks)
                chunks.extend(block)
            raise TestServicesError("receipt exceeds size limit")
        finally:
            os.close(fd)
    finally:
        os.close(dfd)


def active_binding(root: Path, endpoint: str) -> dict | None:
    """只返回本进程当前真实服务上下文注册的精确 endpoint 绑定。"""
    value = _ACTIVE_BINDINGS.get((str(root.resolve()), _digest(endpoint)))
    return dict(value) if value is not None else None


def verify_binding_record(root: Path, binding: dict) -> bool:
    """核对ready时产生的不可变绑定记录及精确资源身份。"""
    try:
        lease = binding.get("lease")
        if (
            not isinstance(lease, str)
            or not re.fullmatch(r"[0-9a-f]{32}", lease)
            or binding.get("directory") != f"tmp/quality/test-services/{lease}"
        ):
            return False
        if (
            not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("resource_id", "")))
            or not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("image_id", "")))
            or not re.fullmatch(
                r"[0-9a-f]{64}", str(binding.get("endpoint_sha256", ""))
            )
            or not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("binding_sha256", "")))
        ):
            return False
        bind_bytes = _read_receipt(root, lease, "binding.json")
        if hashlib.sha256(bind_bytes).hexdigest() != binding["binding_sha256"]:
            return False
        bind = json.loads(bind_bytes)
        return (
            set(bind)
            == {"schema", "lease", "resource_id", "image_id", "endpoint_sha256"}
            and set(binding)
            == {
                "schema",
                "lease",
                "resource_id",
                "image_id",
                "endpoint_sha256",
                "binding_sha256",
                "directory",
            }
            and bind.get("schema") == "lexiflow.redis-lease-binding.v1"
            and bind.get("endpoint_sha256") == binding["endpoint_sha256"]
            and bind.get("resource_id") == binding["resource_id"]
            and bind.get("lease") == binding["lease"]
            and bind.get("image_id") == binding["image_id"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def verify_binding_receipts(root: Path, binding: dict) -> bool:
    """核对不可变绑定及成功清理收据的摘要和精确资源关联。"""
    try:
        if not verify_binding_record(root, binding):
            return False
        cleanup_bytes = _read_receipt(root, binding["lease"], "cleanup-receipt.json")
        cleanup = json.loads(cleanup_bytes)
        return (
            set(cleanup)
            == {"schema", "lease", "resource_id", "binding_sha256", "removed"}
            and cleanup.get("schema") == "lexiflow.redis-lease-cleanup.v1"
            and cleanup.get("binding_sha256") == binding["binding_sha256"]
            and cleanup.get("resource_id") == binding["resource_id"]
            and cleanup.get("lease") == binding["lease"]
            and cleanup.get("removed") is True
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
        raise TestServicesError(f"临时容器 {name} 已在本次删除前消失，不能证明清理归属")
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
    if ident != resource.get("id"):
        raise TestServicesError(f"容器 {name} 的实际 ID 与本次绑定不匹配，拒绝删除")
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
    root = root.resolve()
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
    lease_fd = _open_lease_directory(root, lease, create=True)
    os.close(lease_fd)
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
    binding_sha256: str | None = None
    try:
        value = _create(resource, lease, directory, progress)
        binding_record = {
            "schema": "lexiflow.redis-lease-binding.v1",
            "lease": lease,
            "resource_id": resource["id"],
            "image_id": image_id,
            "endpoint_sha256": _digest(value),
        }
        binding_sha256 = _publish_receipt(root, lease, "binding.json", binding_record)
        binding = {
            **binding_record,
            "binding_sha256": binding_sha256,
            "directory": str(directory.relative_to(root.resolve())),
        }
        _ACTIVE_BINDINGS[(str(root.resolve()), binding["endpoint_sha256"])] = binding
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
            if resource.get("ready") is not True or not resource.get("id"):
                raise TestServicesError("资源未ready，不能签发成功清理收据")
            if binding_sha256 is None:
                raise TestServicesError(
                    "binding receipt unavailable; cleanup cannot authorize evidence"
                )
            _publish_receipt(
                root,
                lease,
                "cleanup-receipt.json",
                {
                    "schema": "lexiflow.redis-lease-cleanup.v1",
                    "lease": lease,
                    "resource_id": resource["id"],
                    "binding_sha256": binding_sha256,
                    "removed": True,
                },
            )
            progress("隔离 redis 清理 PASS")
        except (OSError, ValueError, KeyError, IndexError) as exc:
            error = str(exc)
            cleanup.append({"name": resource["name"], "removed": False, "error": error})
            progress(f"隔离 redis 清理 BLOCKED：{exc}")
        if "value" in locals():
            _ACTIVE_BINDINGS.pop((str(root.resolve()), _digest(value)), None)
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
