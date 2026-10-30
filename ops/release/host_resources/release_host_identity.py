"""从系统来源解析宿主与用户摘要，不泄露原始机器标识。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import time

SCHEME = "lexiflow.host-binding.v1"
_UUID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_MACHINE_ID = re.compile(r"^[0-9a-f]{32}$")


class HostIdentityError(ValueError):
    """固定且不含敏感内容的宿主解析错误。"""


def _validated_darwin_id(text: str) -> str:
    match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"\r\n]+)"', text)
    value = match.group(1) if match else ""
    if not _UUID.fullmatch(value) or value.replace("-", "").lower() == "0" * 32:
        raise HostIdentityError("HOST_IDENTITY_SOURCE_INVALID")
    return value.lower()


def _validated_linux_id(raw: bytes, info: os.stat_result) -> str:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_size < 1
        or info.st_size > 128
    ):
        raise HostIdentityError("HOST_IDENTITY_SOURCE_INVALID")
    try:
        value = raw.decode("ascii").strip()
    except UnicodeError:
        raise HostIdentityError("HOST_IDENTITY_SOURCE_INVALID") from None
    if not _MACHINE_ID.fullmatch(value) or value == "0" * 32:
        raise HostIdentityError("HOST_IDENTITY_SOURCE_INVALID")
    return value


def _stable_system_id() -> tuple[str, str]:
    if sys.platform == "darwin":
        process = None
        selector = None
        try:
            process = subprocess.Popen(
                ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert process.stdout is not None
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 5
            output = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise HostIdentityError("HOST_IDENTITY_SOURCE_UNAVAILABLE")
                chunk = os.read(process.stdout.fileno(), min(8192, 65537 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 65536:
                    raise HostIdentityError("HOST_IDENTITY_SOURCE_INVALID")
            if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                raise HostIdentityError("HOST_IDENTITY_SOURCE_UNAVAILABLE")
        except HostIdentityError:
            raise
        except (OSError, subprocess.SubprocessError):
            raise HostIdentityError("HOST_IDENTITY_SOURCE_UNAVAILABLE") from None
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if selector is not None:
                selector.close()
            if process is not None and process.stdout is not None:
                process.stdout.close()
        try:
            text = output.decode("utf-8", errors="strict")
        except UnicodeError:
            raise HostIdentityError("HOST_IDENTITY_SOURCE_INVALID") from None
        return "darwin-ioplatformuuid", _validated_darwin_id(text)
    if sys.platform.startswith("linux"):
        fd = None
        try:
            fd = os.open(
                "/etc/machine-id",
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            info = os.fstat(fd)
            raw = os.read(fd, 129)
            value = _validated_linux_id(raw, info)
        except HostIdentityError:
            raise
        except (OSError, UnicodeError):
            raise HostIdentityError("HOST_IDENTITY_SOURCE_UNAVAILABLE") from None
        finally:
            if fd is not None:
                os.close(fd)
        return "linux-machine-id", value
    raise HostIdentityError("HOST_IDENTITY_PLATFORM_UNSUPPORTED")


def resolve_host_binding() -> dict[str, str]:
    """以稳定系统标识和实际用户派生不透明宿主绑定。"""
    source, identifier = _stable_system_id()
    uid = os.getuid()
    if type(uid) is not int or uid < 0:
        raise HostIdentityError("HOST_IDENTITY_UID_INVALID")
    payload = f"lexiflow-host-binding-v1\0{source}\0{identifier}\0uid={uid}".encode(
        "ascii"
    )
    return {"scheme": SCHEME, "sha256": hashlib.sha256(payload).hexdigest()}


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--json"]):
        raise SystemExit("HOST_IDENTITY_ARGUMENTS_INVALID")
    try:
        print(json.dumps(resolve_host_binding(), sort_keys=True, separators=(",", ":")))
    except HostIdentityError as exc:
        raise SystemExit(str(exc)) from None
