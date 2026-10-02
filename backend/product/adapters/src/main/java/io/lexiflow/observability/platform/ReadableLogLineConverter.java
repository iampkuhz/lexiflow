package io.lexiflow.observability.platform;

import ch.qos.logback.classic.pattern.ClassicConverter;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.classic.spi.ThrowableProxyUtil;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

/** Logback 转换器：普通记录生成六列，已格式化事件避免重复前缀。 */
public final class ReadableLogLineConverter extends ClassicConverter {
  private static final String READABLE_MARKER = "LEXIFLOW_READABLE_LINE";
  private final ReadableLogFormatter formatter = new ReadableLogFormatter();

  @Override
  public String convert(ILoggingEvent event) {
    if (event.getMarkerList() != null
        && event.getMarkerList().stream().anyMatch(marker -> marker.contains(READABLE_MARKER)))
      return event.getFormattedMessage();
    var mdc = event.getMDCPropertyMap();
    var correlationId = uuid(mdc.get("requestId"));
    if (correlationId == null) correlationId = uuid(mdc.get("request_id"));
    if (correlationId == null) correlationId = UUID.randomUUID();
    Map<String, String> fields = new HashMap<>();
    var keyValuePairs = event.getKeyValuePairs();
    if (keyValuePairs != null)
      keyValuePairs.forEach(pair -> fields.put(pair.key, String.valueOf(pair.value)));
    String body = event.getFormattedMessage();
    if (event.getThrowableProxy() != null)
      body += "\n" + ThrowableProxyUtil.asString(event.getThrowableProxy());
    return formatter.format(
        java.time.Instant.ofEpochMilli(event.getTimeStamp()),
        event.getLevel().levelStr,
        correlationId,
        event.getLoggerName(),
        fields,
        body);
  }

  private static UUID uuid(String value) {
    if (value == null) return null;
    try {
      var parsed = UUID.fromString(value);
      return parsed.toString().equals(value) ? parsed : null;
    } catch (IllegalArgumentException ignored) {
      return null;
    }
  }
}
