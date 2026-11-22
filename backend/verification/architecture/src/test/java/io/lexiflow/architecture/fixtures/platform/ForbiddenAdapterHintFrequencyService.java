package io.lexiflow.architecture.fixtures.platform;

import io.lexiflow.enrichment.application.preferences.RecentHintFrequency;

/** 适配器直接使用计数应用服务而不是存储合同的反例。 */
public record ForbiddenAdapterHintFrequencyService(RecentHintFrequency service) {}
