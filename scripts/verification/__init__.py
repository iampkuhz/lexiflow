"""独立 Verification 模块；提供仓库基线、变更范围和冻结输入的公开 API。"""

from __future__ import annotations

from scripts.verification.reports.verification_report_store import (
    persist_report,
    read_report,
)
from scripts.verification.execution.check_plan_runner import (
    freeze_inputs,
    verify_frozen_inputs,
    verify_profile,
    verify_profiles,
    verify_repository,
)

__all__ = [
    "freeze_inputs",
    "persist_report",
    "read_report",
    "verify_frozen_inputs",
    "verify_profile",
    "verify_profiles",
    "verify_repository",
]
