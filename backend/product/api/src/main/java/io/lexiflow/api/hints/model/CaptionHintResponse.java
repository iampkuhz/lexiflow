package io.lexiflow.api.hints.model;

import java.util.List;
import java.util.Objects;

/**
 * 增量处理结果，范围定位到当前快照内片段 key。
 *
 * @param processedKeys 本次所有 append 片段的 key，按显示顺序排列，包含未命中提示的片段。
 * @param hints 定位到连续新增片段的非重叠提示列表，可为空。
 */
public record CaptionHintResponse(List<String> processedKeys, List<Hint> hints) {
  /** 冻结输出列表，避免控制器返回后被修改。 */
  public CaptionHintResponse {
    processedKeys = List.copyOf(Objects.requireNonNull(processedKeys, "processedKeys"));
    hints = List.copyOf(Objects.requireNonNull(hints, "hints"));
  }

  /**
   * 一个跨连续新增片段的半开词段提示。
   *
   * @param startKey 提示起点所在的片段 key。
   * @param startOffset 起点在 startKey 片段内的 UTF-16 偏移，包含该位置。
   * @param endKey 提示终点所在的片段 key，与起点之间仅允许连续新增片段。
   * @param endOffset 终点在 endKey 片段内的 UTF-16 偏移，不包含该位置。
   * @param chineseGloss 已发布词义的中文短释义。
   * @param lexiconEntryId 含义：已发布词条正 BIGINT 的精确十进制字符串。取值范围：1 至 9223372036854775807，不转为 JSON 数字。
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
      String senseId) {}
}
