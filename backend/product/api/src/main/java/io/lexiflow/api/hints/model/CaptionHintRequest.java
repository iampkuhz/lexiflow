package io.lexiflow.api.hints.model;

import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import java.util.List;
import java.util.Objects;

/**
 * 双快照字幕增量提示请求；值校验由领域合同统一执行。
 *
 * @param captionTopicKey 字幕主题的不透明身份，非空且不超过 128 字符。
 * @param trackKey 来源轨道身份；无法可靠识别时为 null。
 * @param lastRequestedSnapshot 上次实际发出请求时冻结的可见快照；首次请求为 null。
 * @param currentSnapshot 本次可见快照，不能为空对象，允许无可见字幕。
 */
public record CaptionHintRequest(
    String captionTopicKey,
    String trackKey,
    Snapshot lastRequestedSnapshot,
    Snapshot currentSnapshot) {
  /** 在请求边界拒绝违反领域双快照合同的输入。 */
  public CaptionHintRequest {
    Objects.requireNonNull(currentSnapshot, "currentSnapshot");
    toDomain(captionTopicKey, trackKey, lastRequestedSnapshot, currentSnapshot);
  }

  /**
   * 映射 HTTP 请求值到无框架领域合同。
   *
   * @return 保留快照和来源元数据的已校验领域请求。
   */
  public CaptionIncrementalRequest toDomain() {
    return toDomain(captionTopicKey, trackKey, lastRequestedSnapshot, currentSnapshot);
  }

  private static CaptionIncrementalRequest toDomain(
      String topic, String track, Snapshot previous, Snapshot current) {
    return new CaptionIncrementalRequest(
        topic,
        track,
        previous == null ? null : previous.toDomain(),
        Objects.requireNonNull(current, "currentSnapshot").toDomain());
  }

  /**
   * 字幕组序列。
   *
   * @param captions 按显示顺序排列的字幕组；空列表表示当前无可见字幕。
   */
  public record Snapshot(List<CaptionGroup> captions) {
    CaptionIncrementalRequest.Snapshot toDomain() {
      return new CaptionIncrementalRequest.Snapshot(
          Objects.requireNonNull(captions, "captions").stream()
              .map(CaptionGroup::toDomain)
              .toList());
    }
  }

  /**
   * 一条字幕组。
   *
   * @param windowId 来源窗口身份；来源未提供时为 null。
   * @param startMs 来源字幕组在视频时间轴上的起始毫秒；未知时为 null。
   * @param segments 按可见顺序排列的非空片段列表。
   */
  public record CaptionGroup(String windowId, Long startMs, List<Segment> segments) {
    CaptionIncrementalRequest.Group toDomain() {
      return new CaptionIncrementalRequest.Group(
          windowId,
          startMs,
          Objects.requireNonNull(segments, "segments").stream().map(Segment::toDomain).toList());
    }
  }

  /**
   * 一段可见文字。
   *
   * @param key 插件生成的稳定片段身份，保留片段不换 key，新增后缀另建 key。
   * @param text 当前可见的非空片段文字，同 key 只允许删除前缀。
   * @param offsetMs 片段相对字幕组起点的毫秒偏移；来源未提供时为 null。
   * @param append 是否尚待本次处理；不是原生字幕的追加标记。
   * @param line 从零开始的组内可视行号，仅用于还原布局。
   */
  public record Segment(String key, String text, Long offsetMs, Boolean append, Long line) {
    CaptionIncrementalRequest.Segment toDomain() {
      return new CaptionIncrementalRequest.Segment(
          key,
          text,
          offsetMs,
          Objects.requireNonNull(append, "append"),
          Objects.requireNonNull(line, "line"));
    }
  }
}
