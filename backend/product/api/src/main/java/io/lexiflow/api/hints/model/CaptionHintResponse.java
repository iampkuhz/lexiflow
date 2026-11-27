package io.lexiflow.api.hints.model;

import java.util.List;
import java.util.Objects;

/**
 * 返回增量处理结果及其词库发布版本。 发布版本只在顶层出现，避免每条提示重复携带相同身份。
 *
 * @param processedKeys 含义：本次已处理的字幕片段键。取值范围：不可变列表，按处理顺序排列。
 * @param lexiconVersion 含义：本次查询使用的词库版本。取值范围：无提示时可空，否则为正 long。
 * @param hints 含义：本次返回的提示。取值范围：不可变列表，且与词库版本约束一致。
 */
public record CaptionHintResponse(
    List<String> processedKeys, Long lexiconVersion, List<Hint> hints) {
  /** 校验发布版本与返回提示之间的一致性，并冻结列表。 */
  public CaptionHintResponse {
    processedKeys = List.copyOf(Objects.requireNonNull(processedKeys));
    hints = List.copyOf(Objects.requireNonNull(hints));
    if (lexiconVersion == null ? !hints.isEmpty() : lexiconVersion < 1)
      throw new IllegalArgumentException("invalid lexicon version");
  }

  /**
   * 创建省略显式版本的响应；仅在存在提示时使用版本 1。
   *
   * @param processedKeys 含义：本次已处理的字幕片段键。取值范围：处理顺序列表。
   * @param hints 含义：本次返回的词库提示。取值范围：空列表或有效提示列表。
   */
  public CaptionHintResponse(List<String> processedKeys, List<Hint> hints) {
    this(processedKeys, hints.isEmpty() ? null : 1L, hints);
  }

  /**
   * 将一个词库提示定位到字幕片段的 UTF-16 半开范围。 释义和词条身份来自已发布词库资料。
   *
   * @param startKey 含义：提示起始片段键。取值范围：非空字符串。
   * @param startOffset 含义：起点在起始片段中的偏移。取值范围：非负 UTF-16 偏移。
   * @param endKey 含义：提示结束片段键。取值范围：非空字符串。
   * @param endOffset 含义：终点在结束片段中的半开偏移。取值范围：非负 UTF-16 偏移。
   * @param chineseGloss 含义：提示中文短释。取值范围：非空字符串。
   * @param lexiconEntryId 含义：已发布词条身份。取值范围：非空规范身份字符串。
   * @param frequencyRank 含义：已发布的连续展示排名。取值范围：可空；存在时为正 long。
   */
  public record Hint(
      String startKey,
      int startOffset,
      String endKey,
      int endOffset,
      String chineseGloss,
      String lexiconEntryId,
      Long frequencyRank) {
    /**
     * 从提示位置和内容构造响应；附加身份参数不进入响应。
     *
     * @param startKey 含义：提示起始片段键。取值范围：非空字符串。
     * @param startOffset 含义：起始偏移。取值范围：非负 UTF-16 偏移。
     * @param endKey 含义：提示结束片段键。取值范围：非空字符串。
     * @param endOffset 含义：结束偏移。取值范围：非负 UTF-16 偏移。
     * @param gloss 含义：中文短释。取值范围：非空字符串。
     * @param entryId 含义：词条身份。取值范围：非空身份字符串。
     * @param ignoredVersion 含义：调用方提供的附加版本。取值范围：任意 long，实际忽略。
     * @param ignoredSenseId 含义：调用方提供的附加义项身份。取值范围：可空，实际忽略。
     * @param ignoredLemma 含义：调用方提供的附加主词形。取值范围：可空，实际忽略。
     */
    public Hint(
        String startKey,
        int startOffset,
        String endKey,
        int endOffset,
        String gloss,
        String entryId,
        long ignoredVersion,
        String ignoredSenseId,
        String ignoredLemma) {
      this(startKey, startOffset, endKey, endOffset, gloss, entryId, null);
    }
  }
}
