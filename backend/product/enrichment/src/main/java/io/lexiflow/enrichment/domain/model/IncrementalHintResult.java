package io.lexiflow.enrichment.domain.model;

import java.util.List;
import java.util.Objects;

/**
 * 按当前显示顺序排列、定位到片段 key 的增量提示。
 *
 * @param processedKeys 含义：本次所有待处理片段键。取值范围：不可变列表，按显示顺序包含未命中项。
 * @param hints 含义：返回的非重叠提示。取值范围：不可变列表，可跨紧邻旧尾词并可为空。
 */
public record IncrementalHintResult(List<String> processedKeys, List<Hint> hints) {
  /** 冻结本次处理 key 与提示顺序。 */
  public IncrementalHintResult {
    processedKeys = List.copyOf(Objects.requireNonNull(processedKeys, "processedKeys"));
    hints = List.copyOf(Objects.requireNonNull(hints, "hints"));
  }

  /**
   * 跨连续新增片段的半开范围和已发布词库身份。
   *
   * @param startKey 含义：提示起点所在片段的键。取值范围：非空字符串。
   * @param startOffset 含义：起点在 startKey 片段内的 UTF-16 偏移。取值范围：非负，包含该位置。
   * @param endKey 含义：提示终点所在片段的键。取值范围：非空字符串，且属于本次新增片段。
   * @param endOffset 含义：终点在 endKey 片段内的 UTF-16 偏移。取值范围：非负，不包含该位置。
   * @param chineseGloss 含义：已发布词义的中文短释。取值范围：非空字符串。
   * @param lexiconEntryId 含义：已发布词条稳定身份。取值范围：正 long 的规范十进制字符串。
   * @param lexiconVersion 含义：本次响应使用的词库发布版本。取值范围：正 long。
   * @param senseId 含义：已发布词义的稳定身份。取值范围：非空字符串。
   * @param lemma 含义：匹配词条的真实规范主词形。取值范围：非空字符串。
   * @param frequencyRank 含义：只读词库提供的展示排名。取值范围：可空；存在时为正 long。
   */
  public record Hint(
      String startKey,
      int startOffset,
      String endKey,
      int endOffset,
      String chineseGloss,
      String lexiconEntryId,
      long lexiconVersion,
      String senseId,
      String lemma,
      Long frequencyRank) {
    /** 校验每条提示具有非空来源身份。 */
    public Hint {
      Objects.requireNonNull(startKey, "startKey");
      Objects.requireNonNull(endKey, "endKey");
      Objects.requireNonNull(chineseGloss, "chineseGloss");
      Objects.requireNonNull(lexiconEntryId, "lexiconEntryId");
      Objects.requireNonNull(senseId, "senseId");
      Objects.requireNonNull(lemma, "lemma");
    }
  }
}
