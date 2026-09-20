"""Compatibility import for the formal Gate entrypoint.

Use scripts/gates/formal_gate.py for formal plan/run/status/issuer operations,
scripts/gates/change_verify.py for final-diff review, and
scripts/gates/repository_verify.py for checkout health/readiness.
"""
from scripts.gates.formal_gate import *  # noqa: F401,F403

if __name__ == "__main__":
    from scripts.gates.formal_gate import main
    raise SystemExit(main())
