"""Real daily-verification fixture; only agents runtime is mocked by callers."""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml

from scripts.acceptance.evidence.acceptance_record_store import (
    canonical_bytes,
    delivery_gate_locator,
    publish_bytes,
    publish_json,
)
from scripts.acceptance.evidence.task_review_diff import review_patch
from scripts.acceptance.task_definition.task_acceptance_plan import acceptance_plan
from scripts.acceptance.task_definition.task_acceptance_requirements import (
    load_task_requirements,
)
from scripts.acceptance.task_definition.task_acceptance_scope import (
    derive_task_subject,
    subject_paths,
)
from scripts.verification import (
    freeze_inputs,
    persist_report,
    read_report,
    verify_profile,
)
from scripts.verification.planning.change_risk_assessment import assess


def write_declarations(path, checks):
    """Gate 独立夹具只产生计数 JSON 检查，不依赖 Verification 测试模块。"""
    execution_fields = (
        "command",
        "timeout_seconds",
        "required_environment",
        "input_paths",
    )
    document = {
        "checks": [],
        "scenario_policy": {
            "ci_quick": {"always_required_check_ids": [], "formal_only_check_ids": []}
        },
    }
    for check in checks:
        execution = {field: check[field] for field in execution_fields}
        execution["transaction_reuse"] = check.get("transaction_reuse", False)
        scenarios = check.get("scenarios") or [check["scope"]]
        if "triggers" in check:
            execution["triggers"] = check["triggers"]
        document["checks"].append(
            {
                "check_id": check["check_id"],
                "module_dependencies": check.get("module_dependencies", []),
                "scenarios": scenarios,
                **execution,
            }
        )
    document["scenario_policy"]["ci_quick"]["formal_only_check_ids"] = [
        check["check_id"]
        for check in document["checks"]
        if check["check_id"]
        in {"eng.release.lifecycle-runtime", "eng.release.candidate-runtime"}
        and "repository-baseline" in check["scenarios"]
    ]
    path.write_text(yaml.safe_dump(document, sort_keys=False))


PRODUCER_SESSION = "11111111-1111-4111-9111-111111111111"
VALIDATOR_SESSION = "22222222-2222-4222-9222-222222222222"
REVIEWER_SESSION = "33333333-3333-4333-9333-333333333333"
PRODUCER_ACTOR = f"codex-session-{PRODUCER_SESSION}"
VALIDATOR_ACTOR = f"codex-session-{VALIDATOR_SESSION}"
REVIEWER_ACTOR = f"codex-session-{REVIEWER_SESSION}"
TASK_ID = "LF-TSK-TEST-0001"


@dataclass
class MockRuntime:
    context: dict[str, str]
    proof: dict[str, Any]


def make_mock_runtime(session_id: str) -> MockRuntime:
    return MockRuntime(
        {
            "actor_id": f"codex-session-{session_id}",
            "session_id": session_id,
            "parent_session_id": session_id,
            "client": "codex",
        },
        {
            "schema_version": "lexiflow.codex-local-session-proof.v2",
            "thread_id": session_id,
            "workspace": "/test/workspace",
            "sources": [{"thread_id": session_id, "metadata_sha256": "a" * 64}],
        },
    )


def mock_authority(session_id: str):
    return {
        "trust_boundary": "trusted-local-user-not-platform-cryptographic-attestation",
        "verifier": "scripts.agents.identity.codex_actor_identity.discover",
        "verified_at": "2026-09-21T00:00:00Z",
    }


class DeliveryGateFixture:
    def __init__(self):
        self.proof_patch = patch(
            "scripts.agents.identity.codex_actor_identity.verify_proof",
            return_value=None,
        )
        self.proof_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.submission_id = None
        self.validation_id = None
        self.review_id = None
        self.report_id = None
        for relative, content, mode in self._base_files():
            self.write(relative, content).chmod(mode)

    @classmethod
    @cache
    def _base_files(cls):
        """只复用合成基仓字节；每个实例重新写文件，绝不共享 Git 或证据状态。"""
        with tempfile.TemporaryDirectory() as directory:
            base = object.__new__(cls)
            base.root = Path(directory)
            base._write_catalog()
            base._write_declarations()
            base.write("protected.py", "before\n")
            base.write(".gitignore", "tmp/\n__pycache__/\n")
            base._git("init")
            base._git("config", "user.email", "fixture@example.test")
            base._git("config", "user.name", "fixture")
            base._git("add", ".")
            base._git("commit", "-m", "base")
            # 不保留临时路径、可变容器或硬链接；子类的声明各自冻结。
            return tuple(
                (
                    str(path.relative_to(base.root)),
                    path.read_bytes(),
                    path.stat().st_mode & 0o777,
                )
                for path in sorted(base.root.rglob("*"))
                if path.is_file()
            )

    def cleanup(self):
        self.proof_patch.stop()
        self.temp.cleanup()

    def _git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
        )

    def write(self, relative, content):
        p = self.root / relative
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content.encode() if isinstance(content, str) else content)
        return p

    def _write_catalog(self):
        task = {
            "id": TASK_ID,
            "task_version": 1,
            "change_version": "1.0.0",
            "depends_on": [],
            "required_check_ids": ["fixture.change"],
            "allowed_files": ["src/test.py"],
            "forbidden_files": ["secrets/**"],
            "file_claims": [{"path": "src/**"}],
        }
        self.write(
            "planning/workstreams.yaml",
            yaml.safe_dump(
                {
                    "workstreams": [
                        {
                            "id": "LF-WS-TEST",
                            "epics": [{"capabilities": [{"seed_tasks": [task]}]}],
                        }
                    ]
                },
                sort_keys=False,
            ),
        )

    def _write_declarations(self):
        project_root = Path(__file__).resolve().parents[2]
        self.write(
            "config/engineering/risk.yaml",
            (project_root / "config/engineering/risk.yaml").read_bytes(),
        )
        self.write(
            "scripts/verification/planning/change_risk_assessment.py",
            (
                project_root / "scripts/verification/planning/change_risk_assessment.py"
            ).read_bytes(),
        )
        self.write(
            "fixture_check.py",
            "import json; print(json.dumps({'status':'PASS','checks_run':1,'failures':0,'errors':0,'skipped':0,'reason':''}))\n",
        )
        common = {
            "command": ["python3", "fixture_check.py"],
            "executable": "python3",
            "cwd": ".",
            "timeout_seconds": 10,
            "triggers": [{"path": "src/"}],
            "module_dependencies": [],
            "required_environment": ["python3"],
            "input_paths": [
                "fixture_check.py",
                "protected.py",
                "config/engineering/module-checks.yaml",
            ],
        }
        checks = [
            {
                "check_id": "fixture.baseline",
                "scope": "repository-baseline",
                **common,
            },
            {
                "check_id": "fixture.change",
                "scope": "change-targeted",
                **common,
            },
        ]
        # Canonical release checks expose both latest scenario views without aliases.
        for check_id, trigger in (
            (
                "eng.release.lifecycle-runtime",
                "never/fixture-lifecycle/",
            ),
            (
                "eng.release.candidate-runtime",
                "never/fixture-candidate/",
            ),
        ):
            checks.append(
                {
                    "check_id": check_id,
                    **common,
                    "scenarios": ["repository-baseline", "change-targeted"],
                    "triggers": [{"path": trigger}],
                }
            )
        write_declarations(self.root / "config/engineering/module-checks.yaml", checks)

    def create_verification_report(self, *, result="PASS"):
        self.write("src/test.py", "content of src/test.py\n")
        report = verify_profile(
            self.root,
            frozen_inputs=freeze_inputs(
                self.root, base="HEAD", verification_scope="development-change"
            ),
        )
        if result != "PASS":
            report = {**report, "result": result}
        descriptor = persist_report(self.root, report)
        self.report_id = report["run_id"]
        return {**report, "publication": descriptor}

    def create_submission(self, *, task_id=TASK_ID):
        if not self.report_id:
            self.create_verification_report()
        requirements = load_task_requirements(self.root, task_id)
        report, desc = read_report(self.root, self.report_id)
        full_changed = report["scope_review"]["changed_files"]
        subject = derive_task_subject(self.root, requirements, full_changed)
        risk = assess(
            self.root,
            base=report["base"],
            required_check_ids=tuple(requirements["required_check_ids"]),
            _subject_paths=tuple(subject_paths(subject)),
        )
        plan = acceptance_plan(self.root, risk, requirements)
        freeze = freeze_inputs(
            self.root,
            required_check_ids=requirements["required_check_ids"],
            verification_scope=plan["verification_scope"],
            base=risk["base"],
            _subject_changed_files=subject_paths(subject)
            if plan["verification_scope"] == "development-change"
            else None,
        )
        sid = str(uuid.uuid4())
        self.submission_id = sid
        changed = subject_paths(subject)
        patch = publish_bytes(
            self.root,
            f"tmp/quality/delivery-gate/current/submissions/{sid}.diff.patch",
            review_patch(self.root, risk["base"], changed),
        )
        runtime = make_mock_runtime(PRODUCER_SESSION)
        authority = mock_authority(PRODUCER_SESSION)
        record = {
            "schema_version": "lexiflow.delivery-gate-submission.v7",
            "submission_id": sid,
            "task_requirements": requirements,
            "risk_assessment": risk,
            "acceptance_plan": plan,
            "change_report": desc,
            "diff": patch,
            "submitter_identity": runtime.context,
            "runtime_proof": runtime.proof,
            "authority": authority,
            "producer": {"kind": "current-codex-task"},
            "verification_freeze": freeze,
            "task_subject": subject,
            "release_subject": None,
            "created_at": "2026-09-21T00:00:00Z",
        }
        publish_json(self.root, delivery_gate_locator("submissions", sid), record)
        return record

    def create_validation(self, submission_id=None, result="PASS"):
        from scripts.acceptance.evidence.acceptance_record_store import load_submission

        sid = submission_id or self.submission_id
        sub = load_submission(self.root, sid)
        vid = str(uuid.uuid4())
        self.validation_id = vid
        report = verify_profile(self.root, frozen_inputs=sub["verification_freeze"])
        if result != "PASS":
            report = {
                **report,
                "result": result,
                "checks": [
                    {**check, "status": result} for check in report.get("checks", [])
                ],
            }
        desc = publish_bytes(
            self.root,
            f"tmp/quality/delivery-gate/current/validations/{vid}/report.json",
            canonical_bytes(report),
        )
        rec = {
            "schema_version": "lexiflow.delivery-gate-validation.v5",
            "validation_id": vid,
            "submission_id": sid,
            "submission_content_hash": sub["content_hash"],
            "validator_identity": make_mock_runtime(VALIDATOR_SESSION).context,
            "runtime_proof": make_mock_runtime(VALIDATOR_SESSION).proof,
            "authority": mock_authority(VALIDATOR_SESSION),
            "verification_report": desc,
            "frozen_input_fingerprint": sub["verification_freeze"]["input_fingerprint"],
            "gaps": [],
            "result": result,
            "created_at": "2026-09-21T00:01:00Z",
        }
        publish_json(self.root, delivery_gate_locator("validations", vid), rec)
        return rec

    def create_review(self, submission_id=None, validation_id=None, decision="PASS"):
        from scripts.acceptance.evidence.acceptance_record_store import (
            load_layer,
            load_submission,
        )

        sid, vid = (
            submission_id or self.submission_id,
            validation_id or self.validation_id,
        )
        sub, val = (
            load_submission(self.root, sid),
            load_layer(self.root, "validations", vid),
        )
        rid = str(uuid.uuid4())
        self.review_id = rid
        rec = {
            "schema_version": "lexiflow.delivery-gate-review.v5",
            "review_id": rid,
            "submission_id": sid,
            "validation_id": vid,
            "submission_content_hash": sub["content_hash"],
            "validation_content_hash": val["content_hash"],
            "reviewer_identity": make_mock_runtime(REVIEWER_SESSION).context,
            "runtime_proof": make_mock_runtime(REVIEWER_SESSION).proof,
            "authority": mock_authority(REVIEWER_SESSION),
            "findings": [
                {
                    "finding_id": "f",
                    "severity": decision,
                    "code": "fixture",
                    "evidence": [{"detail": "reviewed"}],
                }
            ],
            "result": decision,
            "created_at": "2026-09-21T00:02:00Z",
            "delivery_rerun": False,
        }
        publish_json(self.root, delivery_gate_locator("reviews", rid), rec)
        return rec
