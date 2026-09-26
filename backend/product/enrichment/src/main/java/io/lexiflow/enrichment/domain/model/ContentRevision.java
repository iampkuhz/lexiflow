package io.lexiflow.enrichment.domain.model;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

/**
 * 一次不可变、来源中立的规范字幕内容修订。
 *
 * @param contentId 服务端稳定内容 UUID。
 * @param revision 单调正整数修订。
 * @param sourceKind 受控来源类别，不携带来源私有字段。
 * @param sourceReferenceDigest 不可逆来源引用摘要。
 * @param normalizationVersion 规范化算法版本。
 * @param segments 有序、不可变的字幕片段。
 */
public record ContentRevision(
    UUID contentId,
    long revision,
    String sourceKind,
    String sourceReferenceDigest,
    long normalizationVersion,
    List<CaptionSegment> segments) {

  /** 校验修订身份、来源摘要、稳定 segment identity 和每条轨道时间线。 */
  public ContentRevision {
    Objects.requireNonNull(contentId, "contentId");
    if (revision < 1 || normalizationVersion < 1) {
      throw new IllegalArgumentException("content revision versions must be positive");
    }
    sourceKind = required(sourceKind, "sourceKind");
    sourceReferenceDigest =
        CaptionSegment.requiredDigest(sourceReferenceDigest, "sourceReferenceDigest");
    segments = List.copyOf(segments);
    if (segments.isEmpty()) {
      throw new IllegalArgumentException("segments must not be empty");
    }
    validateSegments(contentId, revision, normalizationVersion, segments);
  }

  /**
   * 为一个已规范片段计算跨重放稳定、跨 revision 隔离的身份摘要。
   *
   * @param contentId 含义：拥有片段的稳定内容标识。取值范围：非空 UUID。
   * @param revision 含义：内容或字幕修订号。取值范围：正整数。
   * @param normalizationVersion 含义：生成片段文本的规范化算法版本。取值范围：正整数。
   * @param trackId 含义：来源字幕轨道的稳定标识。取值范围：非空白字符串。
   * @param sequenceNo 含义：片段在轨道内的顺序号。取值范围：大于等于 0。
   * @param startMillis 含义：片段半开时间区间的起点。取值范围：大于等于 0。
   * @param endMillis 含义：片段半开时间区间的终点。取值范围：大于 startMillis。
   * @param normalizedText 含义：已规范或待规范的字幕正文。取值范围：规范化后长度为 1 至 500。
   * @return 小写十六进制 SHA-256 片段标识。
   */
  public static String segmentIdFor(
      UUID contentId,
      long revision,
      long normalizationVersion,
      String trackId,
      long sequenceNo,
      long startMillis,
      long endMillis,
      String normalizedText) {
    Objects.requireNonNull(contentId, "contentId");
    var canonical =
        String.join(
            "\u0000",
            contentId.toString(),
            Long.toString(revision),
            Long.toString(normalizationVersion),
            trackId,
            Long.toString(sequenceNo),
            Long.toString(startMillis),
            Long.toString(endMillis),
            CaptionSegment.normalizeText(normalizedText));
    try {
      return HexFormat.of()
          .formatHex(
              MessageDigest.getInstance("SHA-256")
                  .digest(canonical.getBytes(StandardCharsets.UTF_8)));
    } catch (NoSuchAlgorithmException exception) {
      throw new IllegalStateException("SHA-256 is unavailable", exception);
    }
  }

  private static void validateSegments(
      UUID contentId, long revision, long normalizationVersion, List<CaptionSegment> segments) {
    var priorByTrack = new java.util.HashMap<String, CaptionSegment>();
    var ids = new java.util.HashSet<String>();
    for (var segment : segments) {
      if (!ids.add(segment.segmentId())) {
        throw new IllegalArgumentException("segment identities must be unique");
      }
      var expected =
          segmentIdFor(
              contentId,
              revision,
              normalizationVersion,
              segment.trackId(),
              segment.sequenceNo(),
              segment.startMillis(),
              segment.endMillis(),
              segment.normalizedText());
      if (!expected.equals(segment.segmentId())) {
        throw new IllegalArgumentException("segmentId does not match immutable revision input");
      }
      var prior = priorByTrack.put(segment.trackId(), segment);
      if (prior != null
          && (segment.sequenceNo() != prior.sequenceNo() + 1
              || segment.startMillis() < prior.endMillis())) {
        throw new IllegalArgumentException("track sequence or timeline is not monotonic");
      }
    }
  }

  private static String required(String value, String field) {
    Objects.requireNonNull(value, field);
    if (value.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return value;
  }
}
