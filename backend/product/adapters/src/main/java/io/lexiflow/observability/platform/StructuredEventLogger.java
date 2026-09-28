package io.lexiflow.observability.platform;

import java.util.Objects;
import java.util.function.BiConsumer;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import tools.jackson.core.JsonGenerator;
import tools.jackson.core.json.JsonFactory;

/** 将封闭事件编码为单行 JSON；失败不影响业务路径。 */
public final class StructuredEventLogger {
  private static final Logger LOG = LoggerFactory.getLogger(StructuredEventLogger.class);
  private final BiConsumer<StructuredEvent.Level, String> sink;
  private final JsonFactory factory = new JsonFactory();

  /** 使用固定 SLF4J sink。 */
  public StructuredEventLogger() {
    this(null);
  }

  /**
   * 注入测试级别的单行输出 sink。
   *
   * @param sink 含义：接收完整事件 JSON 行的输出函数。取值范围：null 表示固定 SLF4J。
   */
  public StructuredEventLogger(BiConsumer<StructuredEvent.Level, String> sink) {
    this.sink = sink;
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
      var line = encode(event);
      if (sink != null) sink.accept(event.level(), line);
      else emitSlf4j(event, line);
      return true;
    } catch (RuntimeException ignored) {
      return false;
    }
  }

  private static void emitSlf4j(StructuredEvent event, String line) {
    switch (event.level()) {
      case INFO -> LOG.info("{}", line);
      case WARN -> LOG.warn("{}", line);
      case ERROR -> LOG.error("{}", line);
    }
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
