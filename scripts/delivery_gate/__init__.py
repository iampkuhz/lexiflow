"""正式 Delivery Gate 场景入口：submit 绑定任务与证据，validate 独立运行冻结输入，review 审阅证据，check 核对 receipt；status 只读。各阶段不能互代。"""

from __future__ import annotations

from scripts.delivery_gate.submit import submit, SubmissionError
from scripts.delivery_gate.validate import validate, ValidationError
from scripts.delivery_gate.review import review, ReviewError
from scripts.delivery_gate.check import check_conditions, CheckError
from scripts.delivery_gate.status import query_status

__all__ = [
    "submit",
    "SubmissionError",
    "validate",
    "ValidationError",
    "review",
    "ReviewError",
    "check_conditions",
    "CheckError",
    "query_status",
]
