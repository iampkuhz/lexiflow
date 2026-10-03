package io.lexiflow.observability.platform;

import java.util.List;
import java.util.Objects;
import java.util.regex.Pattern;

/**
 * 本机敏感分析台账中的单片段记录；文本仅供专用台账，不得进入普通日志。
 *
 * @param segmentId 含义：原片段键 SHA-256 摘要。取值范围：64 位小写十六进制。
 * @param english 含义：敏感英文片段正文。取值范围：非 null。
 * @param translatedRanges 含义：覆盖此片段的翻译范围。取值范围：防御复制后的有序列表。
 */
public record SegmentAnalysisRecord(
    String segmentId, String english, List<TranslatedRange> translatedRanges) {
  private static final Pattern SHA256 = Pattern.compile("[0-9a-f]{64}");

  /**
   * 创建不可变的片段记录。
   *
   * @param segmentId 含义：原片段键的 SHA-256 十六进制摘要。取值范围：64 位小写十六进制。
   * @param english 含义：片段英文原文。取值范围：非 null。
   * @param translatedRanges 含义：该片段内翻译范围。取值范围：非 null、升序且互不重叠。
   */
  public SegmentAnalysisRecord {
    if (segmentId == null || !SHA256.matcher(segmentId).matches())
      throw new IllegalArgumentException("invalid segment id");
    Objects.requireNonNull(english, "english");
    translatedRanges = List.copyOf(Objects.requireNonNull(translatedRanges, "translatedRanges"));
    int previousEnd = 0;
    for (var range : translatedRanges) {
      if (range.start() < previousEnd || range.end() > english.length())
        throw new IllegalArgumentException("invalid translated range order or bounds");
      if (splitsSurrogate(english, range.start()) || splitsSurrogate(english, range.end()))
        throw new IllegalArgumentException("range splits surrogate pair");
      previousEnd = range.end();
    }
  }

  private static boolean splitsSurrogate(String text, int offset) {
    return offset > 0
        && offset < text.length()
        && Character.isHighSurrogate(text.charAt(offset - 1))
        && Character.isLowSurrogate(text.charAt(offset));
  }

  /**
   * 一个已发布释义在英文片段内的 UTF-16 半开范围。
   *
   * @param start 含义：范围起点。取值范围：非负 UTF-16 偏移。
   * @param end 含义：范围终点。取值范围：大于 start 的半开偏移。
   * @param hintId 含义：跨片段提示标识。取值范围：64 位小写十六进制 SHA-256。
   * @param gloss 含义：发布中文释义。取值范围：非 null 且非空白。
   * @param lexiconVersion 含义：发布词库版本。取值范围：正整数。
   * @param anchor 含义：是否为提示锚点。取值范围：布尔值。
   */
  public record TranslatedRange(
      int start, int end, String hintId, String gloss, long lexiconVersion, boolean anchor) {
    /**
     * 校验提示范围与发布身份。
     *
     * @param start 含义：半开范围起点。取值范围：非负整数。
     * @param end 含义：半开范围终点。取值范围：严格大于 start。
     * @param hintId 含义：跨片段提示标识。取值范围：64 位小写十六进制 SHA-256。
     * @param gloss 含义：发布的中文释义。取值范围：非 null 且非空白。
     * @param lexiconVersion 含义：已发布词库版本。取值范围：正整数。
     * @param anchor 含义：此范围是否为提示展示锚点。取值范围：true 或 false。
     */
    public TranslatedRange {
      if (start < 0 || end <= start || lexiconVersion <= 0)
        throw new IllegalArgumentException("invalid translated range");
      if (hintId == null || !SHA256.matcher(hintId).matches())
        throw new IllegalArgumentException("invalid hint id");
      if (gloss == null || gloss.isBlank()) throw new IllegalArgumentException("blank gloss");
    }
  }
}
