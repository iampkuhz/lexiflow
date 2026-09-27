package io.lexiflow.enrichment.application.caption.model;

import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import java.util.Objects;

/**
 * 当前新增区间的结果、计时与仅供本次诊断的处理文本。
 *
 * @param result 按片段定位的已处理覆盖和提示结果。
 * @param queryNanos 各新增区间查询已发布词库的累计纳秒耗时，非负。
 * @param rulesNanos 各新增区间执行确定性规则的累计纳秒耗时，非负。
 * @param candidateCount 各新增区间返回的候选总数，非负。
 * @param processedEnglish 仅本次处理区间的英文，不代表整屏字幕。
 * @param processedWithHints 仅本次处理区间及已命中提示，不代表整屏最终渲染。
 */
public record MeasuredIncrementalCaptionResult(
    IncrementalHintResult result,
    long queryNanos,
    long rulesNanos,
    int candidateCount,
    String processedEnglish,
    String processedWithHints) {
  /** 校验结果、计时及本次处理文本。 */
  public MeasuredIncrementalCaptionResult {
    Objects.requireNonNull(result, "result");
    Objects.requireNonNull(processedEnglish, "processedEnglish");
    Objects.requireNonNull(processedWithHints, "processedWithHints");
    if (queryNanos < 0 || rulesNanos < 0 || candidateCount < 0) {
      throw new IllegalArgumentException("measurements must be nonnegative");
    }
  }
}
