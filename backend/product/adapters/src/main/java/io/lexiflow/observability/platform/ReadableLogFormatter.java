package io.lexiflow.observability.platform;

import java.time.Clock;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.TreeMap;
import java.util.UUID;

/** 格式化简洁字幕、日常事件及保留关联身份的诊断单行日志。 */
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
   * 格式化控制台文本：日常隐藏内部身份，诊断保留完整关联 UUID。
   *
   * @param level 含义：固定日志级别名称。取值范围：非空且不含竖杠或控制字符。
   * @param correlationId 含义：真实完整关联 UUID。取值范围：UUID 或无请求关联时为 null。
   * @param event 含义：固定事件标识。取值范围：非空且不含竖杠或控制字符。
   * @param fields 含义：定位键值；字幕仅显示视频与播放时间。取值范围：非 null 的键值映射。
   * @param body 含义：待转义正文。取值范围：任意文本或 null，输出严格为单行。
   * @return 分隔符无填充空格的单行日志。
   */
  public String format(
      String level, UUID correlationId, String event, Map<String, String> fields, String body) {
    return format(Instant.now(clock), level, correlationId, event, fields, body);
  }

  /**
   * 使用显式时间格式化，供测试与框架适配器复用。
   *
   * @param instant 含义：事件发生时间。取值范围：非 null。
   * @param level 含义：固定日志级别。取值范围：不含控制字符的非空字符串。
   * @param correlationId 含义：真实关联 UUID。取值范围：无请求关联时为 null。
   * @param event 含义：固定事件名称。取值范围：不含控制字符的非空字符串。
   * @param fields 含义：定位字段。取值范围：非 null 的键值映射。
   * @param body 含义：输出正文。取值范围：任意文本，空值显示为占位符。
   * @return 安全转义后的单行日志。
   */
  public String format(
      Instant instant,
      String level,
      UUID correlationId,
      String event,
      Map<String, String> fields,
      String body) {
    Objects.requireNonNull(instant);
    Objects.requireNonNull(fields);
    String safeLevel = token(level, "level");
    String safeEvent = token(event, "event");
    var timestamp = TIME.format(instant.atZone(clock.getZone()));
    boolean diagnostic = !"INFO".equals(safeLevel);
    String captionEvent =
        switch (event) {
          case "video-start" -> "视频开始";
          case "incremental" -> "字幕增量";
          case "final" -> "字幕收尾";
          case "interrupted" -> "字幕中断";
          default -> null;
        };
    String safeBody = escape(body == null || body.isEmpty() ? "-" : body);
    if (!diagnostic && captionEvent != null) {
      return timestamp
          + '|'
          + safeLevel
          + '|'
          + captionEvent
          + "|video="
          + escapeKeyValue(Objects.requireNonNullElse(fields.get("video"), "-"))
          + '|'
          + playbackPosition(fields.get("position_ms"))
          + '|'
          + safeBody;
    }
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
        + (diagnostic ? (correlationId == null ? "-" : correlationId.toString()) + '|' : "")
        + safeEvent
        + '|'
        + location
        + '|'
        + safeBody;
  }

  private static String playbackPosition(String value) {
    if (value == null) return "-";
    try {
      long milliseconds = Long.parseLong(value);
      if (milliseconds < 0) return "-";
      return String.format(
          Locale.ROOT,
          "%02d:%02d.%03d",
          milliseconds / 60000,
          milliseconds / 1000 % 60,
          milliseconds % 1000);
    } catch (NumberFormatException ignored) {
      return "-";
    }
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
