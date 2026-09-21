"""Read-only status for immutable acceptance records."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from scripts.acceptance.records import RecordError, list_layer, load_submission

def query_status(root: str | Path, *, submission_id: str) -> dict[str, Any]:
    repo=Path(root).resolve()
    try:
        submission=load_submission(repo, submission_id)
        validations=list_layer(repo,"validations","submission_id",submission_id)
        reviews=list_layer(repo,"reviews","submission_id",submission_id)
        checks=list_layer(repo,"checks","submission_id",submission_id)
    except RecordError as exc:
        state="not-found" if exc.code=="record-not-found" else "invalid"
        return {"submission_id":submission_id,"state":state,"reason":exc.code}
    state="checked" if checks else "reviewed" if reviews else "validated" if validations else "submitted"
    return {"submission_id":submission_id,"state":state,"task_id":submission["task_requirements"]["task_id"],
            "validations":[{"validation_id":x["validation_id"],"result":x["result"]} for x in validations],
            "reviews":[{"review_id":x["review_id"],"result":x["result"]} for x in reviews],
            "checks":[{"check_id":x["check_id"],"result":x["result"]} for x in checks]}
