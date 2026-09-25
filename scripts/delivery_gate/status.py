"""只读呈现不可变 Delivery Gate 记录的状态，不签发新结论。"""

from __future__ import annotations
from pathlib import Path
from typing import Any
from scripts.delivery_gate.records import RecordError, list_layer, load_submission


def query_status(root: str | Path, *, submission_id: str) -> dict[str, Any]:
    """读取精确 submission 的各层状态，不推进或重跑正式 Gate。"""
    repo = Path(root).resolve()
    try:
        submission = load_submission(repo, submission_id)
        validations = list_layer(repo, "validations", "submission_id", submission_id)
        reviews = list_layer(repo, "reviews", "submission_id", submission_id)
        checks = list_layer(repo, "checks", "submission_id", submission_id)
    except RecordError as exc:
        state = "not-found" if exc.code == "record-not-found" else "invalid"
        return {"submission_id": submission_id, "state": state, "reason": exc.code}
    state = (
        "checked"
        if checks
        else "reviewed"
        if reviews
        else "validated"
        if validations
        else "submitted"
    )
    return {
        "submission_id": submission_id,
        "state": state,
        "task_id": submission["task_requirements"]["task_id"],
        "validations": [
            {"validation_id": x["validation_id"], "result": x["result"]}
            for x in validations
        ],
        "reviews": [
            {"review_id": x["review_id"], "result": x["result"]} for x in reviews
        ],
        "checks": [{"check_id": x["check_id"], "result": x["result"]} for x in checks],
    }
