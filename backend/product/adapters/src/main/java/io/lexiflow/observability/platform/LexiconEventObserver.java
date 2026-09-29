package io.lexiflow.observability.platform;

import io.lexiflow.lexicon.application.port.LexiconCacheObserver;
import io.lexiflow.lexicon.application.port.LexiconImportObserver;
import java.util.EnumMap;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;

/** 将词库应用观察端口映射为闭合结构化事件；不携带词条或来源正文。 */
public final class LexiconEventObserver implements LexiconImportObserver, LexiconCacheObserver {
  private final StructuredEventLogger logger;

  /** 使用既有结构化事件发件器装配适配器。 */
  public LexiconEventObserver(StructuredEventLogger logger) {
    this.logger = Objects.requireNonNull(logger);
  }

  @Override
  public void onEvent(LexiconImportObserver.Event event) {
    var counts = new EnumMap<StructuredEvent.Count, Long>(StructuredEvent.Count.class);
    event
        .counts()
        .forEach((key, value) -> counts.put(StructuredEvent.Count.valueOf(key.name()), value));
    if (event.terminal()) counts.remove(StructuredEvent.Count.HINT_ROWS);
    else counts.remove(StructuredEvent.Count.LOOKUP_ROWS);
    var rules = new EnumMap<StructuredEvent.ReasonCount, Long>(StructuredEvent.ReasonCount.class);
    if (event.terminal())
      event
          .reasonCounts()
          .forEach(
              (key, value) -> {
                try {
                  rules.put(
                      StructuredEvent.ReasonCount.valueOf(key.toUpperCase(Locale.ROOT)), value);
                } catch (IllegalArgumentException ignored) {
                }
              });
    var type =
        event.terminal()
            ? StructuredEvent.EventType.LEXICON_IMPORT_COMPLETED
            : StructuredEvent.EventType.LEXICON_IMPORT_STAGE;
    logger.tryEmit(
        () ->
            new StructuredEvent(
                type,
                StructuredEvent.Reason.valueOf(event.reason().name()),
                Math.max(0, event.durationNanos() / 1_000_000),
                counts,
                event.lexiconVersion(),
                null,
                Map.of(),
                event.terminal() ? null : StructuredEvent.ImportStep.valueOf(event.step().name()),
                event.terminal() ? null : StructuredEvent.ImportPhase.valueOf(event.phase().name()),
                rules));
  }

  @Override
  public void versionChanged(
      long oldVersion, long newVersion, int positiveKeys, int negativeKeys, long durationNanos) {
    logger.tryEmit(
        () ->
            new StructuredEvent(
                StructuredEvent.EventType.LEXICON_CACHE_VERSION_CHANGED,
                StructuredEvent.Reason.VERSION_CHANGED,
                Math.max(0, durationNanos / 1_000_000),
                Map.of(
                    StructuredEvent.Count.INVALIDATED_POSITIVE,
                    (long) positiveKeys,
                    StructuredEvent.Count.INVALIDATED_NEGATIVE,
                    (long) negativeKeys),
                newVersion,
                null,
                Map.of(),
                null,
                null,
                Map.of()));
  }
}
