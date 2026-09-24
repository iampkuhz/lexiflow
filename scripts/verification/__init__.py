"""独立 Verification 模块；提供仓库基线、变更范围和冻结输入的公开 API。"""

from __future__ import annotations

from scripts.verification.scenarios import (
    freeze_inputs,
    verify_changes,
    verify_repository,
)
from scripts.verification.reports import persist_report, read_report

__all__ = [
    "freeze_inputs",
    "persist_report",
    "read_report",
    "verify_changes",
    "verify_repository",
]
