"""正式 Delivery Gate 场景入口：submit 绑定任务与证据，validate 独立运行冻结输入，review 审阅证据，check 核对 receipt；status 只读。各阶段不能互代。"""

from __future__ import annotations

from scripts.acceptance.task_operations.submit_task_for_acceptance import (
    submit,
    SubmissionError,
)
from scripts.acceptance.task_operations.run_independent_task_validation import (
    validate_batch,
    ValidationError,
)
from scripts.acceptance.task_operations.record_independent_task_review import (
    review,
    ReviewError,
)
from scripts.acceptance.task_operations.evaluate_task_acceptance import (
    check_conditions,
    CheckError,
)
from scripts.acceptance.task_operations.show_task_acceptance_status import query_status
from scripts.acceptance.evidence.verify_existing_acceptance_chain import (
    consume_existing_pass,
)
from scripts.acceptance.evidence.verify_candidate_acceptance_evidence import (
    consume_candidate_pass,
)

__all__ = [
    "submit",
    "SubmissionError",
    "validate_batch",
    "ValidationError",
    "review",
    "ReviewError",
    "check_conditions",
    "CheckError",
    "query_status",
    "consume_existing_pass",
    "consume_candidate_pass",
]
