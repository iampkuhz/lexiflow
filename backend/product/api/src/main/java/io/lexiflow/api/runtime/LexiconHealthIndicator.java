package io.lexiflow.api.runtime;

import org.springframework.boot.health.contributor.Health;
import org.springframework.boot.health.contributor.HealthIndicator;

/** 词库正式 readiness 探针。 */
public final class LexiconHealthIndicator implements HealthIndicator {
  private final LexiconRuntime runtime;

  /** 注入唯一运行状态 owner。 */
  public LexiconHealthIndicator(LexiconRuntime runtime) {
    this.runtime = runtime;
  }

  @Override
  public Health health() {
    var state = runtime.probe();
    var builder = state.ready() ? Health.up() : Health.down();
    builder.withDetail("mode", state.mode()).withDetail("reason", state.reason().name());
    if (state.version() != null) builder.withDetail("version", state.version());
    return builder.build();
  }
}
