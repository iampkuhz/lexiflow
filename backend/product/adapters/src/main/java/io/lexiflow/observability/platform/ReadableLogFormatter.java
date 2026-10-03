package io.lexiflow.observability.platform;

import java.time.Clock;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;
import java.util.UUID;

/** 将控制台日志格式化为固定六列的单行文本。 */
public final class ReadableLogFormatter {
  private static final DateTimeFormatter TIME =
      DateTimeFormatter.ofPattern("MM-dd HH:mm:ss", Locale.ROOT);
  private final Clock clock;

  /** 使用系统本地时钟。 */
  public ReadableLogFormatter() {
    this(Clock.systemDefaultZone());
  }

  /** 注入时钟，便于确定性验证时间列。 */
  public ReadableLogFormatter(Clock clock) {
    this.clock = Objects.requireNonNull(clock);
  }

  /**
   * 格式化时间、级别、完整关联 UUID、事件、定位键值和正文。
   *
   * @param level 含义：日志级别名称。取值范围：非空且不含分隔符或控制字符。
   * @param correlationId 含义：完整关联 UUID。取值范围：非 null。
   * @param event 含义：固定事件标识。取值范围：非空且不含分隔符或控制字符。
   * @param fields 含义：按键名排序输出的定位键值。取值范围：非 null，键非 null，值可为 null。
   * @param body 含义：转义为单行的正文。取值范围：任意文本，null 或空串输出占位符。
   * @return 无填充空格的六列日志行。
   */
  public String format(
      String level, UUID correlationId, String event, Map<String, String> fields, String body) {
    return format(Instant.now(clock), level, correlationId, event, fields, body);
  }

  /**
   * 使用显式时间格式化，供测试与框架适配器复用。
   *
   * @param instant 含义：事件时间。取值范围：非 null。
   * @param level 含义：日志级别名称。取值范围：非空且不含分隔符或控制字符。
   * @param correlationId 含义：完整关联 UUID。取值范围：非 null。
   * @param event 含义：固定事件标识。取值范围：非空且不含分隔符或控制字符。
   * @param fields 含义：按键名排序输出的定位键值。取值范围：非 null，键非 null，值可为 null。
   * @param body 含义：转义为单行的正文。取值范围：任意文本，null 或空串输出占位符。
   * @return 无填充空格的六列日志行。
   */
  public String format(
      Instant instant,
      String level,
      UUID correlationId,
      String event,
      Map<String, String> fields,
      String body) {
    Objects.requireNonNull(instant);
    Objects.requireNonNull(correlationId);
    Objects.requireNonNull(fields);
    String safeLevel = token(level, "level");
    String safeEvent = token(event, "event");
    var timestamp = TIME.format(instant.atZone(clock.getZone()));
    var location = new StringBuilder();
    new TreeMap<>(fields)
        .forEach(
            (key, value) -> {
              if (!location.isEmpty()) location.append(';');
              location.append(escapeKeyValue(key)).append('=');
              location.append(value == null ? "-" : escapeKeyValue(value));
            });
    if (location.isEmpty()) location.append('-');
    return timestamp
        + '|'
        + safeLevel
        + '|'
        + correlationId
        + '|'
        + safeEvent
        + '|'
        + location
        + '|'
        + escape(body == null || body.isEmpty() ? "-" : body);
  }

  private static String token(String value, String name) {
    if (value == null || value.isBlank() || value.indexOf('|') >= 0 || hasControl(value))
      throw new IllegalArgumentException("invalid " + name);
    return escape(value);
  }

  private static String escapeKeyValue(String value) {
    String escaped = escape(Objects.requireNonNull(value));
    return escaped.replace(";", "\\u003B").replace("=", "\\u003D");
  }

  private static String escape(String value) {
    var out = new StringBuilder(value.length());
    for (int i = 0; i < value.length(); i++) {
      char c = value.charAt(i);
      switch (c) {
        case '\\' -> out.append("\\\\");
        case '|' -> out.append("\\u007C");
        case '\n' -> out.append("\\n");
        case '\r' -> out.append("\\r");
        case '\t' -> out.append("\\t");
        default -> {
          if (Character.isISOControl(c)) out.append(String.format(Locale.ROOT, "\\u%04X", (int) c));
          else out.append(c);
        }
      }
    }
    return out.toString();
  }

  private static boolean hasControl(String value) {
    return value.chars().anyMatch(Character::isISOControl);
  }
}
