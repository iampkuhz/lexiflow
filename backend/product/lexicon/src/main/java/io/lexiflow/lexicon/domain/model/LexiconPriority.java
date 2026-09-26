package io.lexiflow.lexicon.domain.model;

/**
 * 词库发布时计算的非个人化预热优先级。
 *
 * @param frequencyZipf 语料频率的 Zipf 标度，取值范围为 0 至 8。
 * @param complexListCount 收录该词条的复杂学习词表数量。
 * @param memoryPriority 由频率与复杂词表证据确定的 0 至 1000 分数。
 */
public record LexiconPriority(double frequencyZipf, int complexListCount, int memoryPriority) {
  private static final double MIN_ZIPF = 0.0;
  private static final double MAX_ZIPF = 8.0;

  /** 校验不可由用户行为推导的发布优先级。 */
  public LexiconPriority {
    if (!Double.isFinite(frequencyZipf) || frequencyZipf < MIN_ZIPF || frequencyZipf > MAX_ZIPF) {
      throw new IllegalArgumentException("frequencyZipf must be within 0 and 8");
    }
    if (complexListCount < 0 || memoryPriority < 0 || memoryPriority > 1000) {
      throw new IllegalArgumentException("lexicon priority values are invalid");
    }
  }

  /**
   * 由语料频率和复杂词表覆盖计算预热分数，不接受个人观看或熟悉度输入。
   *
   * @param frequencyZipf 含义：语料频率的 Zipf 标度。取值范围：有限数字 0 至 8。
   * @param complexListCount 含义：收录该词条的复杂学习词表数量。取值范围：大于等于 0。
   * @return 由公开词汇证据导出的预热优先级。
   */
  public static LexiconPriority fromEvidence(double frequencyZipf, int complexListCount) {
    if (!Double.isFinite(frequencyZipf) || frequencyZipf < MIN_ZIPF || frequencyZipf > MAX_ZIPF) {
      throw new IllegalArgumentException("frequencyZipf must be within 0 and 8");
    }
    if (complexListCount < 0) {
      throw new IllegalArgumentException("complexListCount must not be negative");
    }
    var frequencyFit = triangularFrequencyFit(frequencyZipf);
    var complexityEvidence = 1.0 - Math.exp(-complexListCount);
    var score = (int) Math.round(1000.0 * (0.55 * frequencyFit + 0.45 * complexityEvidence));
    return new LexiconPriority(frequencyZipf, complexListCount, score);
  }

  /**
   * 由已知或缺失的来源排名计算优先级；缺失排名永不进入内存预热。
   *
   * @param frequencyZipf 含义：语料频率的 Zipf 标度。取值范围：有限数字 0 至 8。
   * @param complexListCount 含义：收录该词条的复杂学习词表数量。取值范围：大于等于 0。
   * @param hasRank 含义：来源是否提供可用排名。取值范围：true 或 false。
   * @return 有排名时的计算优先级，否则为不可预热的零分优先级。
   */
  public static LexiconPriority fromRankedEvidence(
      double frequencyZipf, int complexListCount, boolean hasRank) {
    if (!hasRank) {
      return new LexiconPriority(0.0, complexListCount, 0);
    }
    return fromEvidence(frequencyZipf, complexListCount);
  }

  /**
   * 为尚未导入语料频率的内置演示词条提供最低优先级。
   *
   * @return 频率、复杂词表数量和预热分数均为零的优先级。
   */
  public static LexiconPriority unranked() {
    return new LexiconPriority(0.0, 0, 0);
  }

  private static double triangularFrequencyFit(double zipf) {
    if (zipf <= 2.5 || zipf >= 6.0) {
      return 0.0;
    }
    if (zipf <= 4.3) {
      return (zipf - 2.5) / 1.8;
    }
    return (6.0 - zipf) / 1.7;
  }
}
