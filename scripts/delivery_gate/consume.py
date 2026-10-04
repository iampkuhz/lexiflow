"""只读消费已经完成的正式 Delivery Gate PASS 链。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts.delivery_gate.check import _evaluate_conditions


def consume_existing_pass(root: str | Path, *, submission_id: str) -> dict[str, Any]:
    """重核唯一既有完整 PASS 链，不签发 receipt 或运行交付命令。"""
    result, chain = _evaluate_conditions(
        root, submission_id=submission_id, publish_missing=False
    )
    result = {**result, "published": False, "delivery_rerun": False}
    if result.get("result") != "PASS" or chain is None:
        result.pop("proof", None)
        return result
    try:
        submission = chain["submission"]
        validation = chain["validation"]
        review = chain["review"]
        check = chain["check"]
        requirements = submission["task_requirements"]
        proof = {
            "submission_id": submission["submission_id"],
            "submission_content_hash": submission["content_hash"],
            "validation_id": validation["validation_id"],
            "validation_content_hash": validation["content_hash"],
            "review_id": review["review_id"] if review else None,
            "review_content_hash": review["content_hash"] if review else None,
            "check_id": check["check_id"],
            "check_content_hash": check["content_hash"],
            "task_id": requirements["task_id"],
            "task_version": requirements["task_version"],
            "change_version": requirements["change_version"],
            "frozen_input_fingerprint": validation["frozen_input_fingerprint"],
            "verification_report": validation["verification_report"],
        }
    except Exception:
        return {
            "submission_id": submission_id,
            "result": "BLOCKED",
            "reason": "proof-binding-invalid",
            "published": False,
            "delivery_rerun": False,
        }
    result["proof"] = proof
    return result
