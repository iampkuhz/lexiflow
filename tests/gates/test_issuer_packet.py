from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

from scripts.gates.issuer_packet import (
    ATTESTATION_SCHEMA,
    PACKET_SCHEMA,
    REASON_CODES,
    IssuerPacketError,
    IssuerPacketMaterializer,
    canonical_json_bytes,
    sha256_bytes,
)


class IssuerPacketMaterializerTest(unittest.TestCase):
    NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)
    HUMAN_KEY = b"human-authority-test-key"
    CI_KEY = b"ci-authority-test-key"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[2] / "harness/gate-issuer-authorities.yaml"
        target = self.root / "harness/gate-issuer-authorities.yaml"
        target.parent.mkdir(parents=True)
        shutil.copyfile(source, target)
        self.counter = 1
        self.codex_context = {
            "actor_id": "codex-main",
            "session_id": self._uuid(),
            "parent_session_id": self._uuid(),
            "client": "codex",
        }

    def _uuid(self) -> str:
        value = str(uuid.UUID(int=self.counter, version=4))
        self.counter += 1
        return value

    def _keys(self, key_id: str) -> bytes | None:
        return {
            "lexiflow-human-gate-v1": self.HUMAN_KEY,
            "lexiflow-ci-gate-v1": self.CI_KEY,
        }.get(key_id)

    def _materializer(
        self,
        *,
        codex_context: dict[str, str] | None = None,
        key_resolver=None,
        clock=None,
    ) -> IssuerPacketMaterializer:
        return IssuerPacketMaterializer(
            self.root,
            trusted_codex_context=self.codex_context if codex_context is None else codex_context,
            key_resolver=self._keys if key_resolver is None else key_resolver,
            now=clock or (lambda: self.NOW),
        )

    def _write_json(self, locator: str, value: dict) -> tuple[str, str]:
        path = self.root / locator
        path.parent.mkdir(parents=True, exist_ok=True)
        content = canonical_json_bytes(value)
        path.write_bytes(content)
        return locator, sha256_bytes(content)

    def _request(self, *kinds: str, instance_id: str | None = None) -> dict:
        return {
            "issuer_instance_id": instance_id or self._uuid(),
            "receipt_kinds": list(kinds or ("TASK_VALIDATION",)),
        }

    def _subject(self, **overrides) -> dict:
        subject = {
            "parent_session_id": self._uuid(),
            "agent_id": "subject_agent",
            "run_id": self._uuid(),
            "session_id": self._uuid(),
            "client": "subject-runner",
            "parent_client": "codex",
        }
        subject.update(overrides)
        return subject

    def _qoder_evidence(self, *, mutate_task=None, mutate_completion=None) -> tuple[str, str, dict]:
        run_id = self._uuid()
        task = {
            "task_id": "LF-TSK-QLT-0999",
            "task_version": 1,
            "change_version": "1.0.0",
            "agent_id": "agent_fixture",
            "run_id": run_id,
            "session_id": self._uuid(),
            "client": "qoder",
            "parent_client": "codex",
            "parent_session_id": self._uuid(),
        }
        completion = {
            **task,
            "status": "finished",
            "exit_code": 0,
        }
        if mutate_task:
            mutate_task(task)
        if mutate_completion:
            mutate_completion(completion)
        base = f"tmp/qoder-tasks/{run_id}"
        self._write_json(f"{base}/task.json", task)
        locator, digest = self._write_json(f"{base}/completion.json", completion)
        os.utime(self.root / locator, (self.NOW.timestamp(), self.NOW.timestamp()))
        return locator, digest, completion

    def _codex_evidence(self, **overrides) -> tuple[str, str, dict]:
        data = {
            "schema_version": ATTESTATION_SCHEMA,
            "actor_type": "codex",
            "attestation_id": self._uuid(),
            "audience": "lexiflow-gate",
            "nonce": self._uuid(),
            "issued_at": "2026-09-16T11:59:00Z",
            "expires_at": "2026-09-16T12:05:00Z",
            **self.codex_context,
        }
        data.update(overrides)
        locator = f"tmp/quality/authority/codex/{self._uuid()}.json"
        ref = self._write_json(locator, data)
        return *ref, data

    def _signed_evidence(self, actor_type: str, **overrides) -> tuple[str, str, dict]:
        self.assertIn(actor_type, {"human", "ci"})
        key_id, key, actor_field, actor_id = (
            ("lexiflow-human-gate-v1", self.HUMAN_KEY, "operator_id", "lexiflow-maintainer")
            if actor_type == "human"
            else ("lexiflow-ci-gate-v1", self.CI_KEY, "workload_id", "lexiflow-gate-ci")
        )
        data = {
            "schema_version": ATTESTATION_SCHEMA,
            "actor_type": actor_type,
            "attestation_id": self._uuid(),
            "audience": "lexiflow-gate",
            "nonce": self._uuid(),
            "issued_at": "2026-09-16T11:59:00Z",
            "expires_at": "2026-09-16T12:05:00Z",
            actor_field: actor_id,
            "session_id": self._uuid(),
            "client": actor_type,
        }
        if actor_type == "ci":
            data.update(
                {
                    "repository": "iampkuhz/lexiflow",
                    "ref": "refs/heads/main",
                    "environment": "lexiflow-gate",
                }
            )
        data.update(overrides)
        signature = hmac.new(key, canonical_json_bytes(data), hashlib.sha256).hexdigest()
        data["signature"] = {
            "algorithm": "hmac-sha256",
            "key_id": key_id,
            "value": signature,
        }
        locator = f"tmp/quality/authority/{actor_type}/{self._uuid()}.json"
        ref = self._write_json(locator, data)
        return *ref, data

    def _assert_error(self, code: str, call) -> IssuerPacketError:
        self.assertIn(code, REASON_CODES)
        with self.assertRaises(IssuerPacketError) as raised:
            call()
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.result, "FAIL")
        self.assertEqual(raised.exception.category, "issuer-untrusted")
        return raised.exception

    def _materialize_ref(self, materializer, request, ref, *, subject=None):
        if subject is None:
            subject = self._subject()
        return materializer.materialize(
            request,
            authority_evidence_locator=ref[0],
            authority_evidence_sha256=ref[1],
            subject_identity=subject,
        )

    def test_qoder_positive_binds_runner_task_and_completion(self) -> None:
        ref = self._qoder_evidence()
        result = self._materialize_ref(self._materializer(), self._request(), ref)
        packet = result.packet
        self.assertEqual(packet["schema_version"], PACKET_SCHEMA)
        self.assertEqual(packet["actor_type"], "qoder")
        self.assertEqual(packet["actor_id"], "agent_fixture")
        self.assertEqual(packet["authorized_receipt_kinds"], ["TASK_VALIDATION"])
        self.assertEqual(packet["authority"]["verifier_id"], "qoder.runner-provenance.v1")
        self.assertEqual(packet["authority"]["verifier_abi"], 1)
        self.assertEqual([p["kind"] for p in packet["authority"]["provenance"]], ["runner-task", "runner-completion"])
        stored = (self.root / result.locator).read_bytes()
        self.assertEqual(stored, canonical_json_bytes(packet))
        self.assertEqual(result.sha256, sha256_bytes(stored))
        self.assertEqual(list((self.root / result.locator).parent.glob("*.tmp")), [])
        self.assertNotIn("result", packet)
        self.assertNotIn("status", packet)

    def test_codex_positive_binds_current_host_session(self) -> None:
        ref = self._codex_evidence()
        result = self._materialize_ref(
            self._materializer(),
            self._request("CATALOG_DECISION", "TASK_VALIDATION"),
            ref,
        )
        self.assertEqual(result.packet["actor_type"], "codex")
        self.assertEqual(result.packet["session_id"], self.codex_context["session_id"])
        self.assertEqual(
            result.packet["authorized_receipt_kinds"],
            ["TASK_VALIDATION", "CATALOG_DECISION"],
        )

    def test_codex_subject_accepts_canonical_actor_without_weakening_qoder_identity(self) -> None:
        result = self._materialize_ref(
            self._materializer(), self._request(), self._codex_evidence(),
            subject=self._subject(agent_id="/root", client="codex"),
        )
        self.assertEqual(result.packet["actor_type"], "codex")
        self._assert_error(
            "invalid-subject-identity",
            lambda: self._materialize_ref(
                self._materializer(), self._request(), self._codex_evidence(),
                subject=self._subject(agent_id="/root", client="qoder"),
            ),
        )

    def test_external_session_identities_accept_uuid7_but_packet_ids_remain_uuid4(self) -> None:
        session_v7 = "018f47a6-3a2b-7c4d-8e5f-123456789abc"
        parent_v7 = "018f47a6-3a2b-7c4d-8e5f-123456789abd"
        self.codex_context.update(
            {"session_id": session_v7, "parent_session_id": parent_v7}
        )
        codex = self._materialize_ref(
            self._materializer(),
            self._request("TASK_VALIDATION"),
            self._codex_evidence(),
        )
        self.assertEqual(codex.packet["session_id"], session_v7)
        self.assertEqual(codex.packet["parent_session_id"], parent_v7)
        self.assertEqual(uuid.UUID(codex.packet["issuer_instance_id"]).version, 4)

        def mutate_identity(value):
            value.update({"session_id": session_v7, "parent_session_id": parent_v7})

        qoder = self._materialize_ref(
            self._materializer(),
            self._request("TASK_VALIDATION"),
            self._qoder_evidence(
                mutate_task=mutate_identity, mutate_completion=mutate_identity
            ),
        )
        self.assertEqual(qoder.packet["session_id"], session_v7)
        self.assertEqual(qoder.packet["parent_session_id"], parent_v7)
        self.assertEqual(uuid.UUID(qoder.packet["issuer_instance_id"]).version, 4)

    def test_human_positive_uses_registered_operator_and_authenticated_attestation(self) -> None:
        ref = self._signed_evidence("human")
        result = self._materialize_ref(self._materializer(), self._request("INDEPENDENT_REVIEW"), ref)
        self.assertEqual(result.packet["actor_id"], "lexiflow-maintainer")
        self.assertEqual(result.packet["client"], "human")
        self.assertEqual(result.packet["authority"]["verifier_id"], "human.operator-record.v1")

    def test_ci_positive_uses_registered_workload_identity(self) -> None:
        ref = self._signed_evidence("ci")
        result = self._materialize_ref(self._materializer(), self._request("CATALOG_DECISION"), ref)
        self.assertEqual(result.packet["actor_id"], "lexiflow-gate-ci")
        self.assertEqual(result.packet["client"], "ci")
        self.assertEqual(result.packet["authority"]["verifier_id"], "ci.workload-identity.v1")

    def test_request_cannot_assert_authority_or_identity(self) -> None:
        ref = self._qoder_evidence()
        for field in (
            "verifier_id",
            "authority_id",
            "role",
            "authorized_receipt_kinds",
            "actor_type",
            "actor_id",
            "session_id",
            "client",
        ):
            with self.subTest(field=field):
                request = self._request()
                request[field] = "forged"
                self._assert_error(
                    "caller-authority-override",
                    lambda request=request: self._materialize_ref(self._materializer(), request, ref),
                )

    def test_registry_missing_malformed_or_symlink_fails_closed(self) -> None:
        registry = self.root / "harness/gate-issuer-authorities.yaml"
        original = registry.read_bytes()
        registry.unlink()
        self._assert_error("evidence-unavailable", self._materializer)
        registry.write_text("schema_version: wrong\n", encoding="utf-8")
        self._assert_error("invalid-authority-registry", self._materializer)
        registry.unlink()
        outside = self.root / "registry.yaml"
        outside.write_bytes(original)
        registry.symlink_to(outside)
        self._assert_error("unsafe-locator", self._materializer)

    def test_unavailable_authority_and_unknown_verifier_have_no_fallback(self) -> None:
        ref = self._qoder_evidence()
        registry = self.root / "harness/gate-issuer-authorities.yaml"
        data = yaml.safe_load(registry.read_text())
        data["authorities"]["qoder"]["available"] = False
        registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self._assert_error(
            "authority-unavailable",
            lambda: self._materialize_ref(self._materializer(), self._request(), ref),
        )
        data["authorities"]["qoder"]["available"] = True
        data["authorities"]["qoder"]["verifier_id"] = "fallback.must.not.run"
        registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self._assert_error(
            "unknown-verifier",
            lambda: self._materialize_ref(self._materializer(), self._request(), ref),
        )

    def test_duplicate_verifier_binding_is_a_malformed_registry(self) -> None:
        registry = self.root / "harness/gate-issuer-authorities.yaml"
        data = yaml.safe_load(registry.read_text())
        data["authorities"]["ci"]["verifier_id"] = data["authorities"]["human"]["verifier_id"]
        registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self._assert_error("invalid-authority-registry", self._materializer)

    def test_qoder_forged_identity_and_nonterminal_completion_fail(self) -> None:
        identity_ref = self._qoder_evidence(
            mutate_completion=lambda value: value.__setitem__("agent_id", "copied_agent")
        )
        self._assert_error(
            "identity-drift",
            lambda: self._materialize_ref(self._materializer(), self._request(), identity_ref),
        )
        terminal_ref = self._qoder_evidence(
            mutate_completion=lambda value: value.__setitem__("status", "running")
        )
        self._assert_error(
            "invalid-evidence",
            lambda: self._materialize_ref(self._materializer(), self._request(), terminal_ref),
        )

    def test_qoder_missing_runner_provenance_cannot_be_replaced_by_prose(self) -> None:
        locator, digest, completion = self._qoder_evidence()
        (self.root / locator).with_name("task.json").unlink()
        completion["callback"] = "queued; PASS"
        (self.root / locator).write_bytes(canonical_json_bytes(completion))
        digest = sha256_bytes((self.root / locator).read_bytes())
        self._assert_error(
            "evidence-unavailable",
            lambda: self._materialize_ref(
                self._materializer(), self._request(), (locator, digest)
            ),
        )

    def test_codex_requires_available_matching_host_context(self) -> None:
        ref = self._codex_evidence()
        self._assert_error(
            "authority-unavailable",
            lambda: self._materialize_ref(
                self._materializer(codex_context={}), self._request(), ref
            ),
        )
        drifted = dict(self.codex_context)
        drifted["session_id"] = self._uuid()
        self._assert_error(
            "identity-drift",
            lambda: self._materialize_ref(
                self._materializer(codex_context=drifted), self._request(), ref
            ),
        )

    def test_human_and_ci_forged_signatures_fail(self) -> None:
        for actor_type in ("human", "ci"):
            with self.subTest(actor_type=actor_type):
                locator, digest, data = self._signed_evidence(actor_type)
                data["signature"]["value"] = "0" * 64
                locator, digest = self._write_json(locator, data)
                self._assert_error(
                    "forged-attestation",
                    lambda: self._materialize_ref(
                        self._materializer(), self._request(), (locator, digest)
                    ),
                )

    def test_attestation_cannot_self_assert_role_or_authority(self) -> None:
        for field in ("role", "verifier_id", "authority_id", "authorized_receipt_kinds"):
            with self.subTest(field=field):
                ref = self._signed_evidence("human", **{field: "forged"})
                self._assert_error(
                    "caller-authority-override",
                    lambda ref=ref: self._materialize_ref(
                        self._materializer(), self._request(), ref
                    ),
                )

    def test_signed_attestations_enforce_audience_time_nonce_and_hash(self) -> None:
        for actor_type in ("human", "ci"):
            with self.subTest(actor_type=actor_type, case="audience"):
                ref = self._signed_evidence(actor_type, audience="other-service")
                self._assert_error(
                    "invalid-attestation",
                    lambda ref=ref: self._materialize_ref(self._materializer(), self._request(), ref),
                )
            with self.subTest(actor_type=actor_type, case="stale"):
                ref = self._signed_evidence(
                    actor_type,
                    issued_at="2026-09-16T11:00:00Z",
                    expires_at="2026-09-16T11:10:00Z",
                )
                self._assert_error(
                    "stale-attestation",
                    lambda ref=ref: self._materialize_ref(self._materializer(), self._request(), ref),
                )
            with self.subTest(actor_type=actor_type, case="nonce"):
                ref = self._signed_evidence(actor_type, nonce="not-a-uuid")
                self._assert_error(
                    "invalid-identity",
                    lambda ref=ref: self._materialize_ref(self._materializer(), self._request(), ref),
                )
            with self.subTest(actor_type=actor_type, case="nonce-v7"):
                ref = self._signed_evidence(
                    actor_type, nonce="018f47a6-3a2b-7c4d-8e5f-123456789abc"
                )
                self._assert_error(
                    "invalid-identity",
                    lambda ref=ref: self._materialize_ref(
                        self._materializer(), self._request(), ref
                    ),
                )
            with self.subTest(actor_type=actor_type, case="hash"):
                ref = self._signed_evidence(actor_type)
                self._assert_error(
                    "evidence-drift",
                    lambda ref=ref: self._materialize_ref(
                        self._materializer(), self._request(), (ref[0], "0" * 64)
                    ),
                )

    def test_ci_registry_constraints_are_required_at_initialization(self) -> None:
        registry = self.root / "harness/gate-issuer-authorities.yaml"
        original = registry.read_bytes()
        for field in ("repository", "environment", "allowed_ref_prefixes"):
            with self.subTest(field=field):
                data = yaml.safe_load(original)
                del data["authorities"]["ci"]["workloads"]["lexiflow-gate-ci"][field]
                registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
                self._assert_error("invalid-authority-registry", self._materializer)
                registry.write_bytes(original)
        data = yaml.safe_load(original)
        data["authorities"]["ci"]["workloads"]["lexiflow-gate-ci"]["repository"] = (
            "owner/group/repository"
        )
        registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self._assert_error("invalid-authority-registry", self._materializer)
        registry.write_bytes(original)
        for invalid_prefix in (
            "",
            "heads/",
            "refs/heads/*",
            "refs/../heads/",
            "refs/heads//double/",
            "refs/heads/space name/",
            "refs/heads/topic~/",
            "refs/heads/topic.lock/",
            "refs/heads/.hidden/",
            "refs/heads/trailing./",
            "refs/heads/topic@{/",
        ):
            with self.subTest(invalid_prefix=invalid_prefix):
                data = yaml.safe_load(original)
                data["authorities"]["ci"]["workloads"]["lexiflow-gate-ci"][
                    "allowed_ref_prefixes"
                ] = [invalid_prefix]
                registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
                self._assert_error("invalid-authority-registry", self._materializer)
                registry.write_bytes(original)
        for field in ("repository", "environment"):
            with self.subTest(field=field, case="empty"):
                data = yaml.safe_load(original)
                data["authorities"]["ci"]["workloads"]["lexiflow-gate-ci"][field] = " "
                registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
                self._assert_error("invalid-authority-registry", self._materializer)
                registry.write_bytes(original)

    def test_registry_configuration_views_are_deeply_immutable(self) -> None:
        mutation_cases = (
            (
                "qoder-authorization-append",
                lambda materializer: materializer.authorities["qoder"][
                    "authorized_receipt_kinds"
                ].append("CATALOG_DECISION"),
            ),
            (
                "qoder-authorization-replace",
                lambda materializer: materializer.authorities["qoder"].__setitem__(
                    "authorized_receipt_kinds", ["CATALOG_DECISION"]
                ),
            ),
            (
                "qoder-role",
                lambda materializer: materializer.registry["authorities"]["qoder"].__setitem__(
                    "role", "catalog-owner"
                ),
            ),
            (
                "qoder-evidence-roots",
                lambda materializer: materializer.authorities["qoder"][
                    "evidence_roots"
                ].append("tmp/quality/authority/qoder-attacker"),
            ),
            (
                "qoder-verifier",
                lambda materializer: materializer.authorities["qoder"].__setitem__(
                    "verifier_id", "attacker.verifier.v1"
                ),
            ),
            (
                "max-age",
                lambda materializer: setattr(materializer, "max_age_seconds", 999999),
            ),
            (
                "audience",
                lambda materializer: setattr(
                    materializer, "attestation_audience", "attacker-audience"
                ),
            ),
            (
                "store-locator",
                lambda materializer: setattr(
                    materializer, "store_locator", "tmp/quality/attacker-store"
                ),
            ),
            (
                "frozen-max-age",
                lambda materializer: setattr(
                    materializer._configuration, "max_age_seconds", 999999
                ),
            ),
            (
                "frozen-verifier-bindings",
                lambda materializer: materializer._configuration.verifier_bindings.__setitem__(
                    "qoder", ("attacker.verifier.v1", 1)
                ),
            ),
            (
                "frozen-evidence-roots",
                lambda materializer: materializer._configuration.evidence_roots.__setitem__(
                    "qoder", ("tmp/quality/authority/qoder-attacker",)
                ),
            ),
            (
                "operator-record",
                lambda materializer: materializer.authorities["human"]["actors"].__setitem__(
                    "attacker-operator",
                    materializer.authorities["human"]["actors"]["lexiflow-maintainer"],
                ),
            ),
            (
                "operator-record-role",
                lambda materializer: materializer.authorities["human"]["actors"][
                    "lexiflow-maintainer"
                ].__setitem__("role", "catalog-owner"),
            ),
            (
                "workload-record",
                lambda materializer: materializer.registry["authorities"]["ci"][
                    "workloads"
                ].__setitem__(
                    "attacker-workload",
                    materializer.authorities["ci"]["workloads"]["lexiflow-gate-ci"],
                ),
            ),
            (
                "workload-record-authorization",
                lambda materializer: materializer.authorities["ci"]["workloads"][
                    "lexiflow-gate-ci"
                ]["authorized_receipt_kinds"].append("INDEPENDENT_REVIEW"),
            ),
        )
        for name, mutate in mutation_cases:
            with self.subTest(name=name):
                materializer = self._materializer()
                with self.assertRaises((AttributeError, TypeError)):
                    mutate(materializer)

    def test_in_memory_qoder_authorization_cannot_expand_disk_registry_permissions(self) -> None:
        materializer = self._materializer()
        with self.assertRaises((AttributeError, TypeError)):
            materializer.authorities["qoder"]["authorized_receipt_kinds"].append(
                "CATALOG_DECISION"
            )
        evidence = self._qoder_evidence()
        request = self._request("CATALOG_DECISION")
        final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
        self._assert_error(
            "unauthorized-receipt-kind",
            lambda: self._materialize_ref(materializer, request, evidence),
        )
        self.assertFalse(final.exists())
        self.assertEqual(list(final.parent.glob("*.tmp")), [])
        claim_dir = final.parent / ".attestation-claims"
        self.assertEqual(list(claim_dir.glob("*.json")) if claim_dir.exists() else [], [])

    def test_registry_is_strictly_reparsed_for_initial_pre_and_post_link_trust(self) -> None:
        original_safe_load = yaml.safe_load
        with mock.patch(
            "scripts.gates.issuer_packet.yaml.safe_load", wraps=original_safe_load
        ) as safe_load:
            materializer = self._materializer()
            evidence = self._qoder_evidence()
            self._materialize_ref(materializer, self._request(), evidence)
        self.assertGreaterEqual(safe_load.call_count, 4)

    def test_same_registry_bytes_with_drifted_parsed_semantics_fail_closed(self) -> None:
        original_safe_load = yaml.safe_load
        for drift_call in (2, 3, 4):
            with self.subTest(drift_call=drift_call):
                parse_count = 0

                def semantic_drift(content):
                    nonlocal parse_count
                    parse_count += 1
                    parsed = original_safe_load(content)
                    if parse_count == drift_call:
                        parsed["authorities"]["qoder"][
                            "authorized_receipt_kinds"
                        ].append("CATALOG_DECISION")
                    return parsed

                evidence = self._qoder_evidence()
                request = self._request()
                final = self.root / (
                    f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
                )
                claim_dir = final.parent / ".attestation-claims"
                before_claims = (
                    set(claim_dir.glob("*.json")) if claim_dir.exists() else set()
                )
                try:
                    with mock.patch(
                        "scripts.gates.issuer_packet.yaml.safe_load",
                        side_effect=semantic_drift,
                    ):
                        materializer = self._materializer()
                        self._assert_error(
                            "evidence-drift",
                            lambda: self._materialize_ref(
                                materializer, request, evidence
                            ),
                        )
                    self.assertFalse(final.exists())
                    self.assertEqual(list(final.parent.glob("*.tmp")), [])
                    after_claims = (
                        set(claim_dir.glob("*.json")) if claim_dir.exists() else set()
                    )
                    self.assertEqual(after_claims, before_claims)
                finally:
                    final.unlink(missing_ok=True)
                    for temp_path in final.parent.glob("*.tmp"):
                        temp_path.unlink(missing_ok=True)
                    if claim_dir.exists():
                        for claim_path in (
                            set(claim_dir.glob("*.json")) - before_claims
                        ):
                            claim_path.unlink(missing_ok=True)

    def test_ci_repository_ref_and_environment_are_registry_bound(self) -> None:
        for mutation in (
            {"repository": "attacker/fork"},
            {"ref": "tags/untrusted"},
            {"environment": "production"},
        ):
            with self.subTest(mutation=mutation):
                ref = self._signed_evidence("ci", **mutation)
                self._assert_error(
                    "identity-drift",
                    lambda ref=ref: self._materialize_ref(
                        self._materializer(), self._request(), ref
                    ),
                )

    def test_ci_ref_uses_complete_git_ref_grammar_before_prefix_matching(self) -> None:
        invalid_refs = (
            "refs/heads/../escape",
            "refs/heads//double",
            "refs/heads/space name",
            "refs/heads/topic~1",
            "refs/heads/topic.lock",
            "refs/heads/.hidden",
            "refs/heads/trailing.",
            "refs/heads/topic@{upstream}",
            "refs/heads/topic^next",
            "refs/heads/topic:next",
            "refs/heads/topic?next",
            "refs/heads/topic*next",
            "refs/heads/topic[next",
            "refs/heads/topic\\next",
            "refs/heads/topic\tnext",
            "refs/heads/@",
            "refs/heads/",
            "@",
        )
        for ref_value in invalid_refs:
            with self.subTest(ref_value=ref_value):
                evidence = self._signed_evidence("ci", ref=ref_value)
                self._assert_error(
                    "identity-drift",
                    lambda evidence=evidence: self._materialize_ref(
                        self._materializer(), self._request(), evidence
                    ),
                )

    def test_ci_valid_nested_branch_and_pull_refs_remain_allowed(self) -> None:
        for ref_value in ("refs/heads/feature/issuer-hardening", "refs/pull/42/head"):
            with self.subTest(ref_value=ref_value):
                evidence = self._signed_evidence("ci", ref=ref_value)
                result = self._materialize_ref(
                    self._materializer(), self._request(), evidence
                )
                self.assertEqual(result.packet["actor_type"], "ci")

    def test_revoked_human_and_ci_records_fail(self) -> None:
        for actor_type, collection, actor_id in (
            ("human", "actors", "lexiflow-maintainer"),
            ("ci", "workloads", "lexiflow-gate-ci"),
        ):
            with self.subTest(actor_type=actor_type):
                registry = self.root / "harness/gate-issuer-authorities.yaml"
                data = yaml.safe_load(registry.read_text())
                data["authorities"][actor_type][collection][actor_id]["revoked"] = True
                registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
                ref = self._signed_evidence(actor_type)
                self._assert_error(
                    "revoked-actor",
                    lambda ref=ref: self._materialize_ref(self._materializer(), self._request(), ref),
                )
                data["authorities"][actor_type][collection][actor_id]["revoked"] = False
                registry.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def test_attestation_replay_is_rejected_across_packet_instances(self) -> None:
        ref = self._signed_evidence("human")
        materializer = self._materializer()
        self._materialize_ref(materializer, self._request(), ref)
        self._assert_error(
            "replayed-attestation",
            lambda: self._materialize_ref(self._materializer(), self._request(), ref),
        )

        original = ref[2]
        changed_bytes_same_identity = self._signed_evidence(
            "human",
            attestation_id=original["attestation_id"],
            nonce=original["nonce"],
            expires_at="2026-09-16T12:06:00Z",
        )
        self._assert_error(
            "replayed-attestation",
            lambda: self._materialize_ref(
                self._materializer(), self._request(), changed_bytes_same_identity
            ),
        )

    def test_attestation_id_and_nonce_are_independent_replay_claims(self) -> None:
        first = self._signed_evidence("human")
        self._materialize_ref(self._materializer(), self._request(), first)
        original = first[2]
        reused_id = self._signed_evidence(
            "human",
            attestation_id=original["attestation_id"],
        )
        self._assert_error(
            "replayed-attestation",
            lambda: self._materialize_ref(self._materializer(), self._request(), reused_id),
        )
        reused_nonce = self._signed_evidence("human", nonce=original["nonce"])
        self._assert_error(
            "replayed-attestation",
            lambda: self._materialize_ref(self._materializer(), self._request(), reused_nonce),
        )
        claims = list((self.root / "tmp/quality/issuers/.attestation-claims").glob("*.json"))
        self.assertEqual(len(claims), 2)

    def test_qoder_run_identity_is_a_persistent_replay_claim(self) -> None:
        ref = self._qoder_evidence()
        self._materialize_ref(self._materializer(), self._request(), ref)
        self._assert_error(
            "replayed-attestation",
            lambda: self._materialize_ref(self._materializer(), self._request(), ref),
        )

    def test_concurrent_shared_nonce_has_one_winner_and_rolls_back_loser_claim(self) -> None:
        nonce = self._uuid()
        refs = [self._signed_evidence("ci", nonce=nonce) for _ in range(2)]
        requests = [self._request(), self._request()]

        def attempt(pair):
            request, ref = pair
            try:
                self._materialize_ref(self._materializer(), request, ref)
                return "MATERIALIZED"
            except IssuerPacketError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(pool.map(attempt, zip(requests, refs)))
        self.assertEqual(outcomes, ["MATERIALIZED", "replayed-attestation"])
        claims = list((self.root / "tmp/quality/issuers/.attestation-claims").glob("*.json"))
        self.assertEqual(len(claims), 2)

    def test_concurrent_replay_has_one_exclusive_winner(self) -> None:
        ref = self._signed_evidence("ci")
        requests = [self._request(), self._request()]

        def attempt(request):
            try:
                self._materialize_ref(self._materializer(), request, ref)
                return "MATERIALIZED"
            except IssuerPacketError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(pool.map(attempt, requests))
        self.assertEqual(outcomes, ["MATERIALIZED", "replayed-attestation"])

    def test_receipt_kind_authorization_is_exact(self) -> None:
        ref = self._qoder_evidence()
        self._assert_error(
            "unauthorized-receipt-kind",
            lambda: self._materialize_ref(
                self._materializer(), self._request("CATALOG_DECISION"), ref
            ),
        )
        self._assert_error(
            "unsupported-receipt-kind",
            lambda: self._materialize_ref(
                self._materializer(), self._request("MADE_UP_KIND"), ref
            ),
        )

    def test_subject_identity_cannot_be_copied(self) -> None:
        ref = self._signed_evidence("human")
        self._assert_error(
            "copied-subject-identity",
            lambda: self._materialize_ref(
                self._materializer(),
                self._request(),
                ref,
                subject=self._subject(agent_id="lexiflow-maintainer"),
            ),
        )
        instance_id = self._uuid()
        self._assert_error(
            "copied-subject-identity",
            lambda: self._materialize_ref(
                self._materializer(),
                self._request(instance_id=instance_id),
                ref,
                subject=self._subject(run_id=instance_id),
            ),
        )

    def test_subject_identity_is_mandatory_and_schema_complete(self) -> None:
        ref = self._signed_evidence("human")
        materializer = self._materializer()
        request = self._request()
        kwargs = {
            "authority_evidence_locator": ref[0],
            "authority_evidence_sha256": ref[1],
        }
        self._assert_error(
            "invalid-subject-identity",
            lambda: materializer.materialize(request, subject_identity=None, **kwargs),
        )
        for field in (
            "parent_session_id",
            "agent_id",
            "run_id",
            "session_id",
            "client",
            "parent_client",
        ):
            with self.subTest(field=field):
                subject = self._subject()
                del subject[field]
                self._assert_error(
                    "invalid-subject-identity",
                    lambda subject=subject: materializer.materialize(
                        request, subject_identity=subject, **kwargs
                    ),
                )
        self._assert_error(
            "invalid-subject-identity",
            lambda: materializer.materialize(
                request,
                subject_identity={**self._subject(), "actor_type": "forged"},
                **kwargs,
            ),
        )
        for field, invalid in (
            ("parent_session_id", "not-a-uuid"),
            ("agent_id", "../agent"),
            ("run_id", "not-a-uuid"),
            ("session_id", "not-a-uuid"),
            ("client", ""),
            ("parent_client", "bad/client"),
        ):
            with self.subTest(field=field, invalid=invalid):
                self._assert_error(
                    "invalid-subject-identity",
                    lambda field=field, invalid=invalid: materializer.materialize(
                        request,
                        subject_identity=self._subject(**{field: invalid}),
                        **kwargs,
                    ),
                )

    def test_actor_and_run_copy_are_rejected_while_shared_host_session_is_allowed(self) -> None:
        human_ref = self._signed_evidence("human")
        human = human_ref[2]
        for mutation in (
            {"agent_id": "lexiflow-maintainer"},
        ):
            with self.subTest(mutation=mutation):
                self._assert_error(
                    "copied-subject-identity",
                    lambda mutation=mutation: self._materialize_ref(
                        self._materializer(),
                        self._request(),
                        human_ref,
                        subject=self._subject(**mutation),
                    ),
                )
        allowed = self._materialize_ref(
            self._materializer(), self._request(), human_ref,
            subject=self._subject(client="human", session_id=human["session_id"]),
        )
        self.assertEqual(allowed.packet["actor_type"], "human")
        qoder_ref = self._qoder_evidence()
        self._assert_error(
            "copied-subject-identity",
            lambda: self._materialize_ref(
                self._materializer(),
                self._request(),
                qoder_ref,
                subject=self._subject(run_id=qoder_ref[2]["run_id"]),
            ),
        )

    def test_registry_and_authority_bytes_cannot_drift(self) -> None:
        ref = self._signed_evidence("ci")
        materializer = self._materializer()
        registry = self.root / "harness/gate-issuer-authorities.yaml"
        registry.write_bytes(registry.read_bytes() + b"\n")
        self._assert_error(
            "evidence-drift",
            lambda: self._materialize_ref(materializer, self._request(), ref),
        )

        # A fresh materializer freezes the new registry bytes; the evidence hash
        # still prevents later authority-evidence mutation.
        materializer = self._materializer()
        (self.root / ref[0]).write_bytes((self.root / ref[0]).read_bytes() + b" ")
        self._assert_error(
            "evidence-drift",
            lambda: self._materialize_ref(materializer, self._request(), ref),
        )

    def test_duplicate_issuer_instance_never_overwrites_existing_packet(self) -> None:
        materializer = self._materializer()
        instance_id = self._uuid()
        first = self._signed_evidence("human")
        result = self._materialize_ref(
            materializer, self._request(instance_id=instance_id), first
        )
        original = (self.root / result.locator).read_bytes()
        second = self._signed_evidence("human")
        self._assert_error(
            "duplicate-packet-identity",
            lambda: self._materialize_ref(
                materializer, self._request(instance_id=instance_id), second
            ),
        )
        self.assertEqual((self.root / result.locator).read_bytes(), original)

    def test_partial_temp_write_or_install_error_leaves_no_final_packet(self) -> None:
        ref = self._signed_evidence("human")
        for failure in ("partial-write", "link-error"):
            with self.subTest(failure=failure):
                materializer = self._materializer()
                request = self._request()
                final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
                if failure == "partial-write":
                    def partial(path, content):
                        path.write_bytes(content[: max(1, len(content) // 2)])
                        raise OSError("injected partial write")

                    patcher = mock.patch.object(
                        materializer, "_write_temp_file", side_effect=partial, create=True
                    )
                else:
                    patcher = mock.patch("scripts.gates.issuer_packet.os.link", side_effect=OSError("injected link error"))
                with patcher:
                    self._assert_error(
                        "publication-failed",
                        lambda: self._materialize_ref(materializer, request, ref),
                    )
                self.assertFalse(final.exists())
                self.assertEqual(list(final.parent.glob("*.tmp")), [])

    def test_publish_critical_path_revalidates_authority_evidence(self) -> None:
        ref = self._signed_evidence("ci")
        materializer = self._materializer()
        request = self._request()
        final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
        original_install = getattr(materializer, "_install_packet_atomic", None)
        self.assertIsNotNone(original_install)

        def mutate_then_install(*args, **kwargs):
            evidence = self.root / ref[0]
            evidence.write_bytes(evidence.read_bytes() + b" ")
            return original_install(*args, **kwargs)

        with mock.patch.object(materializer, "_install_packet_atomic", side_effect=mutate_then_install):
            self._assert_error(
                "evidence-drift",
                lambda: self._materialize_ref(materializer, request, ref),
            )
        self.assertFalse(final.exists())

    def test_publish_critical_path_revalidates_registry_bytes(self) -> None:
        ref = self._signed_evidence("human")
        materializer = self._materializer()
        request = self._request()
        final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
        original_install = materializer._install_packet_atomic

        def mutate_then_install(*args, **kwargs):
            registry = self.root / "harness/gate-issuer-authorities.yaml"
            registry.write_bytes(registry.read_bytes() + b"\n")
            return original_install(*args, **kwargs)

        with mock.patch.object(materializer, "_install_packet_atomic", side_effect=mutate_then_install):
            self._assert_error(
                "evidence-drift",
                lambda: self._materialize_ref(materializer, request, ref),
            )
        self.assertFalse(final.exists())

    def test_real_link_window_registry_drift_rolls_back_final_and_claims(self) -> None:
        ref = self._signed_evidence("human")
        materializer = self._materializer()
        request = self._request()
        final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
        real_link = os.link

        def mutate_then_link(source, destination, **kwargs):
            registry = self.root / "harness/gate-issuer-authorities.yaml"
            registry.write_bytes(registry.read_bytes() + b"\n")
            return real_link(source, destination, **kwargs)

        with mock.patch("scripts.gates.issuer_packet.os.link", side_effect=mutate_then_link):
            self._assert_error(
                "evidence-drift",
                lambda: self._materialize_ref(materializer, request, ref),
            )
        self.assertFalse(final.exists())
        self.assertEqual(list(final.parent.glob("*.tmp")), [])
        self.assertEqual(list((final.parent / ".attestation-claims").glob("*.json")), [])

    def test_real_link_window_authority_drift_rolls_back_final_and_claims(self) -> None:
        ref = self._signed_evidence("ci")
        materializer = self._materializer()
        request = self._request()
        final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
        real_link = os.link

        def mutate_then_link(source, destination, **kwargs):
            evidence = self.root / ref[0]
            evidence.write_bytes(evidence.read_bytes() + b" ")
            return real_link(source, destination, **kwargs)

        with mock.patch("scripts.gates.issuer_packet.os.link", side_effect=mutate_then_link):
            self._assert_error(
                "evidence-drift",
                lambda: self._materialize_ref(materializer, request, ref),
            )
        self.assertFalse(final.exists())
        self.assertEqual(list(final.parent.glob("*.tmp")), [])
        self.assertEqual(list((final.parent / ".attestation-claims").glob("*.json")), [])

    def test_qoder_mtime_becoming_stale_in_link_window_rolls_back_final(self) -> None:
        ref = self._qoder_evidence()
        materializer = self._materializer()
        request = self._request()
        final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
        real_link = os.link

        def expire_then_link(source, destination, **kwargs):
            stale = (self.NOW - timedelta(seconds=901)).timestamp()
            os.utime(self.root / ref[0], (stale, stale))
            return real_link(source, destination, **kwargs)

        with mock.patch("scripts.gates.issuer_packet.os.link", side_effect=expire_then_link):
            self._assert_error(
                "stale-attestation",
                lambda: self._materialize_ref(materializer, request, ref),
            )
        self.assertFalse(final.exists())
        self.assertEqual(list((final.parent / ".attestation-claims").glob("*.json")), [])

    def test_clock_crossing_expiry_in_link_window_rolls_back_final(self) -> None:
        ref = self._signed_evidence("human")
        clock = {"now": self.NOW}
        materializer = self._materializer(clock=lambda: clock["now"])
        request = self._request()
        final = self.root / f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
        real_link = os.link

        def expire_then_link(source, destination, **kwargs):
            clock["now"] = self.NOW + timedelta(minutes=6)
            return real_link(source, destination, **kwargs)

        with mock.patch("scripts.gates.issuer_packet.os.link", side_effect=expire_then_link):
            self._assert_error(
                "stale-attestation",
                lambda: self._materialize_ref(materializer, request, ref),
            )
        self.assertFalse(final.exists())
        self.assertEqual(list((final.parent / ".attestation-claims").glob("*.json")), [])

    def test_stable_forged_verifier_returns_never_establish_trust(self) -> None:
        fixtures = (
            ("qoder", "_verify_qoder", self._qoder_evidence),
            ("codex", "_verify_codex", self._codex_evidence),
            ("human", "_verify_human", lambda: self._signed_evidence("human")),
            ("ci", "_verify_ci", lambda: self._signed_evidence("ci")),
        )
        registry_locator = "harness/gate-issuer-authorities.yaml"
        registry_bytes = (self.root / registry_locator).read_bytes()
        registry_hash = sha256_bytes(registry_bytes)
        for actor_type, verifier_name, evidence_factory in fixtures:
            evidence = evidence_factory()
            materializer = self._materializer()
            legitimate, _digest = materializer._resolve_verified_issuer(
                evidence[0], evidence[1]
            )
            other_actor_type = next(
                value for value in ("qoder", "codex", "human", "ci")
                if value != actor_type
            )
            replay_value_drift = list(legitimate.replay_claims)
            replay_value_drift[0] = (replay_value_drift[0][0], self._uuid())
            if actor_type == "qoder":
                replay_shape_drift = (
                    ("attestation-id", legitimate.replay_claims[0][1]),
                )
            else:
                replay_shape_drift = tuple(reversed(legitimate.replay_claims))
            auth_first = (legitimate.authorized_receipt_kinds[0],)
            auth_last = (legitimate.authorized_receipt_kinds[-1],)
            provenance_order_or_extra = (
                tuple(reversed(legitimate.provenance))
                if len(legitimate.provenance) > 1
                else legitimate.provenance + legitimate.provenance
            )
            mutations = {
                "actor-type": replace(legitimate, actor_type=other_actor_type),
                "actor-id": replace(legitimate, actor_id=f"forged-{actor_type}"),
                "parent-session-id": replace(
                    legitimate, parent_session_id=self._uuid()
                ),
                "session-id": replace(legitimate, session_id=self._uuid()),
                "client": replace(legitimate, client="forged-client"),
                "role": replace(legitimate, role="forged-role"),
                "authority-id": replace(
                    legitimate, authority_id="forged-authority"
                ),
                "verifier-id": replace(
                    legitimate, verifier_id="forged.verifier.v1"
                ),
                "verifier-abi": replace(legitimate, verifier_abi=2),
                "actor-run-id": replace(legitimate, actor_run_id=self._uuid()),
                "replay-value": replace(
                    legitimate, replay_claims=tuple(replay_value_drift)
                ),
                "replay-shape": replace(
                    legitimate, replay_claims=replay_shape_drift
                ),
                "authorization-subset": replace(
                    legitimate, authorized_receipt_kinds=auth_first
                ),
                "authorization-alternative": replace(
                    legitimate, authorized_receipt_kinds=auth_last
                ),
                "authorization-order": replace(
                    legitimate,
                    authorized_receipt_kinds=tuple(
                        reversed(legitimate.authorized_receipt_kinds)
                    ),
                ),
            }
            provenance = [dict(item) for item in legitimate.provenance]
            provenance[0] = {
                **provenance[0],
                "locator": registry_locator,
                "sha256": registry_hash,
            }
            mutations["provenance-other-existing"] = replace(
                legitimate, provenance=tuple(provenance)
            )
            provenance = [dict(item) for item in legitimate.provenance]
            provenance[0]["sha256"] = "0" * 64
            mutations["provenance-zero-hash"] = replace(
                legitimate, provenance=tuple(provenance)
            )
            provenance = [dict(item) for item in legitimate.provenance]
            provenance[0]["locator"] = "tmp/quality/authority/nonexistent.json"
            provenance[0]["sha256"] = "1" * 64
            mutations["provenance-nonexistent"] = replace(
                legitimate, provenance=tuple(provenance)
            )
            provenance = [dict(item) for item in legitimate.provenance]
            provenance[0]["kind"] = "forged-kind"
            mutations["provenance-kind"] = replace(
                legitimate, provenance=tuple(provenance)
            )
            mutations["provenance-set-missing"] = replace(
                legitimate, provenance=legitimate.provenance[:-1]
            )
            mutations["provenance-order-or-extra"] = replace(
                legitimate, provenance=provenance_order_or_extra
            )
            self.assertEqual(len(mutations), 21)

            for mutation_name, forged in mutations.items():
                with self.subTest(actor_type=actor_type, mutation=mutation_name):
                    request = self._request()
                    final = self.root / (
                        f"tmp/quality/issuers/{request['issuer_instance_id']}.json"
                    )
                    claim_dir = final.parent / ".attestation-claims"
                    before_claims = set(claim_dir.glob("*.json")) if claim_dir.exists() else set()
                    try:
                        with mock.patch.object(
                            materializer, verifier_name, return_value=forged
                        ):
                            with self.assertRaises(IssuerPacketError) as raised:
                                self._materialize_ref(
                                    materializer, request, evidence
                                )
                        self.assertEqual(raised.exception.result, "FAIL")
                        self.assertFalse(final.exists())
                        self.assertEqual(list(final.parent.glob("*.tmp")), [])
                        after_claims = (
                            set(claim_dir.glob("*.json")) if claim_dir.exists() else set()
                        )
                        self.assertEqual(after_claims, before_claims)
                    finally:
                        final.unlink(missing_ok=True)
                        for temp_path in final.parent.glob("*.tmp"):
                            temp_path.unlink(missing_ok=True)
                        if claim_dir.exists():
                            for claim_path in set(claim_dir.glob("*.json")) - before_claims:
                                claim_path.unlink(missing_ok=True)

    def test_unknown_actor_root_mismatch_and_unsafe_locator_do_not_search(self) -> None:
        unknown = {
            "schema_version": ATTESTATION_SCHEMA,
            "actor_type": "robot",
        }
        ref = self._write_json("tmp/quality/authority/human/unknown.json", unknown)
        self._assert_error(
            "unknown-actor-type",
            lambda: self._materialize_ref(self._materializer(), self._request(), ref),
        )
        ref = self._signed_evidence("human")
        copied = self._write_json("tmp/quality/authority/codex/copied-human.json", json.loads((self.root / ref[0]).read_text()))
        self._assert_error(
            "identity-drift",
            lambda: self._materialize_ref(self._materializer(), self._request(), copied),
        )
        self._assert_error(
            "unsafe-locator",
            lambda: self._materializer().materialize(
                self._request(),
                authority_evidence_locator="../outside.json",
                authority_evidence_sha256="0" * 64,
                subject_identity=self._subject(),
            ),
        )
        self._assert_error(
            "unsafe-locator",
            lambda: self._materializer().materialize(
                self._request(),
                authority_evidence_locator="tmp/quality/authority/human/latest/evidence.json",
                authority_evidence_sha256="0" * 64,
                subject_identity=self._subject(),
            ),
        )

    def test_authority_evidence_symlink_is_rejected(self) -> None:
        ref = self._signed_evidence("human")
        link = self.root / "tmp/quality/authority/human/link.json"
        link.symlink_to(self.root / ref[0])
        self._assert_error(
            "unsafe-locator",
            lambda: self._materializer().materialize(
                self._request(),
                authority_evidence_locator="tmp/quality/authority/human/link.json",
                authority_evidence_sha256=ref[1],
                subject_identity=self._subject(),
            ),
        )

    def test_packet_identity_requires_canonical_uuid_and_store_namespace(self) -> None:
        ref = self._signed_evidence("ci")
        for invalid in (
            "00000000-0000-0000-0000-000000000000",
            "ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF",
            "018f47a6-3a2b-7c4d-8e5f-123456789abc",
        ):
            with self.subTest(invalid=invalid):
                self._assert_error(
                    "invalid-identity",
                    lambda invalid=invalid: self._materialize_ref(
                        self._materializer(),
                        self._request(instance_id=invalid),
                        ref,
                    ),
                )
        result = self._materialize_ref(self._materializer(), self._request(), ref)
        self.assertRegex(
            result.locator,
            r"^tmp/quality/issuers/[0-9a-f-]{36}\.json$",
        )


if __name__ == "__main__":
    unittest.main()
