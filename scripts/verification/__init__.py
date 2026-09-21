"""Independent verification module.

Public API:
    verify_repository(root, *, check_ids=None) -> dict
    verify_changes(root, *, base=None, expected_paths=()) -> dict

This module does NOT import scripts.gates, scripts.harness, or read
task catalogs, phase profiles, or formal identity materials.
"""
from __future__ import annotations

from scripts.verification.scenarios import freeze_inputs, verify_changes, verify_repository
from scripts.verification.reports import persist_report, read_report

__all__ = ["freeze_inputs", "persist_report", "read_report", "verify_changes", "verify_repository"]
