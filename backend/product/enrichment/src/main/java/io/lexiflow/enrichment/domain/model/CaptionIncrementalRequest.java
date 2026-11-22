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
 * @param previous 上次成功确认请求在发送时冻结的可见快照；尚无成功请求时为 null。
 * @param current 本次可见快照，不能为空对象，允许无可见字幕。
 * @param commonWordCutoff 单词提示截止排名；仅允许 1000、1500、2000。
 */
public record CaptionIncrementalRequest(
    String captionTopicKey,
    String trackKey,
    Snapshot previous,
    Snapshot current,
    int commonWordCutoff) {
  /** 校验请求身份、快照内容和沿用 key 的前缀裁剪规则。 */
  public CaptionIncrementalRequest {
    captionTopicKey = required(captionTopicKey, "captionTopicKey", 128);
    trackKey = optional(trackKey, "trackKey");
    Objects.requireNonNull(current, "current");
    if (commonWordCutoff != 1000 && commonWordCutoff != 1500 && commonWordCutoff != 2000)
      throw new IllegalArgumentException("commonWordCutoff is invalid");
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

  /** 创建使用默认 1500 截止值的请求。 */
  public CaptionIncrementalRequest(
      String topic, String track, Snapshot previous, Snapshot current) {
    this(topic, track, previous, current, 1500);
  }

  /** 由客户端仅提交仍可见旧片段时构造比较快照。 */
  public CaptionIncrementalRequest(List<PreviousSegment> previousSegments, Snapshot current) {
    this(previousSegments, current, 1500);
  }

  public CaptionIncrementalRequest(List<PreviousSegment> previousSegments, Snapshot current, int cutoff) {
    this("caption", null, visiblePrevious(previousSegments, current), current, cutoff);
  }

  private static Snapshot visiblePrevious(List<PreviousSegment> previous, Snapshot current) {
    Objects.requireNonNull(previous, "previousSegments");
    Objects.requireNonNull(current, "current");
    var values = new HashMap<String, String>();
    int chars = 0;
    for (var segment : previous) {
      Objects.requireNonNull(segment, "previousSegment");
      String key = required(segment.key(), "previous key", 128);
      String text = required(segment.text(), "previous text", 500);
      if (!wellFormedUtf16(text)) throw new IllegalArgumentException("previous text is invalid");
      chars = Math.addExact(chars, text.length());
      if (chars > 500) throw new IllegalArgumentException("previous snapshot exceeds limits");
      if (values.put(key, text) != null)
        throw new IllegalArgumentException("duplicate previous segment key");
    }
    var currentKeys = current.captions().stream().flatMap(g -> g.segments().stream())
        .map(Segment::key).collect(java.util.stream.Collectors.toSet());
    if (!currentKeys.containsAll(values.keySet()))
      throw new IllegalArgumentException("previous segment is not visible in current snapshot");
    var groups = current.captions().stream().map(group -> {
      var segments = group.segments().stream().filter(s -> values.containsKey(s.key()))
          .map(s -> new Segment(s.key(), values.get(s.key()), s.offsetMs(), false, s.line())).toList();
      return segments.isEmpty() ? null : new Group(group.windowId(), group.startMs(), segments);
    }).filter(Objects::nonNull).toList();
    return groups.isEmpty() ? null : new Snapshot(groups);
  }

  public record PreviousSegment(String key, String text) {}

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
    public Group(List<Segment> segments) { this(null, null, segments); }
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
    public Segment(String key, String text, boolean append, long line) {
      this(key, text, null, append, line);
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
