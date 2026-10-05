package io.lexiflow.observability.platform;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.concurrent.Executor;
import java.util.function.BiConsumer;
import java.util.function.Consumer;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MarkerFactory;
import tools.jackson.core.JsonGenerator;
import tools.jackson.core.json.JsonFactory;

/** 将封闭事件写入机器测试 sink 或精简控制台；失败不影响业务路径。 */
public final class StructuredEventLogger {
  private static final Logger LOG = LoggerFactory.getLogger(StructuredEventLogger.class);
  private static final String READABLE_MARKER = "LEXIFLOW_READABLE_LINE";
  private static final Executor READABLE_OUTPUT =
      new java.util.concurrent.ThreadPoolExecutor(
          1,
          1,
          0,
          java.util.concurrent.TimeUnit.MILLISECONDS,
          new java.util.concurrent.ArrayBlockingQueue<>(256),
          runnable -> {
            var thread = new Thread(runnable, "lexiflow-readable-log");
            thread.setDaemon(true);
            return thread;
          },
          new java.util.concurrent.ThreadPoolExecutor.DiscardPolicy());
  private final BiConsumer<StructuredEvent.Level, String> sink;
  private final Executor readableExecutor;
  private final Consumer<String> readableSink;
  private final JsonFactory factory = new JsonFactory();
  private final ReadableLogFormatter formatter = new ReadableLogFormatter();

  /** 使用固定 SLF4J sink。 */
  public StructuredEventLogger() {
    this(null, READABLE_OUTPUT, line -> emitReadableSlf4j(StructuredEvent.Level.INFO, line));
  }

  /**
   * 注入测试级别的单行输出 sink。
   *
   * @param sink 含义：接收完整事件 JSON 行的输出函数。取值范围：null 表示固定 SLF4J。
   */
  public StructuredEventLogger(BiConsumer<StructuredEvent.Level, String> sink) {
    this(sink, READABLE_OUTPUT, line -> emitReadableSlf4j(StructuredEvent.Level.INFO, line));
  }

  /**
   * 注入可读日志执行器与 sink，便于验证队列饱和和 sink 故障隔离。
   *
   * @param sink 含义：结构化事件测试输出。取值范围：null 表示禁用机器事件 sink。
   * @param readableExecutor 含义：可读事件执行器。取值范围：非 null。
   * @param readableSink 含义：消费格式化可读行的函数。取值范围：非 null。
   */
  public StructuredEventLogger(
      BiConsumer<StructuredEvent.Level, String> sink,
      Executor readableExecutor,
      Consumer<String> readableSink) {
    this.sink = sink;
    this.readableExecutor = Objects.requireNonNull(readableExecutor);
    this.readableSink = Objects.requireNonNull(readableSink);
  }

  /**
   * 安全尝试生成、编码并输出事件。
   *
   * @param eventSupplier 含义：延迟生成封闭事件。取值范围：非 null。
   * @return 事件写出成功时为 true，否则为 false。
   */
  public boolean tryEmit(Supplier<StructuredEvent> eventSupplier) {
    try {
      var event = Objects.requireNonNull(eventSupplier).get();
      if (sink != null) sink.accept(event.level(), encode(event));
      else if (event.type() == StructuredEvent.EventType.CAPTION_REQUEST_COMPLETED
          && event.level() == StructuredEvent.Level.INFO) {
        // 正常字幕请求已由展示事件反馈，统计仅供显式 DEBUG 排查。
        if (LOG.isDebugEnabled())
          LOG.debug(MarkerFactory.getMarker(READABLE_MARKER), "{}", readableLine(event, "DEBUG"));
      } else emitSlf4j(event, readableLine(event, event.level().name()));
      return true;
    } catch (RuntimeException ignored) {
      return false;
    }
  }

  /**
   * 安全写入固定 INFO 级别的可读敏感事件。调用者必须仅传入服务端白名单事件名称。
   *
   * @param event 含义：固定事件名称。取值范围：服务端白名单事件。
   * @param correlationId 含义：真实事件身份，日常字幕不展示。取值范围：已验证的 UUID。
   * @param fields 含义：已验证且需安全转义的定位字段。取值范围：非 null 的键值映射。
   * @param body 含义：已确认的显示正文。取值范围：已校验长度的字幕文本。
   * @return 格式化及提交未抛异常时为 true；队列饱和可丢弃，不表示持久化成功。
   */
  public boolean tryEmitReadableInfo(
      String event, UUID correlationId, Map<String, String> fields, String body) {
    try {
      var line = formatter.format("INFO", correlationId, event, fields, body);
      readableExecutor.execute(
          () -> {
            try {
              readableSink.accept(line);
            } catch (RuntimeException ignored) {
              // 日志后端故障不能回流到字幕请求，也不应打印带正文的异常。
            }
          });
      return true;
    } catch (RuntimeException ignored) {
      return false;
    }
  }

  private static void emitSlf4j(StructuredEvent event, String line) {
    emitReadableSlf4j(event.level(), line);
  }

  private static void emitReadableSlf4j(StructuredEvent.Level level, String line) {
    var marker = MarkerFactory.getMarker(READABLE_MARKER);
    switch (level) {
      case INFO -> LOG.info(marker, "{}", line);
      case WARN -> LOG.warn(marker, "{}", line);
      case ERROR -> LOG.error(marker, "{}", line);
    }
  }

  private String readableLine(StructuredEvent event, String displayLevel) {
    var fields = new java.util.TreeMap<String, String>();
    fields.put("duration_ms", Long.toString(event.durationMs()));
    if (event.reason() != StructuredEvent.Reason.OK) fields.put("reason", event.reason().name());
    boolean detailed = LOG.isDebugEnabled();
    if (event.lexiconVersion() != null
        && (detailed
            || event.type() == StructuredEvent.EventType.RUNTIME_START_COMPLETED
            || event.type() == StructuredEvent.EventType.LEXICON_CACHE_VERSION_CHANGED))
      fields.put("lexicon_version", Long.toString(event.lexiconVersion()));
    putCount(event, StructuredEvent.Count.SELECTED, fields);
    putCount(event, StructuredEvent.Count.NEW_RANGES, fields);
    boolean warningOrFailure = event.level() != StructuredEvent.Level.INFO;
    if (detailed || warningOrFailure)
      event
          .counts()
          .forEach((key, value) -> fields.put("count_" + snake(key.name()), value.toString()));
    if (detailed)
      event
          .timingsMs()
          .forEach((key, value) -> fields.put("timing_" + snake(key.name()), value.toString()));
    if (detailed && event.step() != null) {
      fields.put("step", snake(event.step().name()));
      fields.put("phase", snake(event.phase().name()));
    }
    if (detailed)
      event
          .reasonCounts()
          .forEach((key, value) -> fields.put("reason_" + snake(key.name()), value.toString()));
    return formatter.format(displayLevel, event.requestId(), event.eventName(), fields, "-");
  }

  private static void putCount(
      StructuredEvent event, StructuredEvent.Count count, java.util.Map<String, String> fields) {
    Long value = event.counts().get(count);
    if (value != null) fields.put("count_" + snake(count.name()), value.toString());
  }

  private String encode(StructuredEvent e) {
    var out = new java.io.StringWriter();
    try (JsonGenerator g =
        factory.createGenerator(tools.jackson.core.ObjectWriteContext.empty(), out)) {
      g.writeStartObject();
      g.writeStringProperty("schema", "lexiflow.event.v1");
      g.writeStringProperty("event", e.eventName());
      g.writeStringProperty("stage", e.stage());
      g.writeStringProperty("result", e.result());
      g.writeStringProperty("reason", e.reason().name());
      g.writeNumberProperty("duration_ms", e.durationMs());
      if (!e.counts().isEmpty()) {
        g.writeObjectPropertyStart("counts");
        e.counts().forEach((k, v) -> g.writeNumberProperty(snake(k.name()), v));
        g.writeEndObject();
      }
      if (e.lexiconVersion() != null) g.writeNumberProperty("lexicon_version", e.lexiconVersion());
      if (e.requestId() != null) g.writeStringProperty("request_id", e.requestId().toString());
      if (!e.timingsMs().isEmpty()) {
        g.writeObjectPropertyStart("timings_ms");
        e.timingsMs().forEach((k, v) -> g.writeNumberProperty(snake(k.name()), v));
        g.writeEndObject();
      }
      if (e.step() != null) {
        g.writeStringProperty("step", snake(e.step().name()));
        g.writeStringProperty("phase", snake(e.phase().name()));
      }
      if (!e.reasonCounts().isEmpty()) {
        g.writeObjectPropertyStart("reason_counts");
        e.reasonCounts().forEach((k, v) -> g.writeNumberProperty(snake(k.name()), v));
        g.writeEndObject();
      }
      g.writeEndObject();
    }
    return out.toString();
  }

  private static String snake(String value) {
    return value.toLowerCase(java.util.Locale.ROOT);
  }
}
