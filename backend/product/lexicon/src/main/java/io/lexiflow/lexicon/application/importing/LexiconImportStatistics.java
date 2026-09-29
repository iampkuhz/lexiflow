package io.lexiflow.lexicon.application.importing;

import java.util.Map;

/**
 * 完整来源扫描的隐私安全导入统计，仅保存数字与固定规则标识。 预热候选不参与这些完整扫描数字。
 *
 * @param inputRows 来源凭据中的原始行数。
 * @param preparedRows 完整扫描准备成功的词条数。
 * @param hintRows 可提示词条数。
 * @param blockedRows 被阻断词条数。
 * @param reasonCounts 固定规则标识计数，不含任何词条文本。
 */
public record LexiconImportStatistics(
    long inputRows,
    long preparedRows,
    long hintRows,
    long blockedRows,
    Map<String, Long> reasonCounts) {
  /** 校验计数并复制规则计数。 */
  public LexiconImportStatistics {
    if (inputRows < 0
        || preparedRows < 0
        || hintRows < 0
        || blockedRows < 0
        || hintRows + blockedRows != preparedRows)
      throw new IllegalArgumentException("invalid import statistics");
    reasonCounts = Map.copyOf(reasonCounts);
  }
}
