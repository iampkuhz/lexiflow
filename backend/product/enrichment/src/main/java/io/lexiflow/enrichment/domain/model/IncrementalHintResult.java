package io.lexiflow.enrichment.domain.model;

import java.util.List;
import java.util.Objects;

/**
 * 按当前显示顺序排列、定位到片段 key 的增量提示。
 *
 * @param processedKeys 本次所有 append 片段的 key，按显示顺序排列，包含未命中提示的片段。
 * @param hints 定位到新增片段、可跨紧邻旧尾词的非重叠提示列表，可为空。
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
   * @param startKey 提示起点所在的片段 key。
   * @param startOffset 起点在 startKey 片段内的 UTF-16 偏移，包含该位置。
   * @param endKey 提示终点所在的片段 key，必须落在本次新增片段内。
   * @param endOffset 终点在 endKey 片段内的 UTF-16 偏移，不包含该位置。
   * @param chineseGloss 已发布词义的中文短释义。
   * @param lexiconEntryId 已发布词条的稳定身份。
   * @param lexiconVersion 本次响应使用的单一词库发布版本。
   * @param senseId 已发布词义的稳定身份。
   */
  public record Hint(
      String startKey,
      int startOffset,
      String endKey,
      int endOffset,
      String chineseGloss,
      String lexiconEntryId,
      long lexiconVersion,
      String senseId) {
    /** 校验每条提示具有非空来源身份。 */
    public Hint {
      Objects.requireNonNull(startKey, "startKey");
      Objects.requireNonNull(endKey, "endKey");
      Objects.requireNonNull(chineseGloss, "chineseGloss");
      Objects.requireNonNull(lexiconEntryId, "lexiconEntryId");
      Objects.requireNonNull(senseId, "senseId");
    }
  }
}
