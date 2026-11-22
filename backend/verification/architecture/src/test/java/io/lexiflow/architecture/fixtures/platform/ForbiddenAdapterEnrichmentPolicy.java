package io.lexiflow.architecture.fixtures.platform;

import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;

/** 适配器直接使用提示策略的反例。 */
public record ForbiddenAdapterEnrichmentPolicy(DeterministicHintPolicy policy) {}
