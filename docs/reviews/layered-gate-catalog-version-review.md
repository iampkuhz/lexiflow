# Layered Gate Catalog Version Review

> Review result: `BLOCKED`  
> Implementation and regression result: `PASS`  
> Work package: `LF-WP-QLT-LAYERED-CATALOG-REVIEW-018`

## Decision

The layered execution contract and current catalog migration reconcile. `TASK_VALIDATION` is the sole layer that may execute selected registry checks. `INDEPENDENT_REVIEW` and `CATALOG_DECISION` compile evidence-consumption plans with an empty `checks` array and verify immutable, hash-bound prior evidence without invoking the delivery executor.

This is not a current formal Gate completion. The repository contains only historical `LF-TSK-QLT-0012` and `LF-TSK-QLT-0013` work-package artifacts at task version `1`, change version `1.0.0`; it contains no artifacts or immutable run receipts for the current identities `0012@2/1.1.0` and `0013@2/2.0.0`. The historical artifacts cannot be promoted or reinterpreted as current evidence. Therefore the Phase 1/G1 receipt closure remains `BLOCKED`/pending.

## Catalog and dependency reconciliation

- `planning/workstreams.yaml` defines `LF-TSK-QLT-0012` as `2/1.1.0`; it consumes `LF-TSK-QLT-0010@2/2.0.0` task-validation and `LF-TSK-QLT-0011@2/2.0.0` independent-review contracts.
- `LF-TSK-QLT-0013` is `2/2.0.0`; it pins the current planner, validation, review, hash-DAG, evidence, issuer, and catalog prerequisites, including `0012@2/1.1.0`.
- `LF-TSK-ARCH-0008` is `3/2.0.0` and requires `LF-TSK-QLT-0013@2/2.0.0` with `catalog-decision-receipt@2.0.0`. G2 entry requirements use the same `ARCH-0008@3/2.0.0` identity.
- Registry v3 has 30 generated entries and declares the same current subject versions for QLT-0012, QLT-0013, and ARCH-0008. The planning and registry-profile validators both passed.

## Layering and quality ownership

- The registry declares `source_scan: forbidden`, `task_validation: executes-selected-checks`, and both non-delivery layers as `consumes-immutable-receipts`.
- Planner and CLI regression tests prove a non-delivery route has `checks: []`, `checker_execution: forbidden`, and does not call the delivery executor. A mismatched execution layer fails before execution.
- Hash-DAG and catalog-decision regressions reject cycles, aliases, hash drift, stale/missing dependency identities, stale task-source locators, and subject mutation after validation.
- The Java product manifest declares Java 25 with Gradle as the delivery owner. Its incremental aggregate is `check`, full aggregate is `deliveryFull`, and the owner set is Spotless, Checkstyle, PMD, Java source gates, ArchUnit/Gradle dependency guard, JUnit/Spring suites, and JaCoCo. The Python control plane is explicitly forbidden from scanning Java sources. This package inspected the contract and regression tests; it did not run a Java delivery aggregate.

## Verification

All completed commands returned `PASS`:

- `python3 -m scripts.gates.planning --root .`
- `python3 -m scripts.gates.registry_profiles --root . --check`
- `python3 -m unittest discover -s tests/gates -p 'test_hash_dag.py'`
- `python3 -m unittest discover -s tests/gates -p 'test_catalog_decision.py'`
- `python3 -m unittest discover -s tests/gates -p 'test_quality_gate_layering.py'`
- `python3 -m unittest tests.gates.test_gate_planner.TestRealMaterializersAndPlan.test_zero_write_packet_compiles_only_for_read_only_receipt_routes tests.gates.test_gate_planner.TestFormalClosureCompileMatrix.test_current_task_and_dependency_versions_are_bound tests.gates.test_gate_planner.TestFormalClosureCompileMatrix.test_stale_dependency_is_rejected`
- `python3 -m unittest tests.gates.test_gate_lifecycle.TestGateLifecycle.test_evidence_only_receipt_kind_never_calls_delivery_executor tests.gates.test_gate_lifecycle.TestGateLifecycle.test_receipt_kind_rejects_a_mismatched_execution_layer_before_executor tests.gates.test_independent_review.TestIndependentReview.test_stale_scope_and_hash_drift_are_rejected tests.gates.test_catalog_decision.TestCatalogDecision.test_missing_or_stale_dependency_fails tests.gates.test_catalog_decision.TestCatalogDecision.test_same_byte_task_source_alias_is_not_current`

No full Gate was run and no formal receipt was generated.
