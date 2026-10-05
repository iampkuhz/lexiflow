package io.lexiflow.enrichment.domain.model;

import java.util.List;
import java.util.Objects;

/**
 * 确定性提示选择及其实际歧义、重叠淘汰计数。
 *
 * @param hints 按起点排序的不可变提示列表。
 * @param ambiguous 匹配范围中存在多个词条身份的唯一范围数。
 * @param overlapDropped 通过前置过滤并仅因与已选提示重叠而落选的候选数。
 */
public record HintSelectionResult(List<AnnotationHint> hints, int ambiguous, int overlapDropped) {
  /** 防御性复制提示并验证计数。 */
  public HintSelectionResult {
    hints = List.copyOf(Objects.requireNonNull(hints, "hints"));
    if (ambiguous < 0 || overlapDropped < 0)
      throw new IllegalArgumentException("selection counts must be nonnegative");
  }
}
