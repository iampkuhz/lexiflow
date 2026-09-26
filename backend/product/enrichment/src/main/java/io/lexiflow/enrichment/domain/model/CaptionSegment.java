package io.lexiflow.enrichment.domain.model;

import java.text.Normalizer;
import java.util.Objects;

/**
 * 一个属于 ContentRevision 的不可变规范字幕片段。
 *
 * @param segmentId 由 revision、轨道、位置和规范文本派生的 SHA-256 标识。
 * @param trackId 来源轨道稳定标识。
 * @param sequenceNo 同一轨道内从零开始的顺序号。
 * @param startMillis 半开时间区间起点。
 * @param endMillis 半开时间区间终点。
 * @param normalizedText 规范化且受长度限制的英文字幕。
 */
public record CaptionSegment(
    String segmentId,
    String trackId,
    long sequenceNo,
    long startMillis,
    long endMillis,
    String normalizedText) {

  /** 校验片段 identity、时间线位置和规范文本。 */
  public CaptionSegment {
    segmentId = requiredDigest(segmentId, "segmentId");
    trackId = required(trackId, "trackId");
    if (sequenceNo < 0 || startMillis < 0 || endMillis <= startMillis) {
      throw new IllegalArgumentException("caption segment timeline is invalid");
    }
    normalizedText = normalizeText(normalizedText);
  }

  /**
   * 将字幕正文规范为 NFC、单一空白和不超过 500 个 UTF-16 code unit。
   *
   * @param value 含义：待规范化的英文字幕正文。取值范围：非空白，规范化后长度为 1 至 500。
   * @return 规范化后的字幕正文。
   */
  public static String normalizeText(String value) {
    var normalized = Normalizer.normalize(required(value, "normalizedText"), Normalizer.Form.NFC);
    normalized = normalized.replaceAll("\\s+", " ").trim();
    if (normalized.isBlank() || normalized.length() > 500) {
      throw new IllegalArgumentException("normalizedText length is invalid");
    }
    return normalized;
  }

  static String requiredDigest(String value, String field) {
    var digest = required(value, field);
    if (!digest.matches("[0-9a-f]{64}")) {
      throw new IllegalArgumentException(field + " must be a lowercase SHA-256 digest");
    }
    return digest;
  }

  private static String required(String value, String field) {
    Objects.requireNonNull(value, field);
    if (value.isBlank()) {
      throw new IllegalArgumentException(field + " must not be blank");
    }
    return value;
  }
}
