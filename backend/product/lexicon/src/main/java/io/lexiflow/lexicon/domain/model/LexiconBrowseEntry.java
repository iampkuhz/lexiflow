package io.lexiflow.lexicon.domain.model;

/**
 * 已发布词库浏览结果中的来源中立词项。
 *
 * @param entryId 含义：词条稳定身份。取值范围：正 long。
 * @param lemma 含义：规范词形。取值范围：非空白字符串。
 * @param gloss 含义：可展示的中文短释。取值范围：可空；阻断词条必须为空。
 * @param frequencyRank 含义：原始 BNC 频率排名。取值范围：可空；存在时为正数。
 * @param displayFrequencyRank 含义：发布展示名次。取值范围：可空；存在时为正安全整数。
 * @param hintState 含义：发布提示资格状态。取值范围：ELIGIBLE 或 BLOCKED。
 */
public record LexiconBrowseEntry(
    long entryId, String lemma, String gloss, Long frequencyRank, Long displayFrequencyRank, PublishedHintState hintState) {
  /** 校验发布身份、正排名及短释与提示状态的一致性。 */
  public LexiconBrowseEntry {
    if (entryId < 1 || lemma == null || lemma.isBlank() || hintState == null)
      throw new IllegalArgumentException("browse entry is invalid");
    if (frequencyRank != null && frequencyRank < 1)
      throw new IllegalArgumentException("frequency rank must be positive");
    if (displayFrequencyRank != null
        && (displayFrequencyRank < 1 || displayFrequencyRank > 9_007_199_254_740_991L))
      throw new IllegalArgumentException("display rank must be a positive safe integer");
    if ((gloss == null) != (hintState == PublishedHintState.BLOCKED))
      throw new IllegalArgumentException("hint state does not match published gloss");
  }
}
