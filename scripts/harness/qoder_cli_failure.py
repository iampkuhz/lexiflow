"""Bounded Qoder failure diagnostics; never export prompts, credentials or logs."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_ACTIONS = {
    "authentication": "refresh-authentication",
    "quota": "check-account-plan-and-credits",
    "billing_access_rejected": "check-account-plan-and-credits",
    "billing_check_required": "check-account-plan-and-credits",
    "access_denied": "check-account-or-model-access",
    "invalid_input": "correct-input-or-configuration",
    "transient_service": "parent-decides-bounded-recovery",
    "runtime_failure": "inspect-failure-before-recovery",
}
_ACCESS_BLOCKERS = frozenset(_ACTIONS) - {
    "invalid_input", "transient_service", "runtime_failure"
}


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 <= value < 10**9:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{1,9}", value):
        return int(value)
    return None


def _read_tail(path: Path, limit: int) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            return b""
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - limit))
            return stream.read(limit)
    except OSError:
        return b""


def _json(value: str) -> Any:
    try:
        return json.loads(value)
    except (ValueError, TypeError, RecursionError):
        return None


def _details(value: Any, depth: int = 0) -> tuple[int | None, int | None, bool]:
    if depth > 4:
        return None, None, False
    if isinstance(value, str):
        return _details(_json(value), depth + 1)
    if not isinstance(value, dict):
        return None, None, False
    code = _number(value.get("error_code", value.get("code")))
    status = _number(value.get("http_status", value.get("status_code")))
    if status is not None and not 100 <= status <= 599:
        status = None
    pricing = False
    link = value.get("pricingUrl")
    if isinstance(link, str):
        try:
            url = urlparse(link)
            pricing = url.scheme == "https" and url.netloc == "qoder.com" and url.path == "/pricing"
        except ValueError:
            pass
    for key in ("message", "body", "error"):
        child_code, child_status, child_pricing = _details(value.get(key), depth + 1)
        code = code if code is not None else child_code
        status = status if status is not None else child_status
        pricing |= child_pricing
    return code, status, pricing


def _matched_transport_details(
    pid: int, session_id: str, cwd: Path, started_at: float,
    log_root: Path | None = None,
) -> tuple[int | None, int | None, bool]:
    """Only read a matching CLI run's metadata/transport events, not sessions."""
    root = log_root or Path.home() / ".qoder/logs/runs"
    if any(path.is_symlink() for path in (root, *root.parents)):
        return None, None, False
    try:
        candidates = list(root.glob(f"*-p{pid}"))
    except OSError:
        return None, None, False
    if len(candidates) > 4:
        return None, None, False
    matches = []
    for directory in candidates:
        if directory.is_symlink():
            continue
        manifest = _json(_read_tail(directory / "manifest.json", 65536).decode(errors="replace"))
        if not isinstance(manifest, dict) or manifest.get("pid") != pid:
            continue
        argv = manifest.get("argv")
        if not isinstance(argv, list) or not any(
            item in ("--session-id", "--resume") and index + 1 < len(argv)
            and argv[index + 1] == session_id for index, item in enumerate(argv)
        ):
            continue
        try:
            timestamp = datetime.fromisoformat(manifest["started_at"]).timestamp()
            if abs(timestamp - started_at) > 30 or Path(manifest["cwd"]).resolve() != cwd.resolve():
                continue
        except (KeyError, TypeError, ValueError, OSError):
            continue
        matches.append(directory)
    if len(matches) != 1:
        return None, None, False
    lines = _read_tail(matches[0] / "qodercli.log", 131072).decode(errors="replace").splitlines()
    for line in reversed(lines):
        if f"session={session_id} " not in line or "model.request.attempt_failed " not in line:
            continue
        status_match = re.search(r"\berror_status=(\d{3})\b", line)
        status = int(status_match[1]) if status_match else None
        marker = "error_message="
        try:
            message, _ = json.JSONDecoder().raw_decode(line.split(marker, 1)[1])
            # The structured transport message contains a JSON service body.
            body, _ = json.JSONDecoder().raw_decode(message[message.index("{"):])
            code, _, pricing = _details(body)
        except (ValueError, IndexError, AttributeError, TypeError, RecursionError):
            code, pricing = None, False
        return code, status, pricing
    return None, None, False


def summarize_cli_failure(
    stdout_path: Path, exit_code: int, *, pid: int | None = None,
    session_id: str = "", cwd: Path | None = None, started_at: float | None = None,
    log_root: Path | None = None,
) -> dict[str, Any]:
    content = _read_tail(stdout_path, 1048576).decode(errors="replace")
    decoded = _json(content)
    messages = decoded if isinstance(decoded, list) else [decoded]
    if decoded is None:
        messages = [_json(line) for line in content.splitlines()]
    results = [item for item in messages if isinstance(item, dict) and item.get("type") == "result"]
    result = results[-1] if results else {}
    result_code = _number(result.get("error_code"))
    # Normalized Result codes and raw transport service codes are not interchangeable.
    code, status, pricing = _details({key: value for key, value in result.items() if key != "error_code"})
    errors = result.get("errors")
    for error in errors if isinstance(errors, list) else []:
        child_code, child_status, child_pricing = _details(error)
        code = code if code is not None else child_code
        status = status if status is not None else child_status
        pricing |= child_pricing
    source = "cli-result"
    if (code is None or status is None) and pid is not None and cwd is not None and started_at is not None:
        extra_code, extra_status, extra_pricing = _matched_transport_details(
            pid, session_id, cwd, started_at, log_root,
        )
        if extra_code is not None or extra_status is not None:
            code = code if code is not None else extra_code
            status = status if status is not None else extra_status
            pricing |= extra_pricing
            source = "cli-result-and-matched-transport-log"
    if result_code in {105, 100401} or (result_code is None and exit_code == 41) or status == 401:
        category = "authentication"
    elif result_code in {110, 113, 114, 115, 116, 117, 118, 119, 122}:
        category = "quota"
    elif pricing:
        category = "billing_access_rejected" if status == 403 else "billing_check_required"
    elif status == 403 or result_code == 100403:
        category = "access_denied"
    elif result_code in {406, 416, 430, 48716, 80411, 80412} or exit_code in {42, 52}:
        category = "invalid_input"
    elif result_code in {500, 10408, 10500} or status is not None and status >= 500:
        category = "transient_service"
    else:
        category = "runtime_failure"
    usage = result.get("usage")
    zero_tokens = isinstance(usage, dict) and all(
        not isinstance(usage.get(key), bool) and usage.get(key) == 0
        for key in ("input_tokens", "output_tokens")
    )
    subtype = result.get("subtype")
    return {
        "category": category, "result_error_code": result_code, "service_code": code, "http_status": status,
        "result_subtype": subtype if isinstance(subtype, str) and subtype in {"success", "error_during_execution", "error_max_turns"} else "unknown",
        "retryable": category == "transient_service",
        "requires_external_change": category in _ACCESS_BLOCKERS,
        "action": _ACTIONS[category], "diagnostic_source": source,
        "zero_token_turn": zero_tokens,
    }


def access_blocked(failure: Any) -> bool:
    return (isinstance(failure, dict) and isinstance(failure.get("category"), str)
            and failure["category"] in _ACCESS_BLOCKERS)


def compact_failure_signal(failure: Any) -> str:
    """Rebuild from allowlisted enums/numbers, never interpolate raw error text."""
    if not isinstance(failure, dict):
        return ""
    category = failure.get("category")
    if not isinstance(category, str) or category not in _ACTIONS:
        category = "runtime_failure"
    code = _number(failure.get("service_code"))
    result_code = _number(failure.get("result_error_code"))
    status = _number(failure.get("http_status"))
    return (f"failure: category={category} result_error_code={result_code} service_code={code} http_status={status} "
            f"retryable={str(category == 'transient_service').lower()} action={_ACTIONS[category]}")
