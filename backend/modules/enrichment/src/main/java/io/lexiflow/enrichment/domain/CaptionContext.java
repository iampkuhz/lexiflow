package io.lexiflow.enrichment.domain;

import java.util.Objects;
import java.util.UUID;

/**
 * 一段已定位字幕及其内容修订，独立于来源 DOM 和浏览器状态。
 *
 * @param contentId 内容稳定 UUID。
 * @param contentRevision 内容或字幕修订。
 * @param segmentId 当前规范字幕片段 identity。
 * @param caption 当前英文字幕全文。
 * @param startOffset 目标半开区间起点。
 * @param endOffset 目标半开区间终点。
 */
public record CaptionContext(
    UUID contentId,
    long contentRevision,
    String segmentId,
    String caption,
    int startOffset,
    int endOffset) {

  /** 校验内容修订、字幕长度和半开区间。 */
  public CaptionContext {
    Objects.requireNonNull(contentId, "contentId");
    segmentId = CaptionSegment.requiredDigest(segmentId, "segmentId");
    caption = required(caption, "caption");
    if (contentRevision < 1) {
      throw new IllegalArgumentException("contentRevision must be positive");
    }
    if (caption.length() > 500) {
      throw new IllegalArgumentException("caption must not exceed 500 characters");
    }
    if (startOffset < 0 || endOffset <= startOffset || endOffset > caption.length()) {
      throw new IllegalArgumentException("caption range is invalid");
    }
  }

  /**
   * 从已验证的内容修订中创建一个与当前片段完全绑定的输入值。
   *
   * @param revision 含义：拥有片段的不可变内容修订。取值范围：非空且其 revision 为正整数。
   * @param segment 含义：修订中的规范字幕片段。取值范围：非空且必须属于 revision。
   * @param startOffset 含义：目标词段的半开区间起点。取值范围：0 至 segment 文本长度减一。
   * @param endOffset 含义：目标词段的半开区间终点。取值范围：大于 startOffset 且不超过文本长度。
   * @return 与 revision 和 segment 完全绑定的字幕上下文。
   */
  public static CaptionContext from(
      ContentRevision revision, CaptionSegment segment, int startOffset, int endOffset) {
    Objects.requireNonNull(revision, "revision");
    Objects.requireNonNull(segment, "segment");
    return new CaptionContext(
        revision.contentId(),
        revision.revision(),
        segment.segmentId(),
        segment.normalizedText(),
        startOffset,
        endOffset);
  }

  private static String required(String value, String field) {
    Objects.requireNonNull(value, field);
    if (value.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return value;
  }
}
