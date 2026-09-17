# Phase 1 User Requirement Traceability

> Source: the project founder's 2026-09-16 product attachment.  
> Scope: prove that the Phase 1 decision package covers the requested architecture questions. This is not implementation evidence, a catalog receipt or user approval.

## Requested Phase 1 outputs

| Explicit request | Authoritative design evidence | Remaining before Phase 2 |
|---|---|---|
| Domain division | [Phase 1 §5](../architecture/phase-1.md#5-bounded-context-与责任) defines Identity & Access, Content, Lexicon, Vocabulary Profile, Learning, Semantic and Enrichment, plus the Client Delivery / Sync application boundary. Ownership conflicts are resolved in §5.1. | Current-input Gate evidence and explicit user decision. |
| Responsibilities of every module | [Phase 1 §6.1](../architecture/phase-1.md#61-推荐模块布局) maps kernel, public Domain APIs, Domain implementations, workflow, delivery/sync, inbound/outbound adapters, bootstrap roots and Extension responsibilities. | Java module scaffolding and architecture quality tests exist under the separately requested Java harness work; product behavior and further module implementation remain post-approval. |
| Dependency direction | [Phase 1 §6.2](../architecture/phase-1.md#62-静态依赖方向) fixes allowed module edges and forbidden adapter/framework coupling; `harness/module-boundaries.yaml` is its machine-readable proposal. | Approved ADR state and later behavior proof; the existing generated Java scaffold and executable architecture checks do not prove product behavior. |
| YouTube caption to annotation | [Phase 1 §7](../architecture/phase-1.md#7-youtube-caption-到-annotation-的完整链路) provides the end-to-end sequence, fast/slow result semantics, durable handoff and stale-result rejection. | Extension and backend spikes must measure the proposed caption/context assumptions and latency targets. |
| Behavior through Learning into Vocabulary Profile | [Phase 1 §8](../architecture/phase-1.md#8-behavior-到-learning-再到-vocabulary-profile-的完整链路) separates immutable behavior facts, versioned evidence and the Profile projection, including explicit read-your-writes behavior. | Phase 2 event/schema invariants and Phase 6 scoring/replay implementation. |
| Synchronous versus asynchronous operations | [Phase 1 §9](../architecture/phase-1.md#9-同步与异步边界) classifies local rendering, fast enrichment, explicit intent, durable intake, semantic work, implicit projection and replay; ADR-003, ADR-006 and ADR-008 compare alternatives. | User confirmation of the boundary and later latency/consistency experiments. |

## Product and engineering constraints

| Source requirement | Frozen Phase 1 position | Evidence and later proof |
|---|---|---|
| English captions remain primary; no sentence-level bilingual subtitle | English renders from local player/Extension state before any backend or model result. Hints are span annotations only. | [Product brief](../product/product-brief.md), Phase 1 §2.1, caption flow; Extension E2E evidence remains future work. |
| Help is selective and context-aware | Rules decide whether to help; Semantic capabilities determine meaning only for selected candidates. | ADR-003 and [cross-cutting Semantic contracts](../architecture/phase-1-cross-cutting-contracts.md#2-semantic-capability-port-合同). |
| Vocabulary Profile is cross-device and richer than known/unknown | Server-owned, versioned, explainable projection with exposure, familiarity, domain and source evidence; explicit known/unknown is an input, not the whole model. | Phase 1 §5/§8 and ADR-005/006; Phase 2 owns concrete model/schema. |
| Learning preserves behavior facts | Learning owns immutable facts and produces versioned evidence; Profile owns the projection. The whole application is not Event Sourcing. | Phase 1 §5.1/§8 and ADR-005. |
| YouTube is only the first source | YouTube parsing remains an Extension adapter; the Content core and Source Adapter port are source-neutral. | ADR-004 and [Source Adapter contract](../architecture/phase-1-cross-cutting-contracts.md#7-source-adapter-port-与-conformance-合同). |
| Provider independence | Business code depends on task-level Semantic ports and normalized results, not a provider SDK. | ADR-007 and cross-cutting contracts §2–3. |
| Modular Monolith without premature distributed infrastructure | One codebase and business version, `api`/`worker` composition roots, PostgreSQL durable handoff, Redis/cache projections rebuildable. Kafka/Kubernetes/Service Mesh/Vector DB remain outside initial scope. | ADR-001/002/008 and Phase 1 §6.3/§10. |
| Thin Chrome Extension | Extension acquires source context, renders English/annotations, caches locally and emits behavior; it is not Profile truth. | Phase 1 §5.1/§6.1 and `AGENTS.md`. |
| P95/P99, failure and cost must be measured | The decision package presents initial experiment thresholds as assumptions, never as achieved SLOs. Model cost has no invented numeric promise. | [G1 decision package § initial SLO](g1-decision-package.md#初始-slo-与预算假设) and product evidence list. |
| Privacy and observability | Full subtitles, Profile, learning history, model payloads and local run data are excluded from repository evidence; logs/traces use redaction and bounded correlation. | [Cross-cutting contracts §5–6](../architecture/phase-1-cross-cutting-contracts.md#5-信任边界与威胁合同) and Harness privacy rules. |
| Every major choice includes alternatives and trade-offs | Ten proposed ADRs document context, recommendation, alternatives, consequences and revisit triggers. | [Architecture decisions](../architecture/decisions.md) and the comparison table in the G1 decision package. |

## Phase boundary

The requested Phase 1 design is present and reviewable, while formal acceptance remains intentionally open. The Gate control plane, integrated review and current receipts for the 29 prerequisite Tasks must complete before asking for the user decision. The ARCH-0008 exit receipt requires that decision and is issued afterwards; see phase-1-acceptance-order.md. Only an explicit `APPROVED` decision can move the ADRs from `Proposed` and activate Phase 2 Data Model work. SQL fields, concrete REST endpoints, Docker Compose, Prompt engineering and product implementation remain outside Phase 1, as requested.
