"""Strict explicit Gate result evidence packet materializer.

LF-TSK-QLT-0007 final rework: Addresses all defects from Rework-1 independent review.
Never infers result fields from free text, stdout, stderr, callbacks, or exit codes.
"""

import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import unicodedata
import uuid

SCHEMA_VERSION = "lexiflow.explicit-evidence-packet.v1"
SNAPSHOT_SCHEMA_VERSION = "lexiflow.changed-file-snapshot.v1"
CODEX_TASK_PROJECTION_SCHEMA_VERSION = (
    "lexiflow.codex-work-package-task-projection.v1"
)
CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION = "lexiflow.codex-main-task-projection.v1"

RESULT_STATUS_VALUES = ("PASS", "BLOCKED", "FAIL")

REQUIRED_RESULT_FIELDS = (
    "status", "changed_files", "validation",
    "acceptance_evidence", "effect_checks", "risks",
)

REQUIRED_RAW_ARTIFACT_KEYS = (
    "task", "completion", "stdout", "stderr",
    "changed_file_snapshot", "diff",
)

REQUIRED_IDENTITY_FIELDS = (
    "parent_session_id", "agent_id", "run_id",
    "session_id", "client", "parent_client",
)

REQUIRED_TASK_IDENTITY_FIELDS = ("task_id", "task_version", "change_version")

ALLOWED_FIELD_SOURCE_BINDINGS = {
    "status": ("explicit-main-agent-review",),
    "changed_files": ("reviewed-snapshot-diff",),
    "validation": ("test-evidence",),
    "acceptance_evidence": ("test-evidence",),
    "effect_checks": ("explicit-main-agent-review",),
    "risks": ("explicit-main-agent-review",),
}

TERMINAL_COMPLETION_STATUSES = ("finished", "failed", "completed")

UUID_V4_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
)
UUID_CANONICAL_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
)

TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
AGENT_ID_PATTERN = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9._-]{0,127}|/[A-Za-z0-9_][A-Za-z0-9._/-]{0,255})$")
TASK_ID_PATTERN = re.compile(r"^LF-TSK-[A-Z]+-[0-9]{4}$")
WORK_PACKAGE_ID_PATTERN = re.compile(
    r"^LF-WP-[A-Z0-9]+(?:-[A-Z0-9]+)*$"
)
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

ISSUER_FIELDS = {"issuer", "authority", "verifier", "role", "authorized_receipt_kinds", "trusted_issuer_packet", "issuer_instance_id"}

TASK_KEYS = {"task_id", "task_version", "change_version", "task_source"}
TASK_SOURCE_KEYS = {"locator", "sha256"}
SUBJECT_KEYS = {"raw_artifacts", "main_agent_attestation", "identity"}
RAW_ARTIFACT_CONTAINER_KEYS = set(REQUIRED_RAW_ARTIFACT_KEYS) | {"tests"}
RAW_ARTIFACT_KEYS = {"locator", "sha256"}
ATTESTATION_KEYS = {"actor_id", "reviewed_at", "result_fields", "field_source_bindings"}
RESULT_FIELD_KEYS = {"status", "changed_files", "validation", "acceptance_evidence", "effect_checks", "risks"}
VALIDATION_KEYS = {"status", "evidence_locator"}
IDENTITY_KEYS = {"parent_session_id", "agent_id", "run_id", "session_id", "client", "parent_client"}
CODEX_MAIN_RAW_TASK_KEYS = {
    "schema_version", "task_id", "task_source", "task_version", "change_version",
    "allowed_files", "forbidden_files", "expected_output", "acceptance_criteria",
    "acceptance_evidence", "validation_command", "goal", "required_context",
    "failure_policy", *IDENTITY_KEYS,
}
SCOPE_KEYS = {"changed_files", "raw_caller_strings", "normalized", "three_way_reconciliation"}
RAW_CALLER_KEYS = {"allowed_files", "forbidden_files"}
NORMALIZED_KEYS = {"allowed_files", "forbidden_files", "canonical_file_claims"}
RECONCILIATION_KEYS = {"changed_outside_allowed", "changed_matching_forbidden", "changed_without_claim", "claims_outside_allowed", "claims_intersecting_forbidden", "status"}
PACKET_KEYS = {
    "schema_version", "publication_id", "task", "subject", "scope",
    "content_fingerprint",
}

CODEX_CALLER_CONTRACT_KEYS = {
    "goal", "work_package_id", "task_ids", "task_versions",
    "change_versions", "estimated_minutes", "primary_owner",
    "contract_boundary", "allowed_files", "forbidden_files",
    "required_context", "expected_outputs_by_task", "acceptance_by_task",
    "validation_commands", "failure_policy", "parent_client",
}
CODEX_RAW_TASK_KEYS = {
    "schema_version", "work_package_id", "task_ids", "target_task_id",
    "task_id", "task_source", "task_version", "change_version",
    "allowed_files", "forbidden_files", "expected_output",
    "acceptance_criteria", "acceptance_evidence", "validation_command",
    "caller_contract", "parent_session_id", "agent_id", "run_id",
    "session_id", "client", "parent_client",
}
CODEX_ACCEPTANCE_KEYS = {"acceptance_criteria", "acceptance_evidence"}


class EvidencePacketError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _check_no_unknown_fields(obj, allowed_keys, path):
    if not isinstance(obj, dict):
        raise EvidencePacketError("STRUCT_INVALID", f"{path} must be a dict")
    unknown = set(obj.keys()) - allowed_keys
    if unknown:
        raise EvidencePacketError("UNKNOWN_FIELD", f"{path} has unknown fields: {sorted(unknown)}")


def _check_no_issuer_fields(obj, path):
    if isinstance(obj, dict):
        found = set(obj.keys()) & ISSUER_FIELDS
        if found:
            raise EvidencePacketError(
                "ISSUER_FIELD_PRESENT",
                f"{path} contains issuer fields: {sorted(found)}",
            )
        for key, value in obj.items():
            _check_no_issuer_fields(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            _check_no_issuer_fields(value, f"{path}[{index}]")


def check_locator_safety_strict(locator):
    if not isinstance(locator, str) or not locator:
        raise EvidencePacketError("LOCATOR_EMPTY", "locator is empty or not a string")
    if os.path.isabs(locator):
        raise EvidencePacketError("LOCATOR_ABSOLUTE", f"locator is absolute: {locator!r}")
    if re.match(r"^[A-Za-z]:", locator):
        raise EvidencePacketError("LOCATOR_ABSOLUTE", f"locator uses an absolute drive path: {locator!r}")
    if "\\" in locator:
        raise EvidencePacketError("LOCATOR_BACKSLASH", f"locator contains backslash: {locator!r}")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in locator):
        raise EvidencePacketError("LOCATOR_CONTROL", f"locator contains control character: {locator!r}")
    if unicodedata.normalize("NFC", locator) != locator:
        raise EvidencePacketError("LOCATOR_UNICODE", f"locator is not NFC: {locator!r}")
    if "," in locator or "*" in locator:
        raise EvidencePacketError("LOCATOR_GLOB", f"locator contains path expression syntax: {locator!r}")
    parts = locator.split("/")
    for part in parts:
        if part in (".", "..", ""):
            raise EvidencePacketError("LOCATOR_DOT_SEGMENT", f"locator has dot/empty segment: {locator!r}")
        if part.lower() == "latest":
            raise EvidencePacketError("LOCATOR_LATEST", f"locator contains 'latest' segment: {locator!r}")
        if "?" in part or "[" in part or "]" in part or "{" in part or "}" in part:
            raise EvidencePacketError("LOCATOR_GLOB", f"locator contains glob syntax: {locator!r}")


def _check_sha256_lowercase_hex(value, path):
    if not isinstance(value, str):
        raise EvidencePacketError("SHA256_NOT_LOWERCASE_HEX", f"{path} is not a string")
    if not SHA256_PATTERN.match(value):
        raise EvidencePacketError("SHA256_NOT_LOWERCASE_HEX", f"{path} is not lowercase hex SHA-256")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _check_path_components_for_symlinks(path):
    parts = path.split(os.sep)
    current = ""
    for part in parts:
        if not part:
            current = os.sep
            continue
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise EvidencePacketError("LOCATOR_SYMLINK_ANCESTOR", f"symlink in path: {current}")


def _open_directory_path_strict(path):
    """Open an absolute directory path without following any component symlink."""
    absolute = os.path.abspath(os.fspath(path))
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd = os.open(os.path.sep, flags)
    try:
        for component in (part for part in absolute.split(os.path.sep) if part):
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def _directory_identity_strict(path, code):
    fd = None
    try:
        fd = _open_directory_path_strict(path)
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode):
            raise EvidencePacketError(code, f"not a directory: {path}")
        return st.st_dev, st.st_ino, st.st_mode
    except EvidencePacketError:
        raise
    except OSError as exc:
        raise EvidencePacketError(code, f"cannot open directory {path}: {exc}")
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _assert_repo_binding(repo_root, expected):
    identity = _directory_identity_strict(repo_root, "REPO_DRIFT")
    if identity[:2] != expected[:2]:
        raise EvidencePacketError(
            "REPO_DRIFT", "repo_root no longer names the bound repository inode"
        )


def _open_or_create_directory_beneath(repo_root, locator, root_identity):
    """Create and open a repository directory using only dir-fd operations."""
    kind, components = _parse_dispatch_path_v1(locator)
    if kind != "exact":
        raise EvidencePacketError(
            "PUBLISH_DIR_INVALID", "publication directory must be an exact path"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    current_fd = _open_directory_path_strict(repo_root)
    created_directories = []
    try:
        root_stat = os.fstat(current_fd)
        if (root_stat.st_dev, root_stat.st_ino) != root_identity[:2]:
            raise EvidencePacketError(
                "REPO_DRIFT", "publication root differs from bound repository"
            )
        for component in components:
            created = False
            try:
                os.mkdir(component, 0o755, dir_fd=current_fd)
                created = True
            except FileExistsError:
                pass
            if created:
                os.fsync(current_fd)
            next_fd = None
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
                if created:
                    os.fsync(next_fd)
                    created_directories.append(component)
            except BaseException:
                if next_fd is not None:
                    try:
                        os.close(next_fd)
                    except OSError:
                        pass
                raise
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        try:
            os.close(current_fd)
        except OSError:
            pass
        raise


def _read_file_bytes_strict(repo_root, locator, root_identity=None):
    check_locator_safety_strict(locator)
    dir_fds = []
    file_fd = None
    close_error = None
    try:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        current_fd = _open_directory_path_strict(repo_root)
        if root_identity is not None:
            root_stat = os.fstat(current_fd)
            if (root_stat.st_dev, root_stat.st_ino) != root_identity[:2]:
                raise EvidencePacketError(
                    "REPO_DRIFT", "artifact read root differs from bound repository"
                )
        dir_fds.append(current_fd)
        parts = locator.split("/")
        for component in parts[:-1]:
            current_fd = os.open(component, directory_flags, dir_fd=current_fd)
            dir_fds.append(current_fd)
        before_open = os.stat(
            parts[-1], dir_fd=current_fd, follow_symlinks=False
        )
        if not stat.S_ISREG(before_open.st_mode):
            raise EvidencePacketError(
                "LOCATOR_NOT_REGULAR", f"locator is not a regular file: {locator!r}"
            )
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        file_fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
        fst = os.fstat(file_fd)
        if (
            not stat.S_ISREG(fst.st_mode)
            or (fst.st_dev, fst.st_ino) != (before_open.st_dev, before_open.st_ino)
        ):
            raise EvidencePacketError(
                "LOCATOR_NOT_REGULAR",
                f"locator changed or is not a regular file: {locator!r}",
            )
        chunks = []
        while True:
            chunk = os.read(file_fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except EvidencePacketError:
        raise
    except OSError as exc:
        raise EvidencePacketError("LOCATOR_UNREADABLE", f"cannot read {locator!r}: {exc}")
    finally:
        if file_fd is not None:
            try:
                os.close(file_fd)
            except OSError as exc:
                close_error = exc
        for directory_fd in reversed(dir_fds):
            try:
                os.close(directory_fd)
            except OSError as exc:
                if close_error is None:
                    close_error = exc
        if close_error is not None and sys.exc_info()[0] is None:
            raise EvidencePacketError(
                "LOCATOR_CLOSE_FAILED", f"failed to close {locator!r}: {close_error}"
            )


def sha256_file_strict(repo_root, locator, root_identity=None):
    return hashlib.sha256(
        _read_file_bytes_strict(repo_root, locator, root_identity)
    ).hexdigest()


def _locator_identity_strict(repo_root, locator, root_identity=None):
    """Return a locator's lstat identity using the same no-symlink walk as reads."""
    check_locator_safety_strict(locator)
    dir_fds = []
    try:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        current_fd = _open_directory_path_strict(repo_root)
        if root_identity is not None:
            root_stat = os.fstat(current_fd)
            if (root_stat.st_dev, root_stat.st_ino) != root_identity[:2]:
                raise EvidencePacketError(
                    "REPO_DRIFT", "locator root differs from bound repository"
                )
        dir_fds.append(current_fd)
        parts = locator.split("/")
        for component in parts[:-1]:
            try:
                current_fd = os.open(
                    component, directory_flags, dir_fd=current_fd
                )
            except FileNotFoundError:
                return None
            dir_fds.append(current_fd)
        try:
            st = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        return st.st_dev, st.st_ino, st.st_mode
    except EvidencePacketError:
        raise
    except OSError as exc:
        raise EvidencePacketError(
            "LOCATOR_UNREADABLE", f"cannot inspect {locator!r}: {exc}"
        )
    finally:
        for directory_fd in reversed(dir_fds):
            try:
                os.close(directory_fd)
            except OSError:
                pass


def canonical_json(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _parse_dispatch_path_v1(pattern):
    if not isinstance(pattern, str) or not pattern:
        raise EvidencePacketError("PATH_EMPTY", "dispatch path is empty")
    if pattern.startswith("/") or re.match(r"^[A-Za-z]:", pattern):
        raise EvidencePacketError("PATH_ABSOLUTE", f"dispatch path is absolute: {pattern!r}")
    if "\\" in pattern:
        raise EvidencePacketError("PATH_BACKSLASH", f"dispatch path contains backslash: {pattern!r}")
    if "," in pattern:
        raise EvidencePacketError("PATH_COMMA", f"dispatch path contains comma: {pattern!r}")
    if pattern.startswith("!") or any(char in pattern for char in ("$", "`", "~")):
        raise EvidencePacketError("PATH_SHELL_SYNTAX", f"dispatch path contains shell syntax: {pattern!r}")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in pattern):
        raise EvidencePacketError("PATH_CONTROL", f"dispatch path contains control character: {pattern!r}")
    if unicodedata.normalize("NFC", pattern) != pattern:
        raise EvidencePacketError("PATH_UNICODE", f"dispatch path is not NFC: {pattern!r}")
    if "?" in pattern or "[" in pattern or "]" in pattern or "{" in pattern or "}" in pattern:
        raise EvidencePacketError("PATH_GLOB_SYNTAX", f"dispatch path contains illegal glob syntax: {pattern!r}")
    parts = pattern.split("/")
    for i, part in enumerate(parts):
        if not part:
            raise EvidencePacketError("PATH_EMPTY_SEGMENT", f"dispatch path has empty segment: {pattern!r}")
        if part in (".", ".."):
            raise EvidencePacketError("PATH_DOT_SEGMENT", f"dispatch path has dot segment: {pattern!r}")
        if part.lower() == "latest":
            raise EvidencePacketError("PATH_LATEST", f"dispatch path contains latest segment: {pattern!r}")
        if "*" in part and part not in ("*", "**"):
            raise EvidencePacketError("PATH_ILLEGAL_WILDCARD", f"dispatch path has partial wildcard: {pattern!r}")
        if part in ("*", "**") and i != len(parts) - 1:
            raise EvidencePacketError("PATH_MIDDLE_WILDCARD", f"dispatch path has wildcard in middle: {pattern!r}")
    if parts[-1] == "*":
        kind = "child"
        prefix = tuple(parts[:-1])
    elif parts[-1] == "**":
        kind = "subtree"
        prefix = tuple(parts[:-1])
    else:
        kind = "exact"
        prefix = tuple(parts)
    if not prefix:
        raise EvidencePacketError("PATH_EMPTY_PREFIX", f"dispatch wildcard needs a literal prefix: {pattern!r}")
    return kind, prefix


def validate_dispatch_path_v1(pattern):
    _parse_dispatch_path_v1(pattern)
    return True


def match_dispatch_path_v1(path, pattern):
    path_kind, path_segments = _parse_dispatch_path_v1(path)
    if path_kind != "exact":
        raise EvidencePacketError("PATH_NOT_EXACT", f"matched path must be exact: {path!r}")
    pattern_kind, prefix = _parse_dispatch_path_v1(pattern)
    if pattern_kind == "exact":
        return path_segments == prefix
    if path_segments[:len(prefix)] != prefix:
        return False
    if pattern_kind == "child":
        return len(path_segments) == len(prefix) + 1
    return len(path_segments) > len(prefix)


def dispatch_path_contains(container, member):
    container_kind, container_prefix = _parse_dispatch_path_v1(container)
    member_kind, member_prefix = _parse_dispatch_path_v1(member)
    if container_kind == "exact":
        return member_kind == "exact" and container_prefix == member_prefix
    if container_kind == "child":
        if member_kind == "exact":
            return (
                member_prefix[:len(container_prefix)] == container_prefix
                and len(member_prefix) == len(container_prefix) + 1
            )
        return member_kind == "child" and member_prefix == container_prefix
    if member_kind == "exact":
        return (
            member_prefix[:len(container_prefix)] == container_prefix
            and len(member_prefix) > len(container_prefix)
        )
    return (
        member_prefix[:len(container_prefix)] == container_prefix
        and len(member_prefix) >= len(container_prefix)
    )


def dispatch_paths_intersect(left, right):
    left_kind, left_prefix = _parse_dispatch_path_v1(left)
    right_kind, right_prefix = _parse_dispatch_path_v1(right)
    if left_kind == "exact":
        return match_dispatch_path_v1("/".join(left_prefix), right)
    if right_kind == "exact":
        return match_dispatch_path_v1("/".join(right_prefix), left)
    if left_kind == "child" and right_kind == "child":
        return left_prefix == right_prefix
    if left_kind == "subtree" and right_kind == "subtree":
        shorter, longer = sorted((left_prefix, right_prefix), key=len)
        return longer[:len(shorter)] == shorter
    if left_kind == "subtree":
        subtree_prefix, child_prefix = left_prefix, right_prefix
    else:
        subtree_prefix, child_prefix = right_prefix, left_prefix
    return (
        child_prefix[:len(subtree_prefix)] == subtree_prefix
        and len(child_prefix) >= len(subtree_prefix)
    )


def dispatch_path_specificity(pattern):
    kind, prefix = _parse_dispatch_path_v1(pattern)
    rank = {"subtree": 0, "child": 1, "exact": 2}[kind]
    return len(prefix), rank


def _reject_redundant_patterns(patterns, path):
    for index, left in enumerate(patterns):
        for right in patterns[index + 1:]:
            if dispatch_path_contains(left, right) or dispatch_path_contains(right, left):
                raise EvidencePacketError(
                    "PATH_REDUNDANT",
                    f"{path} contains overlapping redundant patterns {left!r} and {right!r}",
                )


def validate_result_field_status(value):
    if not isinstance(value, str) or value not in RESULT_STATUS_VALUES:
        raise EvidencePacketError(
            "RESULT_FIELD_INVALID",
            f"status must be one of {RESULT_STATUS_VALUES}, got {value!r}",
        )


def validate_result_field_changed_files(value):
    if not isinstance(value, list):
        raise EvidencePacketError("RESULT_FIELD_INVALID", "changed_files must be a list")
    for item in value:
        if not isinstance(item, str):
            raise EvidencePacketError("RESULT_FIELD_INVALID", "changed_files items must be strings")
        kind, _ = _parse_dispatch_path_v1(item)
        if kind != "exact":
            raise EvidencePacketError("RESULT_FIELD_INVALID", "changed_files items must be exact paths")
    if value != sorted(value):
        raise EvidencePacketError("CHANGED_FILES_NOT_SORTED", "changed_files must be sorted")
    if len(value) != len(set(value)):
        raise EvidencePacketError("CHANGED_FILES_NOT_UNIQUE", "changed_files must be unique")


def validate_result_field_validation(value, test_artifact_locators):
    if not isinstance(value, dict):
        raise EvidencePacketError("RESULT_FIELD_INVALID", "validation must be a dict")
    _check_no_unknown_fields(value, VALIDATION_KEYS, "validation")
    if "status" not in value:
        raise EvidencePacketError("RESULT_FIELD_MISSING", "validation.status missing")
    if not isinstance(value["status"], str) or value["status"] not in RESULT_STATUS_VALUES:
        raise EvidencePacketError("RESULT_FIELD_INVALID", f"validation.status invalid: {value['status']!r}")
    if "evidence_locator" not in value:
        raise EvidencePacketError("RESULT_FIELD_MISSING", "validation.evidence_locator missing")
    if not isinstance(value["evidence_locator"], str):
        raise EvidencePacketError("RESULT_FIELD_INVALID", "validation.evidence_locator must be string")
    check_locator_safety_strict(value["evidence_locator"])
    if value["evidence_locator"] not in test_artifact_locators:
        raise EvidencePacketError("VALIDATION_EVIDENCE_NOT_IN_TESTS", "validation.evidence_locator not in test artifacts")


def validate_result_field_acceptance_evidence(value, test_artifact_locators):
    if not isinstance(value, list):
        raise EvidencePacketError("RESULT_FIELD_INVALID", "acceptance_evidence must be a list")
    if len(value) == 0:
        raise EvidencePacketError("RESULT_FIELD_EMPTY", "acceptance_evidence must be non-empty")
    for item in value:
        if not isinstance(item, str):
            raise EvidencePacketError("RESULT_FIELD_INVALID", "acceptance_evidence items must be strings")
        check_locator_safety_strict(item)
    if len(value) != len(set(value)):
        raise EvidencePacketError("ACCEPTANCE_EVIDENCE_NOT_UNIQUE", "acceptance_evidence must be unique")
    for item in value:
        if item not in test_artifact_locators:
            raise EvidencePacketError("ACCEPTANCE_EVIDENCE_NOT_VERIFIED", f"acceptance_evidence item {item!r} not in test artifacts")


def validate_result_field_effect_checks(value):
    if not isinstance(value, dict):
        raise EvidencePacketError("RESULT_FIELD_INVALID", "effect_checks must be a dict")
    if len(value) == 0:
        raise EvidencePacketError("RESULT_FIELD_EMPTY", "effect_checks must be non-empty")
    for k, v in value.items():
        if not isinstance(k, str):
            raise EvidencePacketError("RESULT_FIELD_INVALID", "effect_checks keys must be strings")
        try:
            check_locator_safety_strict(k)
        except EvidencePacketError:
            raise EvidencePacketError("EFFECT_CHECKS_KEY_UNSAFE", f"effect_checks key {k!r} is unsafe")
        if not isinstance(v, str) or v not in RESULT_STATUS_VALUES:
            raise EvidencePacketError("RESULT_FIELD_INVALID", f"effect_checks.{k} must be {RESULT_STATUS_VALUES}")


def validate_result_field_risks(value):
    if not isinstance(value, list):
        raise EvidencePacketError("RESULT_FIELD_INVALID", "risks must be a list")
    if len(value) == 0:
        raise EvidencePacketError("RESULT_FIELD_EMPTY", "risks must be non-empty")
    for item in value:
        if not isinstance(item, str):
            raise EvidencePacketError("RESULT_FIELD_INVALID", "risks items must be strings")
        if not item.strip():
            raise EvidencePacketError("RISK_BLANK", "risks items must be nonblank")
    if len(value) != len(set(value)):
        raise EvidencePacketError("RISKS_NOT_UNIQUE", "risks must be unique")


def validate_all_result_fields(rf, test_artifact_locators):
    validate_result_field_status(rf["status"])
    validate_result_field_changed_files(rf["changed_files"])
    validate_result_field_validation(rf["validation"], test_artifact_locators)
    validate_result_field_acceptance_evidence(rf["acceptance_evidence"], test_artifact_locators)
    validate_result_field_effect_checks(rf["effect_checks"])
    validate_result_field_risks(rf["risks"])


def validate_field_source_bindings(fsb):
    expected = set(ALLOWED_FIELD_SOURCE_BINDINGS)
    actual = set(fsb)
    if actual != expected:
        raise EvidencePacketError(
            "BINDING_UNKNOWN_FIELD",
            f"field_source_bindings keys mismatch: missing={sorted(expected - actual)} extra={sorted(actual - expected)}",
        )
    for field in REQUIRED_RESULT_FIELDS:
        binding = fsb[field]
        allowed = ALLOWED_FIELD_SOURCE_BINDINGS[field]
        if not isinstance(binding, str) or binding not in allowed:
            raise EvidencePacketError(
                "BINDING_INVALID",
                f"field {field!r} binding {binding!r} not in allowed {allowed}",
            )


def validate_snapshot_schema(snapshot):
    if not isinstance(snapshot, dict):
        raise EvidencePacketError("SNAPSHOT_INVALID", "snapshot must be a dict")
    _require_exact_keys(snapshot, {"schema_version", "files"}, "snapshot")
    if snapshot["schema_version"] != SNAPSHOT_SCHEMA_VERSION:
        raise EvidencePacketError(
            "SNAPSHOT_SCHEMA_INVALID", "snapshot schema_version mismatch"
        )
    files = snapshot["files"]
    if not isinstance(files, dict):
        raise EvidencePacketError("SNAPSHOT_INVALID", "snapshot.files must be a dict")
    for path, entry in files.items():
        if not isinstance(path, str) or not path:
            raise EvidencePacketError("SNAPSHOT_INVALID", f"snapshot file path invalid: {path!r}")
        kind, _ = _parse_dispatch_path_v1(path)
        if kind != "exact":
            raise EvidencePacketError("SNAPSHOT_INVALID", f"snapshot path must be exact: {path!r}")
        if not isinstance(entry, dict):
            raise EvidencePacketError(
                "SNAPSHOT_INVALID", f"snapshot.files[{path!r}] must be a dict"
            )
        state = entry.get("state")
        if state == "present":
            _require_exact_keys(
                entry, {"state", "sha256"}, f"snapshot.files[{path!r}]"
            )
            _check_sha256_lowercase_hex(
                entry["sha256"], f"snapshot.files[{path!r}].sha256"
            )
        elif state == "absent":
            _require_exact_keys(entry, {"state"}, f"snapshot.files[{path!r}]")
        else:
            raise EvidencePacketError(
                "SNAPSHOT_INVALID",
                f"snapshot.files[{path!r}].state must be present or absent",
            )


def _parse_diff_metadata(diff_content):
    """Parse a deliberately closed subset of ordinary Git diff output."""
    if not isinstance(diff_content, bytes):
        raise EvidencePacketError("DIFF_INVALID", "diff content must be bytes")
    if diff_content == b"":
        return set(), set()
    try:
        lines = diff_content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise EvidencePacketError("DIFF_INVALID", f"diff is not UTF-8: {exc}")

    files = set()
    absent = set()
    used_paths = set()
    section = None
    in_hunk = False
    remaining_old = 0
    remaining_new = 0
    last_hunk_change = False
    binary_patch = False
    binary_patch_header_seen = False
    binary_patch_data_seen = False
    binary_patch_block_closed = False

    mode_pattern = re.compile(r"^[0-7]{6}$")
    index_pattern = re.compile(
        r"^[0-9a-f]{4,64}\.\.[0-9a-f]{4,64}(?: [0-7]{6})?$"
    )
    percent_pattern = re.compile(r"^(?:100|[1-9]?[0-9])%$")
    binary_data_pattern = re.compile(
        r"^[A-Za-z][0-9A-Za-z!#$%&()*+\-;<=>?@^_`{|}~]+$"
    )
    hunk_pattern = re.compile(
        r"^@@ -(?:0|[1-9][0-9]*)(?:,([0-9]+))? "
        r"\+(?:0|[1-9][0-9]*)(?:,([0-9]+))? @@(?: .*)?$"
    )

    def parse_header_token(payload, header):
        raw_path = payload.split("\t", 1)[0]
        try:
            tokens = shlex.split(raw_path, posix=True)
        except ValueError as exc:
            raise EvidencePacketError(
                "DIFF_INVALID", f"invalid {header} path: {exc}"
            )
        if len(tokens) != 1:
            raise EvidencePacketError(
                "DIFF_INVALID", f"invalid {header} path: {payload!r}"
            )
        return tokens[0]

    def exact_path(token, expected_prefix, header, *, allow_dev_null=False):
        if token == "/dev/null":
            if allow_dev_null:
                return None
            raise EvidencePacketError(
                "DIFF_INVALID", f"/dev/null is invalid in {header}"
            )
        if not token.startswith(expected_prefix):
            raise EvidencePacketError(
                "DIFF_INVALID",
                f"{header} path lacks {expected_prefix!r} prefix: {token!r}",
            )
        path = token[len(expected_prefix):]
        kind, _ = _parse_dispatch_path_v1(path)
        if kind != "exact":
            raise EvidencePacketError(
                "DIFF_INVALID", f"diff path is not exact: {path!r}"
            )
        return path

    def unprefixed_exact_path(payload, header):
        path = parse_header_token(payload, header)
        kind, _ = _parse_dispatch_path_v1(path)
        if kind != "exact":
            raise EvidencePacketError(
                "DIFF_INVALID", f"{header} path is not exact: {path!r}"
            )
        return path

    def finalize_section():
        nonlocal section, binary_patch, binary_patch_header_seen
        nonlocal binary_patch_data_seen, binary_patch_block_closed
        if section is None:
            return
        if in_hunk and (remaining_old != 0 or remaining_new != 0):
            raise EvidencePacketError("DIFF_INVALID", "truncated unified diff hunk")
        if section["old_header"] != section["new_header"]:
            raise EvidencePacketError(
                "DIFF_INVALID", "old/new file headers must be paired"
            )
        if (section["old_mode"] is None) != (section["new_mode"] is None):
            raise EvidencePacketError(
                "DIFF_INVALID", "old/new mode metadata must be paired"
            )
        if (
            section["old_mode"] is not None
            and section["old_mode"] == section["new_mode"]
        ):
            raise EvidencePacketError(
                "DIFF_INVALID", "old/new mode metadata must describe a change"
            )
        if section["rename_from"] != section["rename_to"]:
            raise EvidencePacketError(
                "DIFF_INVALID", "rename from/to metadata must be paired"
            )
        if section["copy_from"] != section["copy_to"]:
            raise EvidencePacketError(
                "DIFF_INVALID", "copy from/to metadata must be paired"
            )
        if binary_patch and not (
            binary_patch_header_seen and binary_patch_data_seen
        ):
            raise EvidencePacketError(
                "DIFF_INVALID", "GIT binary patch payload is incomplete"
            )

        is_rename = section["rename_from"] and section["rename_to"]
        is_copy = section["copy_from"] and section["copy_to"]
        is_new = section["new_file_mode"]
        is_delete = section["deleted_file_mode"]
        is_binary = section["binary"]
        structural_kinds = sum(bool(value) for value in (
            is_rename, is_copy, is_new, is_delete
        ))
        if structural_kinds > 1:
            raise EvidencePacketError(
                "DIFF_INVALID", "diff section mixes incompatible semantics"
            )

        headers = section["old_header"] and section["new_header"]
        has_text = section["hunks"] > 0
        has_mode_change = (
            section["old_mode"] is not None and section["new_mode"] is not None
        )
        old_path = section["old"]
        new_path = section["new"]

        if is_rename or is_copy:
            similarity = section["similarity"]
            if old_path == new_path or has_mode_change or similarity is None or section["dissimilarity"] is not None:
                raise EvidencePacketError(
                    "DIFF_INVALID", "rename/copy section has mixed or incomplete semantics"
                )
            if similarity == "100%":
                if headers or has_text or is_binary:
                    raise EvidencePacketError(
                        "DIFF_INVALID", "100% rename/copy cannot carry changed payload"
                    )
            elif is_binary:
                if headers or has_text:
                    raise EvidencePacketError(
                        "DIFF_INVALID", "modified binary rename/copy mixes text payload"
                    )
                if section["binary_old"] not in (None, f"a/{old_path}"):
                    raise EvidencePacketError("DIFF_INVALID", "binary rename/copy old path mismatch")
                if section["binary_new"] not in (None, f"b/{new_path}"):
                    raise EvidencePacketError("DIFF_INVALID", "binary rename/copy new path mismatch")
            elif not (
                headers
                and not section["old_dev_null"]
                and not section["new_dev_null"]
                and has_text
            ):
                raise EvidencePacketError(
                    "DIFF_INVALID", "modified rename/copy lacks a complete payload"
                )
        elif is_new:
            if (
                old_path != new_path
                or has_mode_change
                or section["similarity"] is not None
                or section["dissimilarity"] is not None
            ):
                raise EvidencePacketError("DIFF_INVALID", "new-file section is inconsistent")
            if is_binary:
                if section["binary_old"] not in (None, "/dev/null"):
                    raise EvidencePacketError("DIFF_INVALID", "binary new file has invalid old side")
                if section["binary_new"] not in (None, f"b/{new_path}"):
                    raise EvidencePacketError("DIFF_INVALID", "binary new file has invalid new side")
                if headers or has_text:
                    raise EvidencePacketError("DIFF_INVALID", "binary new file mixes text headers")
            elif not (
                headers
                and section["old_dev_null"]
                and not section["new_dev_null"]
            ):
                raise EvidencePacketError(
                    "DIFF_INVALID", "new file mode requires --- /dev/null and +++ target"
                )
        elif is_delete:
            if (
                old_path != new_path
                or has_mode_change
                or section["similarity"] is not None
                or section["dissimilarity"] is not None
            ):
                raise EvidencePacketError("DIFF_INVALID", "deleted-file section is inconsistent")
            if is_binary:
                if section["binary_old"] not in (None, f"a/{old_path}"):
                    raise EvidencePacketError("DIFF_INVALID", "binary deletion has invalid old side")
                if section["binary_new"] not in (None, "/dev/null"):
                    raise EvidencePacketError("DIFF_INVALID", "binary deletion has invalid new side")
                if headers or has_text:
                    raise EvidencePacketError("DIFF_INVALID", "binary deletion mixes text headers")
            elif not (
                headers
                and not section["old_dev_null"]
                and section["new_dev_null"]
            ):
                raise EvidencePacketError(
                    "DIFF_INVALID", "deleted file mode requires --- source and +++ /dev/null"
                )
        elif is_binary:
            if (
                headers
                or has_text
                or old_path != new_path
                or section["similarity"] is not None
            ):
                raise EvidencePacketError("DIFF_INVALID", "binary section mixes other semantics")
            if section["binary_old"] not in (None, f"a/{old_path}"):
                raise EvidencePacketError("DIFF_INVALID", "binary old path mismatch")
            if section["binary_new"] not in (None, f"b/{new_path}"):
                raise EvidencePacketError("DIFF_INVALID", "binary new path mismatch")
        else:
            if section["similarity"] is not None:
                raise EvidencePacketError("DIFF_INVALID", "similarity metadata lacks rename/copy")
            ordinary_headers = (
                headers
                and not section["old_dev_null"]
                and not section["new_dev_null"]
            )
            if section["dissimilarity"] is not None and not (
                ordinary_headers and has_text
            ):
                raise EvidencePacketError(
                    "DIFF_INVALID", "dissimilarity metadata lacks a text payload"
                )
            if old_path != new_path or not (
                (ordinary_headers and has_text) or has_mode_change
            ):
                raise EvidencePacketError(
                    "DIFF_INVALID", "ordinary text section is incomplete"
                )

        section_paths = {old_path, new_path}
        mutation_paths = {new_path} if is_copy else section_paths
        overlap = mutation_paths & used_paths
        if overlap:
            raise EvidencePacketError(
                "DIFF_INVALID", f"path reused across diff sections: {sorted(overlap)}"
            )
        used_paths.update(mutation_paths)
        files.update(section_paths)
        if is_delete or is_rename:
            absent.add(old_path)
        section = None
        binary_patch = False
        binary_patch_header_seen = False
        binary_patch_data_seen = False
        binary_patch_block_closed = False

    for line in lines:
        if in_hunk:
            if line == "\\ No newline at end of file":
                if not last_hunk_change:
                    raise EvidencePacketError(
                        "DIFF_INVALID", "newline marker must follow a changed hunk line"
                    )
                last_hunk_change = False
                continue
            if remaining_old == 0 and remaining_new == 0:
                in_hunk = False
                last_hunk_change = False
            else:
                if line.startswith("-"):
                    remaining_old -= 1
                    last_hunk_change = True
                elif line.startswith("+"):
                    remaining_new -= 1
                    last_hunk_change = True
                elif line.startswith(" "):
                    remaining_old -= 1
                    remaining_new -= 1
                    last_hunk_change = True
                else:
                    raise EvidencePacketError(
                        "DIFF_INVALID", f"invalid unified diff hunk line: {line!r}"
                    )
                if remaining_old < 0 or remaining_new < 0:
                    raise EvidencePacketError(
                        "DIFF_INVALID", "unified diff hunk exceeds declared line counts"
                    )
                continue

        if binary_patch and not line.startswith("diff --git "):
            if re.fullmatch(r"(?:literal|delta) (?:0|[1-9][0-9]*)", line):
                if binary_patch_header_seen and not (
                    binary_patch_data_seen and binary_patch_block_closed
                ):
                    raise EvidencePacketError(
                        "DIFF_INVALID", "GIT binary patch block is incomplete"
                    )
                binary_patch_header_seen = True
                binary_patch_data_seen = False
                binary_patch_block_closed = False
            elif line == "":
                if (
                    not binary_patch_header_seen
                    or not binary_patch_data_seen
                    or binary_patch_block_closed
                ):
                    raise EvidencePacketError(
                        "DIFF_INVALID", "invalid GIT binary patch block separator"
                    )
                binary_patch_block_closed = True
            elif (
                binary_patch_header_seen
                and not binary_patch_block_closed
                and binary_data_pattern.fullmatch(line)
                and (len(line) - 1) % 5 == 0
            ):
                binary_patch_data_seen = True
            else:
                raise EvidencePacketError(
                    "DIFF_INVALID", f"invalid GIT binary patch payload: {line!r}"
                )
            continue

        if line.startswith("diff --git "):
            finalize_section()
            try:
                tokens = shlex.split(line[len("diff --git "):], posix=True)
            except ValueError as exc:
                raise EvidencePacketError(
                    "DIFF_INVALID", f"invalid diff --git header: {exc}"
                )
            if len(tokens) != 2:
                raise EvidencePacketError(
                    "DIFF_INVALID", f"invalid diff --git header: {line!r}"
                )
            section = {
                "old": exact_path(tokens[0], "a/", "diff --git"),
                "new": exact_path(tokens[1], "b/", "diff --git"),
                "index": False,
                "old_header": False,
                "new_header": False,
                "old_dev_null": False,
                "new_dev_null": False,
                "new_file_mode": False,
                "deleted_file_mode": False,
                "old_mode": None,
                "new_mode": None,
                "similarity": None,
                "dissimilarity": None,
                "rename_from": False,
                "rename_to": False,
                "copy_from": False,
                "copy_to": False,
                "hunks": 0,
                "binary": False,
                "binary_old": None,
                "binary_new": None,
            }
            continue

        if line.startswith("diff --cc ") or line.startswith("diff --combined "):
            raise EvidencePacketError("DIFF_INVALID", "combined diffs are unsupported")
        if section is None:
            raise EvidencePacketError(
                "DIFF_INVALID", f"content outside diff --git section: {line!r}"
            )

        metadata_closed = bool(
            section["old_header"]
            or section["new_header"]
            or section["hunks"]
            or section["binary"]
        )
        metadata_prefixes = (
            "index ", "new file mode ", "deleted file mode ",
            "old mode ", "new mode ", "similarity index ",
            "dissimilarity index ", "rename from ", "rename to ",
            "copy from ", "copy to ",
        )
        if metadata_closed and line.startswith(metadata_prefixes):
            raise EvidencePacketError(
                "DIFF_INVALID", f"metadata appears after section payload: {line!r}"
            )

        if line.startswith("index "):
            if section["index"] or not index_pattern.fullmatch(line[6:]):
                raise EvidencePacketError("DIFF_INVALID", f"invalid index metadata: {line!r}")
            section["index"] = True
        elif line.startswith("new file mode "):
            if section["new_file_mode"] or not mode_pattern.fullmatch(line[14:]):
                raise EvidencePacketError("DIFF_INVALID", f"invalid new file mode: {line!r}")
            section["new_file_mode"] = True
        elif line.startswith("deleted file mode "):
            if section["deleted_file_mode"] or not mode_pattern.fullmatch(line[18:]):
                raise EvidencePacketError("DIFF_INVALID", f"invalid deleted file mode: {line!r}")
            section["deleted_file_mode"] = True
        elif line.startswith("old mode "):
            if section["old_mode"] is not None or not mode_pattern.fullmatch(line[9:]):
                raise EvidencePacketError("DIFF_INVALID", f"invalid old mode: {line!r}")
            section["old_mode"] = line[9:]
        elif line.startswith("new mode "):
            if section["new_mode"] is not None or not mode_pattern.fullmatch(line[9:]):
                raise EvidencePacketError("DIFF_INVALID", f"invalid new mode: {line!r}")
            section["new_mode"] = line[9:]
        elif line.startswith("similarity index "):
            value = line[17:]
            if section["similarity"] is not None or not percent_pattern.fullmatch(value):
                raise EvidencePacketError("DIFF_INVALID", f"invalid similarity index: {line!r}")
            section["similarity"] = value
        elif line.startswith("dissimilarity index "):
            value = line[20:]
            if section["dissimilarity"] is not None or not percent_pattern.fullmatch(value):
                raise EvidencePacketError("DIFF_INVALID", f"invalid dissimilarity index: {line!r}")
            section["dissimilarity"] = value
        elif line.startswith("rename from "):
            path = unprefixed_exact_path(line[12:], "rename from")
            if section["rename_from"] or path != section["old"]:
                raise EvidencePacketError("DIFF_INVALID", "rename source differs from diff --git")
            section["rename_from"] = True
        elif line.startswith("rename to "):
            path = unprefixed_exact_path(line[10:], "rename to")
            if not section["rename_from"] or section["rename_to"] or path != section["new"]:
                raise EvidencePacketError("DIFF_INVALID", "rename target differs from diff --git")
            section["rename_to"] = True
        elif line.startswith("copy from "):
            path = unprefixed_exact_path(line[10:], "copy from")
            if section["copy_from"] or path != section["old"]:
                raise EvidencePacketError("DIFF_INVALID", "copy source differs from diff --git")
            section["copy_from"] = True
        elif line.startswith("copy to "):
            path = unprefixed_exact_path(line[8:], "copy to")
            if not section["copy_from"] or section["copy_to"] or path != section["new"]:
                raise EvidencePacketError("DIFF_INVALID", "copy target differs from diff --git")
            section["copy_to"] = True
        elif line.startswith("--- "):
            if section["old_header"] or section["new_header"] or section["hunks"]:
                raise EvidencePacketError("DIFF_INVALID", "duplicate or out-of-order --- header")
            token = parse_header_token(line[4:], "---")
            path = exact_path(token, "a/", "---", allow_dev_null=True)
            if path is not None and path != section["old"]:
                raise EvidencePacketError("DIFF_INVALID", "--- path differs from diff --git")
            section["old_header"] = True
            section["old_dev_null"] = token == "/dev/null"
        elif line.startswith("+++ "):
            if not section["old_header"] or section["new_header"] or section["hunks"]:
                raise EvidencePacketError("DIFF_INVALID", "duplicate or out-of-order +++ header")
            token = parse_header_token(line[4:], "+++")
            path = exact_path(token, "b/", "+++", allow_dev_null=True)
            if path is not None and path != section["new"]:
                raise EvidencePacketError("DIFF_INVALID", "+++ path differs from diff --git")
            section["new_header"] = True
            section["new_dev_null"] = token == "/dev/null"
            if section["old_dev_null"] and section["new_dev_null"]:
                raise EvidencePacketError("DIFF_INVALID", "both diff sides are /dev/null")
        elif line.startswith("@@"):
            if not (section["old_header"] and section["new_header"]):
                raise EvidencePacketError("DIFF_INVALID", "hunk lacks paired file headers")
            match = hunk_pattern.fullmatch(line)
            if match is None:
                raise EvidencePacketError("DIFF_INVALID", f"invalid hunk header: {line!r}")
            remaining_old = int(match.group(1) or "1")
            remaining_new = int(match.group(2) or "1")
            in_hunk = True
            last_hunk_change = False
            section["hunks"] += 1
        elif line.startswith("Binary files ") and line.endswith(" differ"):
            if section["binary"]:
                raise EvidencePacketError("DIFF_INVALID", "duplicate binary marker")
            payload = line[len("Binary files "):-len(" differ")]
            try:
                binary_tokens = shlex.split(payload, posix=True)
            except ValueError as exc:
                raise EvidencePacketError(
                    "DIFF_INVALID", f"invalid binary marker: {exc}"
                )
            if len(binary_tokens) != 3 or binary_tokens[1] != "and":
                raise EvidencePacketError("DIFF_INVALID", f"invalid binary marker: {line!r}")
            old_token = binary_tokens[0]
            new_token = binary_tokens[2]
            if old_token != "/dev/null":
                exact_path(old_token, "a/", "Binary files old")
            if new_token != "/dev/null":
                exact_path(new_token, "b/", "Binary files new")
            section["binary"] = True
            section["binary_old"] = old_token
            section["binary_new"] = new_token
        elif line == "GIT binary patch":
            if section["binary"]:
                raise EvidencePacketError("DIFF_INVALID", "duplicate binary marker")
            section["binary"] = True
            binary_patch = True
            binary_patch_header_seen = False
            binary_patch_data_seen = False
            binary_patch_block_closed = False
        else:
            raise EvidencePacketError(
                "DIFF_INVALID", f"unknown diff metadata: {line!r}"
            )

    finalize_section()
    if not files:
        raise EvidencePacketError("DIFF_INVALID", "diff contains no complete sections")
    return files, absent


def extract_diff_file_set(diff_content):
    files, _ = _parse_diff_metadata(diff_content)
    return files


def _extract_diff_absent_set(diff_content):
    """Return paths whose post-diff state is absent (delete or rename source)."""
    _, absent = _parse_diff_metadata(diff_content)
    return absent


def _normalize_scope_string(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise EvidencePacketError("SCOPE_INVALID", "scope string must be non-empty")
    parts = [part.strip() for part in raw.split(",")]
    if any(not part for part in parts):
        raise EvidencePacketError("SCOPE_INVALID", "scope string contains an empty item")
    if len(parts) != len(set(parts)):
        raise EvidencePacketError("SCOPE_INVALID", "scope string contains duplicate items")
    for part in parts:
        validate_dispatch_path_v1(part)
    _reject_redundant_patterns(parts, "scope string")
    return sorted(parts)


def reconcile_snapshot_scope(attested_changed_files, scope_changed_files, snapshot_files, diff_files, normalized, raw_caller_strings):
    attested_set = set(attested_changed_files)
    scope_set = set(scope_changed_files)
    snapshot_set = set(snapshot_files)
    diff_set = diff_files

    if attested_set != scope_set:
        raise EvidencePacketError("RECON_MISMATCH", "attested changed_files != scope changed_files")
    if attested_set != snapshot_set:
        raise EvidencePacketError("RECON_MISMATCH", "attested changed_files != snapshot files")
    if attested_set != diff_set:
        raise EvidencePacketError("RECON_MISMATCH", "attested changed_files != diff file set")

    expected_allowed = _normalize_scope_string(raw_caller_strings.get("allowed_files", ""))
    expected_forbidden = _normalize_scope_string(raw_caller_strings.get("forbidden_files", ""))
    if normalized.get("allowed_files") != expected_allowed:
        raise EvidencePacketError("NORMALIZED_MISMATCH", "normalized.allowed_files mismatch")
    if normalized.get("forbidden_files") != expected_forbidden:
        raise EvidencePacketError("NORMALIZED_MISMATCH", "normalized.forbidden_files mismatch")

    allowed = normalized.get("allowed_files", [])
    forbidden = normalized.get("forbidden_files", [])
    claims = normalized.get("canonical_file_claims", [])

    changed_outside_allowed = sorted(f for f in attested_set if not any(match_dispatch_path_v1(f, p) for p in allowed))
    changed_matching_forbidden = sorted(f for f in attested_set if any(match_dispatch_path_v1(f, p) for p in forbidden))
    changed_without_claim = sorted(f for f in attested_set if not any(match_dispatch_path_v1(f, p) for p in claims))
    claims_outside_allowed = sorted(
        c for c in claims if not any(dispatch_path_contains(a, c) for a in allowed)
    )
    claims_intersecting_forbidden = sorted(
        c for c in claims if any(dispatch_paths_intersect(c, fb) for fb in forbidden)
    )

    violations = changed_outside_allowed or changed_matching_forbidden or changed_without_claim or claims_outside_allowed or claims_intersecting_forbidden
    expected_recon = {
        "changed_outside_allowed": changed_outside_allowed,
        "changed_matching_forbidden": changed_matching_forbidden,
        "changed_without_claim": changed_without_claim,
        "claims_outside_allowed": claims_outside_allowed,
        "claims_intersecting_forbidden": claims_intersecting_forbidden,
        "status": "FAIL" if violations else "PASS",
    }

    return expected_recon


def _require_exact_keys(obj, keys, path):
    _check_no_unknown_fields(obj, keys, path)
    missing = set(keys) - set(obj)
    if missing:
        raise EvidencePacketError("STRUCT_MISSING", f"{path} missing fields: {sorted(missing)}")


def _validate_task_identity(task, path):
    task_id = task["task_id"]
    if not isinstance(task_id, str) or not ID_PATTERN.fullmatch(task_id):
        raise EvidencePacketError("TASK_ID_INVALID", f"{path}.task_id is invalid")
    task_version = task["task_version"]
    if isinstance(task_version, bool) or not isinstance(task_version, int) or task_version <= 0:
        raise EvidencePacketError("TASK_VERSION_INVALID", f"{path}.task_version must be a positive integer")
    change_version = task["change_version"]
    if not _is_exact_semver(change_version):
        raise EvidencePacketError("CHANGE_VERSION_INVALID", f"{path}.change_version must be exact SemVer")


def _is_exact_semver(value):
    if not isinstance(value, str) or not SEMVER_PATTERN.fullmatch(value):
        return False
    core_and_prerelease = value.split("+", 1)[0]
    if "-" not in core_and_prerelease:
        return True
    prerelease = core_and_prerelease.split("-", 1)[1]
    return all(
        not (identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"))
        for identifier in prerelease.split(".")
    )


def _is_canonical_rfc_uuid(value):
    if not isinstance(value, str) or not UUID_CANONICAL_PATTERN.fullmatch(value):
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return (
        parsed.int != 0
        and str(parsed) == value
        and parsed.variant == uuid.RFC_4122
        and parsed.version in range(1, 9)
    )


def _validate_identity(identity, path):
    for field in ("parent_session_id", "run_id", "session_id"):
        value = identity[field]
        if not _is_canonical_rfc_uuid(value):
            raise EvidencePacketError(
                "IDENTITY_INVALID",
                f"{path}.{field} must be a canonical lowercase non-nil RFC UUID",
            )
    for field in ("client", "parent_client"):
        value = identity[field]
        if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
            raise EvidencePacketError("IDENTITY_INVALID", f"{path}.{field} must be a stable ID")
    if not isinstance(identity["agent_id"], str) or not AGENT_ID_PATTERN.fullmatch(identity["agent_id"]):
        raise EvidencePacketError("IDENTITY_INVALID", f"{path}.agent_id must be a stable agent identity")
    if identity["client"] not in {"qoder", "codex"}:
        raise EvidencePacketError(
            "IDENTITY_INVALID", f"{path}.client must be qoder or codex"
        )
    if identity["parent_client"] != "codex":
        raise EvidencePacketError("IDENTITY_INVALID", f"{path}.parent_client must be codex")


def _validate_nonempty_string(value, path):
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(char) < 0x20 or ord(char) == 0x7f for char in value)
    ):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID", f"{path} must be a non-empty control-free string"
        )


def _validate_nonempty_strings(value, path):
    if not isinstance(value, list) or not value:
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID", f"{path} must be a non-empty list"
        )
    for index, item in enumerate(value):
        _validate_nonempty_string(item, f"{path}[{index}]")
    if len(value) != len(set(value)):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID", f"{path} must not contain duplicates"
        )


def _validate_codex_mapping_keys(value, task_ids, path):
    if not isinstance(value, dict) or set(value) != set(task_ids):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path} keys must equal caller_contract.task_ids",
        )


def validate_codex_work_package_task_projection(raw_task, path="raw task"):
    """Validate one target-Task projection of a Codex work-package handoff.

    The caller contract is preserved as one exact nested object.  Runner identity
    is separate and therefore cannot be supplied through that caller object.
    Qoder-only permission/resume fields are rejected by the exact-key checks.
    """
    _require_exact_keys(raw_task, CODEX_RAW_TASK_KEYS, path)
    if raw_task["schema_version"] != CODEX_TASK_PROJECTION_SCHEMA_VERSION:
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID", f"{path}.schema_version is unsupported"
        )
    work_package_id = raw_task["work_package_id"]
    if (
        not isinstance(work_package_id, str)
        or not WORK_PACKAGE_ID_PATTERN.fullmatch(work_package_id)
    ):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID", f"{path}.work_package_id is invalid"
        )
    task_ids = raw_task["task_ids"]
    if (
        not isinstance(task_ids, list)
        or len(task_ids) < 2
        or len(task_ids) != len(set(task_ids))
        or any(not isinstance(item, str) or not TASK_ID_PATTERN.fullmatch(item) for item in task_ids)
    ):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path}.task_ids must be an ordered unique list with at least two Task IDs",
        )
    target_task_id = raw_task["target_task_id"]
    if target_task_id not in task_ids or raw_task["task_id"] != target_task_id:
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path}.target_task_id must select raw task.task_id from task_ids",
        )
    _validate_task_identity(raw_task, path)
    _validate_identity(raw_task, path)
    if raw_task["client"] != "codex":
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID", f"{path}.client must be codex"
        )
    _validate_nonempty_string(raw_task["task_source"], f"{path}.task_source")
    for field in ("expected_output", "validation_command"):
        _validate_nonempty_string(raw_task[field], f"{path}.{field}")
    for field in ("acceptance_criteria", "acceptance_evidence"):
        _validate_nonempty_strings(raw_task[field], f"{path}.{field}")
    target_allowed = _normalize_scope_string(raw_task["allowed_files"])
    _normalize_scope_string(raw_task["forbidden_files"])

    caller = raw_task["caller_contract"]
    _require_exact_keys(caller, CODEX_CALLER_CONTRACT_KEYS, f"{path}.caller_contract")
    for field in (
        "goal", "primary_owner", "contract_boundary", "required_context",
        "failure_policy",
    ):
        _validate_nonempty_string(caller[field], f"{path}.caller_contract.{field}")
    if not ID_PATTERN.fullmatch(caller["primary_owner"]):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path}.caller_contract.primary_owner must be a stable ID",
        )
    if caller["parent_client"] != "codex":
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path}.caller_contract.parent_client must be codex",
        )
    estimated = caller["estimated_minutes"]
    if (
        isinstance(estimated, bool)
        or not isinstance(estimated, int)
        or estimated < 120
        or estimated > 360
    ):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path}.caller_contract.estimated_minutes must be 120..360",
        )
    if (
        caller["work_package_id"] != work_package_id
        or caller["task_ids"] != task_ids
    ):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path} work-package identity differs from caller_contract",
        )

    package_allowed = _normalize_scope_string(caller["allowed_files"])
    _normalize_scope_string(caller["forbidden_files"])
    if any(
        not any(dispatch_path_contains(container, member) for container in package_allowed)
        for member in target_allowed
    ):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path} target allowed_files escape caller work-package scope",
        )
    versions = caller["task_versions"]
    changes = caller["change_versions"]
    outputs = caller["expected_outputs_by_task"]
    acceptance = caller["acceptance_by_task"]
    commands = caller["validation_commands"]
    for value, label in (
        (versions, "task_versions"), (changes, "change_versions"),
        (outputs, "expected_outputs_by_task"),
        (acceptance, "acceptance_by_task"),
        (commands, "validation_commands"),
    ):
        _validate_codex_mapping_keys(
            value, task_ids, f"{path}.caller_contract.{label}"
        )
    for task_id in task_ids:
        version = versions[task_id]
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise EvidencePacketError(
                "CODEX_PROJECTION_INVALID",
                f"{path}.caller_contract.task_versions[{task_id!r}] is invalid",
            )
        if not _is_exact_semver(changes[task_id]):
            raise EvidencePacketError(
                "CODEX_PROJECTION_INVALID",
                f"{path}.caller_contract.change_versions[{task_id!r}] is invalid",
            )
        _validate_nonempty_string(
            outputs[task_id],
            f"{path}.caller_contract.expected_outputs_by_task[{task_id!r}]",
        )
        _validate_nonempty_string(
            commands[task_id],
            f"{path}.caller_contract.validation_commands[{task_id!r}]",
        )
        item = acceptance[task_id]
        _require_exact_keys(
            item, CODEX_ACCEPTANCE_KEYS,
            f"{path}.caller_contract.acceptance_by_task[{task_id!r}]",
        )
        _validate_nonempty_strings(
            item["acceptance_criteria"],
            f"{path}.caller_contract.acceptance_by_task[{task_id!r}].acceptance_criteria",
        )
        _validate_nonempty_strings(
            item["acceptance_evidence"],
            f"{path}.caller_contract.acceptance_by_task[{task_id!r}].acceptance_evidence",
        )

    if (
        versions[target_task_id] != raw_task["task_version"]
        or changes[target_task_id] != raw_task["change_version"]
        or outputs[target_task_id] != raw_task["expected_output"]
        or commands[target_task_id] != raw_task["validation_command"]
        or acceptance[target_task_id]["acceptance_criteria"] != raw_task["acceptance_criteria"]
        or acceptance[target_task_id]["acceptance_evidence"] != raw_task["acceptance_evidence"]
    ):
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID",
            f"{path} target projection differs from caller_contract",
        )


def validate_codex_main_task_projection(raw_task, path="raw task"):
    """Validate the mutually-exclusive, one-Task Main Agent projection.

    This route is for an actual host-bound Main actor only.  It intentionally
    has no ``work_package_id`` or ``task_ids`` and therefore cannot weaken the
    minimum two-Task sub-agent work-package contract.
    """
    _require_exact_keys(raw_task, CODEX_MAIN_RAW_TASK_KEYS, path)
    if raw_task["schema_version"] != CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION:
        raise EvidencePacketError("CODEX_PROJECTION_INVALID", f"{path}.schema_version is unsupported")
    _validate_task_identity(raw_task, path)
    _validate_identity(raw_task, path)
    if raw_task["client"] != "codex" or raw_task["parent_client"] != "codex":
        raise EvidencePacketError("CODEX_PROJECTION_INVALID", f"{path} must be host-routed Codex")
    if raw_task["agent_id"] != "/root":
        raise EvidencePacketError(
            "CODEX_PROJECTION_INVALID", f"{path} must bind the canonical Main actor /root"
        )
    for field in ("task_source", "expected_output", "validation_command", "goal", "required_context", "failure_policy"):
        _validate_nonempty_string(raw_task[field], f"{path}.{field}")
    for field in ("acceptance_criteria", "acceptance_evidence"):
        _validate_nonempty_strings(raw_task[field], f"{path}.{field}")
    _normalize_scope_string(raw_task["allowed_files"])
    _normalize_scope_string(raw_task["forbidden_files"])


def _validate_sorted_unique_paths(value, path, *, exact):
    if not isinstance(value, list) or not value:
        raise EvidencePacketError("STRUCT_EMPTY", f"{path} must be a non-empty list")
    if not all(isinstance(item, str) for item in value):
        raise EvidencePacketError("STRUCT_INVALID", f"{path} items must be strings")
    if value != sorted(value) or len(value) != len(set(value)):
        raise EvidencePacketError("STRUCT_INVALID", f"{path} must be sorted and unique")
    for item in value:
        kind, _ = _parse_dispatch_path_v1(item)
        if exact and kind != "exact":
            raise EvidencePacketError("STRUCT_INVALID", f"{path} must contain exact paths")
    _reject_redundant_patterns(value, path)


def validate_structure_strict(inp):
    if not isinstance(inp, dict):
        raise EvidencePacketError("STRUCT_INVALID", "input must be a dict")
    _check_no_issuer_fields(inp, "input")
    _require_exact_keys(inp, {"task", "subject", "scope"}, "input")

    task = inp["task"]
    _require_exact_keys(task, TASK_KEYS, "task")
    _validate_task_identity(task, "task")
    ts = task["task_source"]
    _require_exact_keys(ts, TASK_SOURCE_KEYS, "task.task_source")
    check_locator_safety_strict(ts["locator"])
    _check_sha256_lowercase_hex(ts["sha256"], "task.task_source.sha256")

    subject = inp["subject"]
    _require_exact_keys(subject, SUBJECT_KEYS, "subject")
    raw = subject["raw_artifacts"]
    _require_exact_keys(raw, RAW_ARTIFACT_CONTAINER_KEYS, "subject.raw_artifacts")
    for key in REQUIRED_RAW_ARTIFACT_KEYS:
        art = raw[key]
        _require_exact_keys(art, RAW_ARTIFACT_KEYS, f"raw_artifacts.{key}")
        check_locator_safety_strict(art["locator"])
        _check_sha256_lowercase_hex(art["sha256"], f"raw_artifacts.{key}.sha256")
    tests = raw["tests"]
    if not isinstance(tests, list) or not tests:
        raise EvidencePacketError("STRUCT_EMPTY", "raw_artifacts.tests must be non-empty list")
    for index, artifact in enumerate(tests):
        _require_exact_keys(artifact, RAW_ARTIFACT_KEYS, f"raw_artifacts.tests[{index}]")
        check_locator_safety_strict(artifact["locator"])
        _check_sha256_lowercase_hex(artifact["sha256"], f"raw_artifacts.tests[{index}].sha256")
    test_artifact_locators = [artifact["locator"] for artifact in tests]
    if len(test_artifact_locators) != len(set(test_artifact_locators)):
        raise EvidencePacketError("STRUCT_INVALID", "raw_artifacts.tests locators must be unique")

    attestation = subject["main_agent_attestation"]
    _require_exact_keys(attestation, ATTESTATION_KEYS, "main_agent_attestation")
    actor_id = attestation["actor_id"]
    if not isinstance(actor_id, str) or not ID_PATTERN.fullmatch(actor_id):
        raise EvidencePacketError("ACTOR_ID_INVALID", "actor_id must be a stable non-empty ID")
    reviewed_at = attestation["reviewed_at"]
    if not isinstance(reviewed_at, str) or not TIMESTAMP_PATTERN.fullmatch(reviewed_at):
        raise EvidencePacketError("REVIEWED_AT_INVALID", f"reviewed_at {reviewed_at!r} is not canonical UTC")
    try:
        datetime.datetime.strptime(reviewed_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise EvidencePacketError("REVIEWED_AT_INVALID", f"reviewed_at {reviewed_at!r} is not a real UTC datetime")

    result_fields = attestation["result_fields"]
    _require_exact_keys(result_fields, RESULT_FIELD_KEYS, "result_fields")
    validate_all_result_fields(result_fields, test_artifact_locators)
    field_sources = attestation["field_source_bindings"]
    if not isinstance(field_sources, dict):
        raise EvidencePacketError("STRUCT_INVALID", "field_source_bindings must be a dict")
    validate_field_source_bindings(field_sources)

    identity = subject["identity"]
    _require_exact_keys(identity, IDENTITY_KEYS, "identity")
    _validate_identity(identity, "identity")

    scope = inp["scope"]
    _require_exact_keys(scope, SCOPE_KEYS, "scope")
    validate_result_field_changed_files(scope["changed_files"])
    raw_scope = scope["raw_caller_strings"]
    _require_exact_keys(raw_scope, RAW_CALLER_KEYS, "scope.raw_caller_strings")
    _normalize_scope_string(raw_scope["allowed_files"])
    _normalize_scope_string(raw_scope["forbidden_files"])

    normalized = scope["normalized"]
    _require_exact_keys(normalized, NORMALIZED_KEYS, "scope.normalized")
    _validate_sorted_unique_paths(normalized["allowed_files"], "normalized.allowed_files", exact=False)
    _validate_sorted_unique_paths(normalized["forbidden_files"], "normalized.forbidden_files", exact=False)
    _validate_sorted_unique_paths(normalized["canonical_file_claims"], "normalized.canonical_file_claims", exact=False)

    reconciliation = scope["three_way_reconciliation"]
    _require_exact_keys(reconciliation, RECONCILIATION_KEYS, "scope.three_way_reconciliation")
    for field in RECONCILIATION_KEYS - {"status"}:
        value = reconciliation[field]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise EvidencePacketError("STRUCT_INVALID", f"three_way_reconciliation.{field} must be a string list")
        if value != sorted(value) or len(value) != len(set(value)):
            raise EvidencePacketError("STRUCT_INVALID", f"three_way_reconciliation.{field} must be sorted unique list")
        exact = field.startswith("changed_")
        for item in value:
            kind, _ = _parse_dispatch_path_v1(item)
            if exact and kind != "exact":
                raise EvidencePacketError("STRUCT_INVALID", f"three_way_reconciliation.{field} needs exact paths")
    if reconciliation["status"] not in ("PASS", "FAIL"):
        raise EvidencePacketError("STRUCT_INVALID", "three_way_reconciliation.status must be PASS or FAIL")


def validate_locators_strict(inp):
    ts = inp["task"]["task_source"]
    check_locator_safety_strict(ts["locator"])
    raw = inp["subject"]["raw_artifacts"]
    for key in REQUIRED_RAW_ARTIFACT_KEYS:
        check_locator_safety_strict(raw[key]["locator"])
    for i, t in enumerate(raw["tests"]):
        check_locator_safety_strict(t["locator"])


def verify_artifact_hashes_strict(repo_root, inp, root_identity=None):
    ts = inp["task"]["task_source"]
    actual = sha256_file_strict(repo_root, ts["locator"], root_identity)
    if actual != ts["sha256"]:
        raise EvidencePacketError("HASH_MISMATCH", f"task_source hash mismatch: {ts['locator']}")

    raw = inp["subject"]["raw_artifacts"]
    for key in REQUIRED_RAW_ARTIFACT_KEYS:
        art = raw[key]
        actual = sha256_file_strict(repo_root, art["locator"], root_identity)
        if actual != art["sha256"]:
            raise EvidencePacketError("HASH_MISMATCH", f"raw artifact hash mismatch: {key}")
    for i, t in enumerate(raw["tests"]):
        actual = sha256_file_strict(repo_root, t["locator"], root_identity)
        if actual != t["sha256"]:
            raise EvidencePacketError("HASH_MISMATCH", f"test artifact hash mismatch [{i}]")


def _read_json_dict_strict(repo_root, locator, label):
    data = _read_file_bytes_strict(repo_root, locator)
    return _parse_json_dict_strict(data, label)


def _reject_duplicate_json_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EvidencePacketError(
                "JSON_DUPLICATE_KEY", f"duplicate JSON object key: {key!r}"
            )
        value[key] = item
    return value


def _parse_json_value_strict(data, label, invalid_code="ARTIFACT_INVALID_JSON"):
    if not isinstance(data, bytes):
        raise EvidencePacketError(invalid_code, f"{label} bytes are invalid")
    try:
        text = data.decode("utf-8")
        return json.loads(text, object_pairs_hook=_reject_duplicate_json_pairs)
    except EvidencePacketError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidencePacketError(invalid_code, f"{label} is not valid JSON: {exc}")


def _parse_json_dict_strict(data, label):
    value = _parse_json_value_strict(data, label)
    if not isinstance(value, dict):
        raise EvidencePacketError("ARTIFACT_INVALID", f"{label} must be a JSON object")
    return value


def _read_bound_artifact_bytes(repo_root, descriptor, label, root_identity=None):
    data = _read_file_bytes_strict(
        repo_root, descriptor["locator"], root_identity
    )
    actual_hash = sha256_bytes(data)
    if actual_hash != descriptor["sha256"]:
        raise EvidencePacketError(
            "HASH_MISMATCH", f"{label} parsed bytes do not match declared hash"
        )
    return data


def _read_bound_json_artifact(repo_root, descriptor, label, root_identity=None):
    return _parse_json_dict_strict(
        _read_bound_artifact_bytes(
            repo_root, descriptor, label, root_identity
        ),
        label,
    )


def reconcile_identity_strict(repo_root, inp, root_identity=None):
    raw = inp["subject"]["raw_artifacts"]
    task = inp["task"]
    identity = inp["subject"]["identity"]
    task_artifact = _read_bound_json_artifact(
        repo_root, raw["task"], "raw task", root_identity
    )
    completion_artifact = _read_bound_json_artifact(
        repo_root, raw["completion"], "raw completion", root_identity
    )

    supplied_nine = dict(task)
    supplied_nine.update(identity)
    del supplied_nine["task_source"]
    for label, artifact in (("raw task", task_artifact), ("raw completion", completion_artifact)):
        for field in tuple(REQUIRED_TASK_IDENTITY_FIELDS) + tuple(REQUIRED_IDENTITY_FIELDS):
            if field not in artifact:
                raise EvidencePacketError("IDENTITY_MISSING", f"{label} missing {field}")
            if artifact[field] != supplied_nine[field]:
                raise EvidencePacketError("IDENTITY_DRIFT", f"{label} {field} drift")
        _validate_task_identity(artifact, label)
        _validate_identity(artifact, label)

    if identity["client"] == "codex":
        if task_artifact.get("schema_version") == CODEX_MAIN_TASK_PROJECTION_SCHEMA_VERSION:
            validate_codex_main_task_projection(task_artifact)
        else:
            validate_codex_work_package_task_projection(task_artifact)

    expected_raw_task_fields = {
        "task_source": task["task_source"]["locator"],
        "allowed_files": inp["scope"]["raw_caller_strings"]["allowed_files"],
        "forbidden_files": inp["scope"]["raw_caller_strings"]["forbidden_files"],
    }
    for field, expected in expected_raw_task_fields.items():
        value = task_artifact.get(field)
        if not isinstance(value, str) or not value:
            raise EvidencePacketError("TASK_ARTIFACT_MISSING", f"raw task {field} missing or invalid")
        if value != expected:
            raise EvidencePacketError("IDENTITY_DRIFT", f"raw task {field} drift")

    status_value = completion_artifact.get("status")
    if not isinstance(status_value, str):
        raise EvidencePacketError("COMPLETION_STATUS_MISSING", "completion status missing or invalid")
    if status_value not in TERMINAL_COMPLETION_STATUSES:
        raise EvidencePacketError("COMPLETION_NONTERMINAL", f"completion status {status_value!r} not terminal")
    exit_code = completion_artifact.get("exit_code")
    if "exit_code" in completion_artifact and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        raise EvidencePacketError("COMPLETION_EXIT_INVALID", "completion exit_code must be an integer")


def verify_snapshot_content_hash(repo_root, inp, root_identity=None):
    raw = inp["subject"]["raw_artifacts"]
    snapshot = _read_bound_json_artifact(
        repo_root,
        raw["changed_file_snapshot"],
        "changed file snapshot",
        root_identity,
    )
    validate_snapshot_schema(snapshot)

    diff_content = _read_bound_artifact_bytes(
        repo_root, raw["diff"], "diff", root_identity
    )
    expected_absent = _extract_diff_absent_set(diff_content)

    for file_path, snapshot_entry in snapshot["files"].items():
        current = _locator_identity_strict(repo_root, file_path, root_identity)
        if file_path in expected_absent:
            if snapshot_entry["state"] != "absent":
                raise EvidencePacketError(
                    "SNAPSHOT_STATE_MISMATCH",
                    f"deleted or renamed source is not recorded absent: {file_path}",
                )
            if current is not None:
                raise EvidencePacketError(
                    "SNAPSHOT_EXPECTED_ABSENT", f"deleted or renamed source still exists: {file_path}"
                )
            continue
        if snapshot_entry["state"] != "present":
            raise EvidencePacketError(
                "SNAPSHOT_STATE_MISMATCH",
                f"present diff path is recorded absent: {file_path}",
            )
        if current is None:
            raise EvidencePacketError(
                "SNAPSHOT_CONTENT_MISMATCH", f"snapshot member is missing: {file_path}"
            )
        actual_hash = sha256_file_strict(repo_root, file_path, root_identity)
        if actual_hash != snapshot_entry["sha256"]:
            raise EvidencePacketError("SNAPSHOT_CONTENT_MISMATCH", f"snapshot content hash mismatch for {file_path}")


def verify_snapshot_and_reconciliation(repo_root, inp, root_identity=None):
    raw = inp["subject"]["raw_artifacts"]
    snapshot = _read_bound_json_artifact(
        repo_root,
        raw["changed_file_snapshot"],
        "changed file snapshot",
        root_identity,
    )
    validate_snapshot_schema(snapshot)

    diff_content = _read_bound_artifact_bytes(
        repo_root, raw["diff"], "diff", root_identity
    )
    diff_files = extract_diff_file_set(diff_content)

    att = inp["subject"]["main_agent_attestation"]
    attested_changed_files = att["result_fields"]["changed_files"]
    scope_changed_files = inp["scope"]["changed_files"]
    snapshot_files = list(snapshot["files"].keys())

    expected_recon = reconcile_snapshot_scope(
        attested_changed_files,
        scope_changed_files,
        snapshot_files,
        diff_files,
        inp["scope"]["normalized"],
        inp["scope"]["raw_caller_strings"],
    )

    actual_recon = inp["scope"]["three_way_reconciliation"]
    for key in RECONCILIATION_KEYS:
        if actual_recon.get(key) != expected_recon[key]:
            raise EvidencePacketError("RECONCILIATION_MISMATCH", f"three_way_reconciliation.{key} mismatch")

    if expected_recon["status"] != "PASS":
        raise EvidencePacketError("RECONCILIATION_FAILED", f"scope reconciliation failed: {expected_recon}")


def build_packet_body(inp, publication_id):
    att = inp["subject"]["main_agent_attestation"]
    body = {
        "schema_version": SCHEMA_VERSION,
        "publication_id": publication_id,
        "task": {
            "task_id": inp["task"]["task_id"],
            "task_version": inp["task"]["task_version"],
            "change_version": inp["task"]["change_version"],
            "task_source": {
                "locator": inp["task"]["task_source"]["locator"],
                "sha256": inp["task"]["task_source"]["sha256"],
            },
        },
        "subject": {
            "raw_artifacts": dict(
                **{
                    key: {
                        "locator": inp["subject"]["raw_artifacts"][key]["locator"],
                        "sha256": inp["subject"]["raw_artifacts"][key]["sha256"],
                    }
                    for key in REQUIRED_RAW_ARTIFACT_KEYS
                },
                tests=[
                    {"locator": t["locator"], "sha256": t["sha256"]}
                    for t in inp["subject"]["raw_artifacts"]["tests"]
                ],
            ),
            "main_agent_attestation": {
                "actor_id": att["actor_id"],
                "reviewed_at": att["reviewed_at"],
                "result_fields": {
                    f: copy.deepcopy(att["result_fields"][f])
                    for f in REQUIRED_RESULT_FIELDS
                },
                "field_source_bindings": {f: att["field_source_bindings"][f] for f in REQUIRED_RESULT_FIELDS},
            },
            "identity": {f: inp["subject"]["identity"][f] for f in REQUIRED_IDENTITY_FIELDS},
        },
        "scope": {
            "changed_files": sorted(inp["scope"]["changed_files"]),
            "raw_caller_strings": {
                "allowed_files": inp["scope"]["raw_caller_strings"]["allowed_files"],
                "forbidden_files": inp["scope"]["raw_caller_strings"]["forbidden_files"],
            },
            "normalized": {
                "allowed_files": copy.deepcopy(inp["scope"]["normalized"]["allowed_files"]),
                "forbidden_files": copy.deepcopy(inp["scope"]["normalized"]["forbidden_files"]),
                "canonical_file_claims": copy.deepcopy(inp["scope"]["normalized"]["canonical_file_claims"]),
            },
            "three_way_reconciliation": copy.deepcopy(inp["scope"]["three_way_reconciliation"]),
        },
    }

    fingerprint_body = {k: v for k, v in body.items() if k not in ("publication_id", "content_fingerprint")}
    body["content_fingerprint"] = hashlib.sha256(canonical_json(fingerprint_body)).hexdigest()

    return body


def validate_semantics(
    repo_root,
    inp,
    *,
    packet_body=None,
    publication_id=None,
    root_identity=None,
):
    if root_identity is None:
        root_identity = _directory_identity_strict(repo_root, "REPO_INVALID")
    _assert_repo_binding(repo_root, root_identity)
    validate_structure_strict(inp)
    semantic_input = copy.deepcopy(inp)
    if inp != semantic_input:
        raise EvidencePacketError("PACKET_INPUT_DRIFT", "input changed while being captured")
    validate_structure_strict(semantic_input)
    validate_locators_strict(semantic_input)
    verify_artifact_hashes_strict(repo_root, semantic_input, root_identity)
    reconcile_identity_strict(repo_root, semantic_input, root_identity)
    verify_snapshot_content_hash(repo_root, semantic_input, root_identity)
    verify_snapshot_and_reconciliation(repo_root, semantic_input, root_identity)
    # Close the read/parse window: every parsed artifact and every current changed
    # file must still match the bytes that were bound at function entry.
    verify_artifact_hashes_strict(repo_root, semantic_input, root_identity)
    verify_snapshot_content_hash(repo_root, semantic_input, root_identity)
    _assert_repo_binding(repo_root, root_identity)
    if inp != semantic_input:
        raise EvidencePacketError("PACKET_INPUT_DRIFT", "input changed during validation")
    if publication_id is not None:
        expected = build_packet_body(semantic_input, publication_id)
        if packet_body is not None and packet_body != expected:
            raise EvidencePacketError(
                "PACKET_INPUT_DRIFT",
                "packet body no longer matches the complete current structured input",
            )
        return expected
    if packet_body is not None:
        raise EvidencePacketError("PACKET_INVALID", "publication_id is required for packet validation")
    return True


def _inode_identity(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EvidencePacketError("PUBLISH_STAT_FAILED", f"cannot inspect {path}: {exc}")
    return st.st_dev, st.st_ino, st.st_mode


def _entry_identity(dir_fd, name):
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise EvidencePacketError("PUBLISH_STAT_FAILED", f"cannot inspect {name}: {exc}")
    return st.st_dev, st.st_ino, st.st_mode


def _is_owned_regular_at(dir_fd, name, expected):
    identity = _entry_identity(dir_fd, name)
    return (
        identity is not None
        and stat.S_ISREG(identity[2])
        and identity[:2] == expected[:2]
    )


def _unlink_private_at(dir_fd, name):
    """Remove a freshly generated private name; no shared publication name is used."""
    try:
        os.unlink(name, dir_fd=dir_fd)
    except BaseException as exc:
        try:
            if _entry_identity(dir_fd, name) is None:
                return True
        except EvidencePacketError:
            pass
        raise EvidencePacketError("PUBLISH_UNLINK_FAILED", f"cannot unlink private path {name}: {exc}")
    if _entry_identity(dir_fd, name) is not None:
        raise EvidencePacketError("PUBLISH_UNLINK_FAILED", f"private path remains after unlink: {name}")
    return True


def _restore_foreign_at(dir_fd, isolated_name, original_name):
    isolated_identity = _entry_identity(dir_fd, isolated_name)
    if isolated_identity is None:
        raise EvidencePacketError(
            "PUBLISH_FOREIGN_RESTORE_FAILED", "isolated foreign entry is missing"
        )
    link_error = None
    try:
        # A hard link provides the required no-overwrite restore primitive.  A
        # newer publication appearing at original_name wins with FileExistsError.
        os.link(
            isolated_name,
            original_name,
            src_dir_fd=dir_fd,
            dst_dir_fd=dir_fd,
            follow_symlinks=False,
        )
    except BaseException as exc:
        link_error = exc
    restored_identity = _entry_identity(dir_fd, original_name)
    if (
        restored_identity is None
        or restored_identity[:2] != isolated_identity[:2]
    ):
        raise EvidencePacketError(
            "PUBLISH_FOREIGN_RESTORE_FAILED",
            f"cannot restore foreign entry without overwrite: {link_error}",
        )
    _unlink_private_at(dir_fd, isolated_name)


def _isolate_owned_at(dir_fd, name, expected, prefix):
    """Move an owned entry to a private name, retrying failures without deleting by name."""
    last_error = None
    for _ in range(3):
        if not _is_owned_regular_at(dir_fd, name, expected):
            return None
        isolated_name = f".{prefix}.{uuid.uuid4().hex}"
        try:
            os.rename(
                name,
                isolated_name,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
        except BaseException as exc:
            last_error = exc
        isolated_identity = _entry_identity(dir_fd, isolated_name)
        if isolated_identity is None:
            continue
        if (
            stat.S_ISREG(isolated_identity[2])
            and isolated_identity[:2] == expected[:2]
        ):
            return isolated_name
        _restore_foreign_at(dir_fd, isolated_name, name)
        raise EvidencePacketError(
            "PUBLISH_OWNERSHIP_LOST",
            f"{name} changed before isolation; foreign entry preserved",
        )
    raise EvidencePacketError(
        "PUBLISH_ISOLATE_FAILED", f"cannot isolate owned path {name}: {last_error}"
    )


def _unlink_owned_at(dir_fd, name, expected, prefix="delete"):
    removed_owned_entry = False
    for _ in range(16):
        isolated_name = _isolate_owned_at(dir_fd, name, expected, prefix)
        if isolated_name is None:
            return removed_owned_entry
        try:
            _unlink_private_at(dir_fd, isolated_name)
        except EvidencePacketError:
            isolated_now = _entry_identity(dir_fd, isolated_name)
            if (
                isolated_now is not None
                and stat.S_ISREG(isolated_now[2])
                and isolated_now[:2] == expected[:2]
            ):
                try:
                    _restore_foreign_at(dir_fd, isolated_name, name)
                except BaseException:
                    pass
            raise
        removed_owned_entry = True
        if not _is_owned_regular_at(dir_fd, name, expected):
            return True
    raise EvidencePacketError(
        "PUBLISH_UNLINK_FAILED", f"owned path {name} was repeatedly recreated"
    )


def _rollback_final_at(dir_fd, output_name, expected):
    return _unlink_owned_at(dir_fd, output_name, expected, "rollback")


def _best_effort_rollback_at(dir_fd, output_name, tmp_name, expected):
    errors = []
    try:
        _rollback_final_at(dir_fd, output_name, expected)
    except BaseException as exc:
        errors.append(str(exc))
    try:
        _unlink_owned_at(dir_fd, tmp_name, expected)
    except BaseException as exc:
        errors.append(str(exc))
    try:
        os.fsync(dir_fd)
    except BaseException as exc:
        errors.append(f"directory fsync after rollback failed: {exc}")
    return errors


def _assert_directory_binding(output_dir, expected):
    binding_fd = None
    try:
        binding_fd = _open_directory_path_strict(output_dir)
        st = os.fstat(binding_fd)
        identity = (st.st_dev, st.st_ino, st.st_mode)
    except BaseException as exc:
        raise EvidencePacketError(
            "PUBLISH_DIR_DRIFT", f"cannot resolve publication directory safely: {exc}"
        )
    finally:
        if binding_fd is not None:
            try:
                os.close(binding_fd)
            except OSError:
                pass
    if not stat.S_ISDIR(identity[2]) or identity[:2] != expected[:2]:
        raise EvidencePacketError(
            "PUBLISH_DIR_DRIFT", "publication directory path no longer names the opened directory"
        )


def _verify_published_file_at(dir_fd, output_name, expected_inode, expected_hash):
    if not _is_owned_regular_at(dir_fd, output_name, expected_inode):
        raise EvidencePacketError(
            "PUBLISH_OWNERSHIP_LOST", "final packet is not owned by this publication attempt"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        fd = os.open(output_name, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise EvidencePacketError("PUBLISH_FINAL_VERIFY_FAILED", f"cannot open final packet: {exc}")
    read_error = None
    chunks = []
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino) != expected_inode[:2] or not stat.S_ISREG(fst.st_mode):
            raise EvidencePacketError("PUBLISH_OWNERSHIP_LOST", "final packet changed while opening")
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    except BaseException as exc:
        read_error = exc
    try:
        os.close(fd)
    except BaseException as exc:
        if read_error is None:
            read_error = exc
    if read_error is not None:
        if isinstance(read_error, EvidencePacketError):
            raise read_error
        raise EvidencePacketError("PUBLISH_FINAL_VERIFY_FAILED", f"cannot read final packet: {read_error}")
    actual_hash = hashlib.sha256(b"".join(chunks)).hexdigest()
    if actual_hash != expected_hash:
        raise EvidencePacketError("PUBLISH_CONTENT_DRIFT", "final packet bytes changed during publication")


def _close_directory_fd(dir_fd):
    if dir_fd is None:
        return
    try:
        os.close(dir_fd)
    except OSError:
        # The directory has already been fsynced. A close error cannot be retried
        # without risking EBADF or an unrelated reused descriptor.
        return


def publish_packet_atomic(
    repo_root, packet_body, publication_id, inp, root_identity
):
    canonical_bytes = canonical_json(packet_body)
    packet_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    output_dir = os.path.join(repo_root, "tmp", "quality", "evidence")
    output_name = f"{publication_id}.json"
    tmp_name = None
    tmp_identity = None
    dir_fd = None

    try:
        try:
            dir_fd = _open_or_create_directory_beneath(
                repo_root, "tmp/quality/evidence", root_identity
            )
            dir_stat = os.fstat(dir_fd)
            if not stat.S_ISDIR(dir_stat.st_mode):
                raise EvidencePacketError("PUBLISH_DIR_INVALID", "publication directory is not a directory")
            dir_identity = (dir_stat.st_dev, dir_stat.st_ino, dir_stat.st_mode)
            _assert_directory_binding(output_dir, dir_identity)
            if _entry_identity(dir_fd, output_name) is not None:
                raise EvidencePacketError("PUBLISH_EXISTS", f"packet already exists: {output_name}")
        except EvidencePacketError:
            raise
        except BaseException as exc:
            raise EvidencePacketError("PUBLISH_SETUP_FAILED", f"publication setup failed: {exc}")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = None
        for _ in range(16):
            candidate = f".tmp.{publication_id}.{uuid.uuid4().hex}"
            try:
                fd = os.open(candidate, flags, 0o644, dir_fd=dir_fd)
                tmp_name = candidate
                break
            except FileExistsError:
                continue
            except BaseException as exc:
                raise EvidencePacketError("PUBLISH_OPEN_FAILED", f"cannot create temp packet: {exc}")
        if fd is None:
            raise EvidencePacketError("PUBLISH_RACE", "cannot allocate a unique temp packet")

        try:
            # O_EXCL establishes ownership of this private name. Bind that name
            # to an inode before the first descriptor fstat so an fstat fault can
            # still clean this attempt without touching an unrelated entry.
            tmp_identity = _entry_identity(dir_fd, tmp_name)
            if tmp_identity is None or not stat.S_ISREG(tmp_identity[2]):
                raise EvidencePacketError(
                    "PUBLISH_TEMP_INVALID", "created temp packet is not a regular file"
                )
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise EvidencePacketError(
                    "PUBLISH_TEMP_INVALID", "temp packet is not a regular file"
                )
            if (st.st_dev, st.st_ino) != tmp_identity[:2]:
                raise EvidencePacketError(
                    "PUBLISH_TEMP_INVALID", "temp packet name and descriptor differ"
                )
        except BaseException as exc:
            try:
                os.close(fd)
            except BaseException:
                pass
            fd = None
            if tmp_identity is not None:
                _best_effort_rollback_at(
                    dir_fd, output_name, tmp_name, tmp_identity
                )
            if isinstance(exc, EvidencePacketError):
                raise
            raise EvidencePacketError(
                "PUBLISH_OPEN_FAILED", f"cannot bind temp packet identity: {exc}"
            )

        write_error = None
        try:
            offset = 0
            while offset < len(canonical_bytes):
                try:
                    written = os.write(fd, canonical_bytes[offset:])
                except InterruptedError:
                    continue
                if isinstance(written, bool) or not isinstance(written, int) or written <= 0:
                    raise EvidencePacketError("PUBLISH_WRITE_FAILED", "packet write made no progress")
                offset += written
            os.fsync(fd)
            st = os.fstat(fd)
            if (
                not stat.S_ISREG(st.st_mode)
                or (st.st_dev, st.st_ino) != tmp_identity[:2]
            ):
                raise EvidencePacketError("PUBLISH_TEMP_INVALID", "temp packet is not a regular file")
        except BaseException as exc:
            write_error = exc
        try:
            os.close(fd)
        except BaseException as exc:
            if write_error is None:
                write_error = exc
        fd = None
        if write_error is not None:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            if isinstance(write_error, EvidencePacketError):
                raise write_error
            raise EvidencePacketError("PUBLISH_WRITE_FAILED", f"temp write/fsync/close failed: {write_error}")

        try:
            _assert_directory_binding(output_dir, dir_identity)
            validate_semantics(
                repo_root,
                inp,
                packet_body=packet_body,
                publication_id=publication_id,
                root_identity=root_identity,
            )
        except BaseException as exc:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            if isinstance(exc, EvidencePacketError):
                raise
            raise EvidencePacketError("PUBLISH_PRELINK_FAILED", f"prelink validation failed: {exc}")

        link_exception = None
        try:
            os.link(
                tmp_name,
                output_name,
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
                follow_symlinks=False,
            )
        except BaseException as exc:
            link_exception = exc
        try:
            link_owned = _is_owned_regular_at(dir_fd, output_name, tmp_identity)
        except EvidencePacketError as exc:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            raise exc
        if link_exception is not None:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            if isinstance(link_exception, FileExistsError):
                raise EvidencePacketError("PUBLISH_RACE", f"concurrent publication detected: {output_name}")
            raise EvidencePacketError("PUBLISH_LINK_FAILED", f"hardlink publication failed: {link_exception}")
        if not link_owned:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            raise EvidencePacketError("PUBLISH_OWNERSHIP_LOST", "hardlink did not create the owned final packet")

        try:
            _assert_directory_binding(output_dir, dir_identity)
            _verify_published_file_at(dir_fd, output_name, tmp_identity, packet_sha256)
            _assert_directory_binding(output_dir, dir_identity)
            validate_semantics(
                repo_root,
                inp,
                packet_body=packet_body,
                publication_id=publication_id,
                root_identity=root_identity,
            )
        except BaseException as exc:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            if isinstance(exc, EvidencePacketError):
                raise
            raise EvidencePacketError("PUBLISH_POSTLINK_FAILED", f"postlink validation failed: {exc}")

        try:
            removed = _unlink_owned_at(dir_fd, tmp_name, tmp_identity)
            if not removed:
                raise EvidencePacketError("PUBLISH_TMP_OWNERSHIP_LOST", "temp packet ownership changed")
        except BaseException as exc:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            raise EvidencePacketError("PUBLISH_TMP_CLEANUP_FAILED", f"temp cleanup failed: {exc}")

        try:
            _assert_directory_binding(output_dir, dir_identity)
            os.fsync(dir_fd)
        except BaseException as exc:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            raise EvidencePacketError("PUBLISH_DIR_FSYNC_FAILED", f"directory fsync failed: {exc}")

        try:
            _assert_directory_binding(output_dir, dir_identity)
            _verify_published_file_at(dir_fd, output_name, tmp_identity, packet_sha256)
            _assert_directory_binding(output_dir, dir_identity)
            _assert_repo_binding(repo_root, root_identity)
        except BaseException as exc:
            _best_effort_rollback_at(dir_fd, output_name, tmp_name, tmp_identity)
            if isinstance(exc, EvidencePacketError):
                raise
            raise EvidencePacketError("PUBLISH_FINAL_VERIFY_FAILED", f"final verification failed: {exc}")

        return {
            "locator": f"tmp/quality/evidence/{output_name}",
            "sha256": packet_sha256,
        }
    finally:
        _close_directory_fd(dir_fd)


def materialize(repo_root, input_data, publication_id=None):
    if publication_id is None:
        raise EvidencePacketError("PUBLISH_ID_REQUIRED", "publication_id is required")
    if not isinstance(publication_id, str) or not UUID_V4_PATTERN.fullmatch(publication_id):
        raise EvidencePacketError("PUBLISH_ID_INVALID", f"publication_id not UUIDv4: {publication_id!r}")

    if not isinstance(repo_root, (str, os.PathLike)):
        raise EvidencePacketError("REPO_INVALID", "repo_root must be a path")
    repo_root = os.fspath(repo_root)
    if os.path.islink(repo_root):
        raise EvidencePacketError("REPO_SYMLINK", f"repo_root is a symlink: {repo_root}")

    repo_root = os.path.abspath(repo_root)
    _check_path_components_for_symlinks(repo_root)
    root_identity = _directory_identity_strict(repo_root, "REPO_INVALID")

    packet_body = validate_semantics(
        repo_root,
        input_data,
        publication_id=publication_id,
        root_identity=root_identity,
    )
    publication = publish_packet_atomic(
        repo_root, packet_body, publication_id, input_data, root_identity
    )

    return {
        "packet": packet_body,
        "publication": publication,
    }


def materialize_codex_main_task_evidence(
    repo_root, projection, *, raw_artifacts, main_agent_attestation, scope, publication_id,
):
    """Materialize one explicit Main-Agent projection through the normal validator.

    The projection must already be an immutable runner artifact.  This adapter
    only assembles the fixed structured input; it performs no command execution,
    log inference, temporary-directory discovery, or issuer selection.
    """
    validate_codex_main_task_projection(projection, "main projection")
    if not isinstance(raw_artifacts, dict) or set(raw_artifacts) != set(RAW_ARTIFACT_CONTAINER_KEYS):
        raise EvidencePacketError("STRUCT_INVALID", "Main raw_artifacts must use the canonical descriptor set")
    task_descriptor = raw_artifacts["task"]
    if not isinstance(task_descriptor, dict) or set(task_descriptor) != RAW_ARTIFACT_KEYS:
        raise EvidencePacketError("STRUCT_INVALID", "Main task descriptor is invalid")
    actual = _read_json_dict_strict(repo_root, task_descriptor["locator"], "main projection artifact")
    if actual != projection or sha256_file_strict(repo_root, task_descriptor["locator"]) != task_descriptor["sha256"]:
        raise EvidencePacketError("IDENTITY_MISMATCH", "Main projection artifact is not the supplied immutable projection")
    return materialize(
        repo_root,
        {
            "task": {
                "task_id": projection["task_id"], "task_version": projection["task_version"],
                "change_version": projection["change_version"],
                "task_source": {"locator": "planning/workstreams.yaml", "sha256": sha256_file_strict(repo_root, "planning/workstreams.yaml")},
            },
            "subject": {
                "raw_artifacts": raw_artifacts, "main_agent_attestation": main_agent_attestation,
                "identity": {field: projection[field] for field in REQUIRED_IDENTITY_FIELDS},
            },
            "scope": scope,
        },
        publication_id,
    )


def verify_packet_strict(repo_root, packet_locator, packet_sha256):
    try:
        if not isinstance(repo_root, (str, os.PathLike)):
            raise EvidencePacketError("VERIFY_REPO_INVALID", "repo_root must be a path")
        repo_root = os.fspath(repo_root)
        if os.path.islink(repo_root):
            raise EvidencePacketError("REPO_SYMLINK", f"repo_root is a symlink: {repo_root}")
        repo_root = os.path.abspath(repo_root)
        _check_path_components_for_symlinks(repo_root)
        root_identity = _directory_identity_strict(repo_root, "VERIFY_REPO_INVALID")

        check_locator_safety_strict(packet_locator)
        prefix = "tmp/quality/evidence/"
        if not packet_locator.startswith(prefix):
            raise EvidencePacketError("VERIFY_LOCATOR_NAMESPACE", "packet locator is outside evidence namespace")
        basename = packet_locator[len(prefix):]
        if "/" in basename or not basename.endswith(".json"):
            raise EvidencePacketError("VERIFY_LOCATOR_NAMESPACE", "packet locator must name one packet file")
        locator_publication_id = basename[:-5]
        if not UUID_V4_PATTERN.fullmatch(locator_publication_id):
            raise EvidencePacketError("VERIFY_LOCATOR_NAMESPACE", "packet filename must be canonical UUIDv4")
        _check_sha256_lowercase_hex(packet_sha256, "packet_sha256")

        packet_bytes = _read_file_bytes_strict(
            repo_root, packet_locator, root_identity
        )
        actual_sha256 = hashlib.sha256(packet_bytes).hexdigest()
        if actual_sha256 != packet_sha256:
            raise EvidencePacketError(
                "VERIFY_HASH_MISMATCH",
                f"packet hash mismatch: expected {packet_sha256}, got {actual_sha256}",
            )
        packet = _parse_json_value_strict(
            packet_bytes, "packet", invalid_code="VERIFY_INVALID_JSON"
        )
        _require_exact_keys(packet, PACKET_KEYS, "packet")
        _check_no_issuer_fields(packet, "packet")
        if canonical_json(packet) != packet_bytes:
            raise EvidencePacketError("VERIFY_NONCANONICAL", "packet bytes are not canonical JSON")
        if packet["schema_version"] != SCHEMA_VERSION:
            raise EvidencePacketError("VERIFY_SCHEMA", "packet schema version mismatch")
        publication_id = packet["publication_id"]
        if not isinstance(publication_id, str) or not UUID_V4_PATTERN.fullmatch(publication_id):
            raise EvidencePacketError("VERIFY_INVALID", "publication_id must be canonical UUIDv4")
        if publication_id != locator_publication_id:
            raise EvidencePacketError("VERIFY_ID_LOCATOR_MISMATCH", "publication_id differs from locator basename")
        _check_sha256_lowercase_hex(packet["content_fingerprint"], "content_fingerprint")

        input_view = {
            "task": copy.deepcopy(packet["task"]),
            "subject": copy.deepcopy(packet["subject"]),
            "scope": copy.deepcopy(packet["scope"]),
        }
        validate_semantics(
            repo_root,
            input_view,
            packet_body=packet,
            publication_id=publication_id,
            root_identity=root_identity,
        )
        if sha256_file_strict(
            repo_root, packet_locator, root_identity
        ) != packet_sha256:
            raise EvidencePacketError(
                "VERIFY_HASH_MISMATCH", "packet bytes changed during semantic verification"
            )
        return packet
    except EvidencePacketError:
        raise
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise EvidencePacketError("VERIFY_INVALID", f"packet verification failed: {exc}")


def main():
    parser = argparse.ArgumentParser(description="Strict explicit evidence packet materializer")
    sub = parser.add_subparsers(dest="command")

    mat = sub.add_parser("materialize", help="Materialize an evidence packet")
    mat.add_argument("--repo-root", default=".", help="Repository root directory")
    mat.add_argument("--input", required=True, help="Input JSON file")
    mat.add_argument("--publication-id", required=True, help="Publication UUIDv4")

    ver = sub.add_parser("verify", help="Verify an existing evidence packet")
    ver.add_argument("--repo-root", default=".", help="Repository root directory")
    ver.add_argument("--packet-locator", required=True, help="Packet file locator")
    ver.add_argument("--packet-sha256", required=True, help="Expected packet SHA-256")

    args = parser.parse_args()

    if args.command == "materialize":
        try:
            try:
                with open(args.input, "rb") as stream:
                    input_bytes = stream.read()
            except OSError as exc:
                raise EvidencePacketError(
                    "INPUT_UNREADABLE", f"cannot read input JSON: {exc}"
                )
            input_data = _parse_json_value_strict(
                input_bytes, "input", invalid_code="INPUT_INVALID_JSON"
            )
            result = materialize(args.repo_root, input_data, args.publication_id)
            output = {
                "ok": True,
                "publication_id": result["packet"]["publication_id"],
                "publication_locator": result["publication"]["locator"],
                "publication_sha256": result["publication"]["sha256"],
            }
            print(json.dumps(output, indent=2, sort_keys=True))
        except EvidencePacketError as e:
            print(json.dumps({"ok": False, "code": e.code, "message": e.message}, indent=2, sort_keys=True), file=sys.stderr)
            sys.exit(1)
    elif args.command == "verify":
        try:
            packet = verify_packet_strict(
                args.repo_root, args.packet_locator, args.packet_sha256
            )
            print(json.dumps({"ok": True, "publication_id": packet["publication_id"]}, indent=2, sort_keys=True))
        except EvidencePacketError as e:
            print(json.dumps({"ok": False, "code": e.code, "message": e.message}, indent=2, sort_keys=True), file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
