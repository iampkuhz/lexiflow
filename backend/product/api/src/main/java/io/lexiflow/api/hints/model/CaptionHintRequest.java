package io.lexiflow.api.hints.model;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import java.util.List;
import java.util.Objects;

/**
 * 接收增量提示请求中的仍可见旧片段与当前可见快照。 请求体仅表达当前字幕状态，不建立服务端字幕会话。
 *
 * @param previousSegments 含义：客户端确认仍可见的先前片段。取值范围：非空列表，元素由片段键和文字组成。
 * @param currentSnapshot 含义：本次当前可见字幕快照。取值范围：非空快照对象。
 */
public record CaptionHintRequest(List<PreviousSegment> previousSegments, Snapshot currentSnapshot) {
  /** 校验请求包含完整的旧片段列表和当前快照。 */
  public CaptionHintRequest {
    previousSegments = List.copyOf(Objects.requireNonNull(previousSegments, "previousSegments"));
    Objects.requireNonNull(currentSnapshot, "currentSnapshot");
  }

  /**
   * 将请求映射为领域双快照合同，并使用默认单词截止值。 领域对象随后校验旧片段可见性和快照边界。
   *
   * @return 已完成结构映射的领域请求
   */
  public CaptionIncrementalRequest toDomain() {
    return toDomain(1500);
  }

  /**
   * 将请求映射为领域双快照合同并应用给定的单词截止排名。 该值只用于领域提示筛选，不改变输入快照。
   *
   * @param commonWordCutoff 含义：常见单词提示的排名截止值。取值范围：1000、1500 或 2000。
   * @return 已完成结构映射的领域请求
   */
  public CaptionIncrementalRequest toDomain(int commonWordCutoff) {
    var currentGroups = currentSnapshot.captions().stream().map(CaptionGroup::toCurrent).toList();
    var old =
        previousSegments.stream()
            .map(s -> new CaptionIncrementalRequest.PreviousSegment(s.key(), s.text()))
            .toList();
    return new CaptionIncrementalRequest(
        old, new CaptionIncrementalRequest.Snapshot(currentGroups), commonWordCutoff);
  }

  /**
   * 客户端上次成功确认且仍可见的字幕片段。 该记录只参与构造比较快照，不在服务端持久保存。
   *
   * @param key 含义：片段的稳定客户端键。取值范围：非空字符串。
   * @param text 含义：片段当前可见文字。取值范围：非空字符串。
   */
  public record PreviousSegment(String key, String text) {}

  /**
   * 当前可见字幕组的不可变快照。 快照按来源提供的顺序保留显示内容。
   *
   * @param captions 含义：当前可见字幕组。取值范围：不能为 null，允许为空列表。
   */
  public record Snapshot(List<CaptionGroup> captions) {
    /** 冻结当前字幕组列表。 */
    public Snapshot {
      captions = List.copyOf(Objects.requireNonNull(captions, "captions"));
    }
  }

  /**
   * 具有同一显示上下文的字幕片段集合。 组内片段按来源显示顺序保留。
   *
   * @param segments 含义：本组可见片段。取值范围：不能为 null，转换为领域对象时至少包含一个片段。
   */
  public record CaptionGroup(List<Segment> segments) {
    /** 冻结本组字幕片段列表。 */
    public CaptionGroup {
      segments = List.copyOf(Objects.requireNonNull(segments, "segments"));
    }

    CaptionIncrementalRequest.Group toCurrent() {
      return new CaptionIncrementalRequest.Group(
          null, null, segments.stream().map(Segment::toDomain).toList());
    }
  }

  /**
   * HTTP 请求中的一条字幕片段。 字段映射后由领域模型验证键、文字和可见顺序约束。
   *
   * @param key 含义：客户端稳定片段键。取值范围：非空字符串。
   * @param text 含义：片段可见文字。取值范围：非空字符串。
   * @param append 含义：片段是否待本次处理。取值范围：非空布尔值。
   * @param line 含义：片段在组内的可视行号。取值范围：非空非负 long。
   */
  public record Segment(String key, String text, Boolean append, Long line) {
    CaptionIncrementalRequest.Segment toDomain() {
      return new CaptionIncrementalRequest.Segment(
          key, text, null, Objects.requireNonNull(append), Objects.requireNonNull(line));
    }
  }
}
