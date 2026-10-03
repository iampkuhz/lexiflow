package io.lexiflow.api.runtime;

import io.lexiflow.lexicon.application.port.InvalidPublishedLexiconException;
import io.lexiflow.lexicon.application.port.LexiconReadRepository;
import io.lexiflow.lexicon.application.query.CachedLexiconQueryService;
import io.lexiflow.lexicon.domain.catalog.BuiltinLexiconCatalog;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import io.lexiflow.observability.platform.LexiconEventObserver;
import io.lexiflow.observability.platform.StructuredEvent;
import io.lexiflow.observability.platform.StructuredEventLogger;
import java.sql.SQLException;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import org.springframework.dao.DataAccessException;

/** API 装配层唯一的词库运行状态 owner 与 Catalog 装饰器。 */
public final class LexiconRuntime implements LexiconCatalog {
  /** 固定的运行状态原因。 */
  public enum Reason {
    OK,
    DEMO_MODE,
    NO_PUBLISHED_DATA,
    DEPENDENCY_UNAVAILABLE,
    SCHEMA_MISMATCH,
    PREWARM_DEGRADED
  }

  /**
   * 可供健康探针读取的固定状态。
   *
   * @param mode 固定运行模式：formal、demo 或 invalid。
   * @param reason 含义：固定状态原因。取值范围：Reason 枚举。
   * @param version 本次已知发布版本，未知时为 null；已知空发布为 0。
   * @param ready 含义：正式资料是否可用于提示。取值范围：true 或 false。
   */
  public record State(String mode, Reason reason, Long version, boolean ready) {}

  private final LexiconReadRepository repository;
  private CachedLexiconQueryService cached;
  private final LexiconCatalog demo;
  private final String mode;
  private final StructuredEventLogger events;
  private State state;
  private Boolean dependencyAvailable;

  /**
   * 建立运行时并执行首次只读探测。
   *
   * @param configuredMode 外部配置的运行模式，仅 formal 或 demo 合法。
   * @param repository 可选只读 Repository；缺少时 formal 不可就绪。
   * @param events 故障隔离的结构化事件发件器。
   */
  public LexiconRuntime(
      String configuredMode, LexiconReadRepository repository, StructuredEventLogger events) {
    long started = System.nanoTime();
    this.mode =
        "demo".equals(configuredMode)
            ? "demo"
            : "formal".equals(configuredMode) ? "formal" : "invalid";
    this.repository = repository;
    this.events = Objects.requireNonNull(events);
    this.demo = "demo".equals(mode) ? new BuiltinLexiconCatalog() : null;
    if ("demo".equals(mode)) state = new State(mode, Reason.DEMO_MODE, null, false);
    else if ("invalid".equals(mode)) state = new State(mode, Reason.SCHEMA_MISMATCH, null, false);
    else if (repository == null)
      state = new State(mode, Reason.DEPENDENCY_UNAVAILABLE, null, false);
    else probe();
    emitStart(elapsedMs(started));
  }

  /**
   * 显式只读探测；相同版本不会重做预热。
   *
   * @return 探测后的固定状态。
   */
  public synchronized State probe() {
    return probeMeasured().state();
  }

  private ProbeResult probeMeasured() {
    if (repository == null || !"formal".equals(mode)) return new ProbeResult(state, 0, 0);
    long started = System.nanoTime();
    int versionReads = 1;
    int prewarmReads = 0;
    long previousVersion = cached == null ? -1 : cached.warmupStatus().version();
    try {
      CachedLexiconQueryService.WarmupStatus warmup;
      if (cached == null) {
        cached =
            new CachedLexiconQueryService(
                repository, 4_000, 2_000, 512, new LexiconEventObserver(events));
        warmup = cached.warmupStatus();
      } else warmup = cached.refresh();
      long version = warmup.version();
      if (version != previousVersion) prewarmReads = warmup.attempts();
      if (version == 0)
        transition(new State(mode, Reason.NO_PUBLISHED_DATA, 0L, false), true, elapsedMs(started));
      else if (warmup.degraded()) {
        versionReads++;
        long confirmed = repository.publishedVersion();
        if (confirmed != version) {
          transition(
              new State(
                  mode,
                  confirmed == 0 ? Reason.NO_PUBLISHED_DATA : Reason.DEPENDENCY_UNAVAILABLE,
                  confirmed,
                  false),
              true,
              elapsedMs(started));
        } else
          transition(
              new State(mode, Reason.PREWARM_DEGRADED, version, true), true, elapsedMs(started));
      } else transition(new State(mode, Reason.OK, version, true), true, elapsedMs(started));
    } catch (RuntimeException failure) {
      var reason = classify(failure);
      if (reason == null) throw failure;
      transition(
          new State(mode, reason, null, false),
          reason == Reason.SCHEMA_MISMATCH,
          elapsedMs(started));
    }
    return new ProbeResult(state, versionReads, prewarmReads);
  }

  /**
   * 本次探测的状态与局部访问量，供请求恢复时合并，不保留全局累计器。
   *
   * @param state 含义：本次探测后的运行状态。取值范围：非 null。
   * @param versionReads 含义：本次实际发起的版本读取次数。取值范围：非负整数。
   * @param prewarmReads 含义：本次版本切换实际尝试的预热批次数。取值范围：非负整数。
   */
  private record ProbeResult(State state, int versionReads, int prewarmReads) {}

  /**
   * 返回最近一次已知状态，不执行存储探测。
   *
   * @return 最近一次已知运行状态。
   */
  public synchronized State state() {
    return state;
  }

  @Override
  public synchronized LexiconLookupResult lookupForms(List<String> normalizedForms) {
    if (normalizedForms.isEmpty())
      return new LexiconLookupResult(
          List.of(), java.util.OptionalLong.empty(), LexiconLookupResult.Counts.zero());
    if (demo != null) return demo.lookupForms(normalizedForms);
    if (repository == null || !"formal".equals(mode))
      throw new LexiconNotReadyException(state.reason());
    var recovery = state.ready() ? new ProbeResult(state, 0, 0) : probeMeasured();
    if (!state.ready()) throw new LexiconNotReadyException(state.reason());
    long started = System.nanoTime();
    try {
      var result = cached.lookupForms(normalizedForms);
      var warmup = cached.warmupStatus();
      long version = result.publishedVersion().orElse(0);
      if (version == 0) {
        transition(new State(mode, Reason.NO_PUBLISHED_DATA, 0L, false), true, elapsedMs(started));
      } else if (warmup.degraded()) {
        if (result.counts().prewarmReads() > 0 && result.counts().dbBatches() == 0) {
          long confirmed = repository.publishedVersion();
          result = withAdditionalReads(result, 1, 0);
          if (confirmed != version) {
            transition(
                new State(
                    mode,
                    confirmed == 0 ? Reason.NO_PUBLISHED_DATA : Reason.DEPENDENCY_UNAVAILABLE,
                    confirmed,
                    false),
                true,
                elapsedMs(started));
          } else
            transition(
                new State(mode, Reason.PREWARM_DEGRADED, version, true), true, elapsedMs(started));
        } else
          transition(
              new State(mode, Reason.PREWARM_DEGRADED, version, true), true, elapsedMs(started));
      } else transition(new State(mode, Reason.OK, version, true), true, elapsedMs(started));
      if (state.ready())
        return withAdditionalReads(result, recovery.versionReads(), recovery.prewarmReads());
    } catch (RuntimeException failure) {
      var reason = classify(failure);
      if (reason == null) throw failure;
      transition(
          new State(mode, reason, null, false),
          reason == Reason.SCHEMA_MISMATCH,
          elapsedMs(started));
    }
    throw new LexiconNotReadyException(state.reason());
  }

  private static LexiconLookupResult withAdditionalReads(
      LexiconLookupResult result, int versionReads, int prewarmReads) {
    var c = result.counts();
    return new LexiconLookupResult(
        result.candidates(),
        result.publishedVersion(),
        new LexiconLookupResult.Counts(
            c.queryKeys(),
            c.positiveHits(),
            c.negativeHits(),
            c.cacheMisses(),
            c.dbBatches(),
            c.versionReads() + versionReads,
            c.prewarmReads() + prewarmReads));
  }

  private static Reason classify(Throwable failure) {
    if (failure instanceof InvalidPublishedLexiconException) return Reason.SCHEMA_MISMATCH;
    if (!(failure instanceof DataAccessException)) return null;
    Throwable cursor = failure;
    while (cursor != null) {
      if (cursor instanceof SQLException sql
          && sql.getSQLState() != null
          && sql.getSQLState().startsWith("42")) return Reason.SCHEMA_MISMATCH;
      cursor = cursor.getCause();
    }
    return Reason.DEPENDENCY_UNAVAILABLE;
  }

  private void transition(State next, boolean dependencyHealthy, long durationMs) {
    if (dependencyAvailable != null && dependencyAvailable != dependencyHealthy) {
      var reason =
          dependencyHealthy
              ? StructuredEvent.Reason.RECOVERED
              : StructuredEvent.Reason.DEPENDENCY_UNAVAILABLE;
      events.tryEmit(
          () ->
              new StructuredEvent(
                  StructuredEvent.EventType.RUNTIME_DEPENDENCY_CHANGED,
                  reason,
                  durationMs,
                  Map.of(),
                  next.version(),
                  null,
                  Map.of(),
                  null,
                  null,
                  Map.of()));
    }
    dependencyAvailable = dependencyHealthy;
    state = next;
  }

  private void emitStart(long durationMs) {
    var current = state;
    var reason = StructuredEvent.Reason.valueOf(current.reason().name());
    Map<StructuredEvent.Count, Long> counts = Map.of();
    if (cached != null && cached.warmupStatus().version() >= 0) {
      var warmup = cached.warmupStatus();
      counts =
          Map.of(
              StructuredEvent.Count.PREWARM_POSITIVE,
              (long) warmup.positiveKeys(),
              StructuredEvent.Count.PREWARM_NEGATIVE,
              (long) warmup.negativeKeys());
    }
    var finalCounts = counts;
    events.tryEmit(
        () ->
            new StructuredEvent(
                StructuredEvent.EventType.RUNTIME_START_COMPLETED,
                reason,
                durationMs,
                finalCounts,
                current.version(),
                null,
                Map.of(),
                null,
                null,
                Map.of()));
  }

  private static long elapsedMs(long started) {
    return Math.max(0, (System.nanoTime() - started) / 1_000_000);
  }
}
