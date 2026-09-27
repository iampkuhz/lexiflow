package io.lexiflow.enrichment.domain.model;

import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;

/**
 * 请求级双快照；只含可见文字，不保存服务器字幕会话。
 *
 * @param captionTopicKey 字幕主题的不透明身份，非空且不超过 128 字符。
 * @param trackKey 来源轨道身份；无法可靠识别时为 null。
 * @param previous 上次实际发出请求时冻结的可见快照；首次请求为 null。
 * @param current 本次可见快照，不能为空对象，允许无可见字幕。
 */
public record CaptionIncrementalRequest(
    String captionTopicKey, String trackKey, Snapshot previous, Snapshot current) {
  /** 校验请求身份、快照内容和沿用 key 的前缀裁剪规则。 */
  public CaptionIncrementalRequest {
    captionTopicKey = required(captionTopicKey, "captionTopicKey", 128);
    trackKey = optional(trackKey, "trackKey");
    Objects.requireNonNull(current, "current");
    if (previous != null) {
      var previousText = new HashMap<String, String>();
      for (var group : previous.captions()) {
        for (var segment : group.segments()) {
          previousText.put(segment.key(), segment.text());
        }
      }
      for (var group : current.captions()) {
        for (var segment : group.segments()) {
          var old = previousText.get(segment.key());
          if (old != null && !old.endsWith(segment.text())) {
            throw new IllegalArgumentException("retained segment text may only lose a prefix");
          }
        }
      }
    }
  }

  /**
   * 一份按显示顺序排列的字幕快照。
   *
   * @param captions 按显示顺序排列的字幕组；片段 key 唯一且文字总长不超过 500 UTF-16 单元。
   */
  public record Snapshot(List<Group> captions) {
    /** 校验快照内 key 唯一、片段数和 UTF-16 总长度。 */
    public Snapshot {
      captions = List.copyOf(Objects.requireNonNull(captions, "captions"));
      if (captions.size() > 500) throw new IllegalArgumentException("too many caption groups");
      var keys = new HashSet<String>();
      var count = 0;
      var chars = 0;
      for (var group : captions) {
        for (var segment : group.segments()) {
          count++;
          chars += segment.text().length();
          if (!keys.add(segment.key())) {
            throw new IllegalArgumentException("duplicate segment key");
          }
        }
      }
      if (count > 500 || chars > 500) {
        throw new IllegalArgumentException("snapshot exceeds limits");
      }
    }
  }

  /**
   * 一条字幕显示组；组之间不可跨界匹配。
   *
   * @param windowId 来源窗口身份；来源未提供时为 null。
   * @param startMs 来源字幕组在视频时间轴上的起始毫秒；未知时为 null。
   * @param segments 按可见顺序排列的非空片段列表。
   */
  public record Group(String windowId, Long startMs, List<Segment> segments) {
    /** 校验组元数据和片段序列。 */
    public Group {
      windowId = optional(windowId, "windowId");
      safeNonnegative(startMs, "startMs");
      segments = List.copyOf(Objects.requireNonNull(segments, "segments"));
      if (segments.isEmpty()) throw new IllegalArgumentException("caption group must not be empty");
    }
  }

  /**
   * 当前可见片段；append 只表示本次请求是否尚待处理。
   *
   * @param key 插件生成的稳定片段身份，保留片段不换 key，新增后缀另建 key。
   * @param text 当前可见的非空片段文字，同 key 只允许删除前缀。
   * @param offsetMs 片段相对字幕组起点的毫秒偏移；来源未提供时为 null。
   * @param append 是否尚待本次处理；不是原生字幕的追加标记。
   * @param line 从零开始的组内可视行号，仅用于还原布局。
   */
  public record Segment(String key, String text, Long offsetMs, boolean append, long line) {
    /** 校验片段身份、内容、时间和行号。 */
    public Segment {
      key = required(key, "key", 128);
      text = required(text, "text", 500);
      safeNonnegative(offsetMs, "offsetMs");
      if (line < 0 || line > 9_007_199_254_740_991L || !wellFormedUtf16(text)) {
        throw new IllegalArgumentException("segment line or text is invalid");
      }
    }
  }

  private static boolean wellFormedUtf16(String text) {
    for (var i = 0; i < text.length(); i++) {
      var ch = text.charAt(i);
      if (Character.isHighSurrogate(ch)) {
        if (++i == text.length() || !Character.isLowSurrogate(text.charAt(i))) return false;
      } else if (Character.isLowSurrogate(ch)) {
        return false;
      }
    }
    return true;
  }

  private static void safeNonnegative(Long value, String field) {
    if (value != null && (value < 0 || value > 9_007_199_254_740_991L)) {
      throw new IllegalArgumentException(field + " is invalid");
    }
  }

  private static String required(String value, String field, int maxLength) {
    Objects.requireNonNull(value, field);
    if (value.isEmpty() || value.length() > maxLength) {
      throw new IllegalArgumentException(field + " length is invalid");
    }
    return value;
  }

  private static String optional(String value, String field) {
    return value == null ? null : required(value, field, 128);
  }
}
