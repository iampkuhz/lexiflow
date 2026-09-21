"""Formal acceptance module.

Four named scenarios plus read-only status:

- ``submit``: bind actual diff/snapshot/task requirements and real execution
  identity; return a unique submission reference.
- ``validate``: in an independent session, call ``scripts.verification``
  public API on the same frozen input; report coverage gaps, required
  unexecuted/skipped, input drift and real environment failures.
- ``review``: consume frozen diff, validation evidence and explicit reviewer
  findings; never rerun delivery commands.
- ``check``: verify validation/review/dependency receipts and hash DAG;
  never rerun delivery commands.
- ``status``: read-only query for submission state.

Dependency direction: acceptance -> verification (public), acceptance ->
agents (public facts). The reverse is forbidden.

Identity is discovered from real runtime via ``scripts.agents.local_codex_runtime``,
not caller-supplied. All records are content-bound with SHA256 hashes.
"""
from __future__ import annotations

from scripts.acceptance.submit import submit, SubmissionError
from scripts.acceptance.validate import validate, ValidationError
from scripts.acceptance.review import review, ReviewError
from scripts.acceptance.check import check_conditions, CheckError
from scripts.acceptance.status import query_status

__all__ = [
    "submit", "SubmissionError",
    "validate", "ValidationError",
    "review", "ReviewError",
    "check_conditions", "CheckError",
    "query_status",
]
