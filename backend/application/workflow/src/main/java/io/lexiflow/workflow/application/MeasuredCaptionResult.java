package io.lexiflow.workflow.application;

import io.lexiflow.enrichment.domain.CaptionHintResult;
import java.util.Objects;

/**
 * 当前请求的确定性结果与无内容分段计时；不持久化字幕或观看记录。
 *
 * @param result 含义：确定性提示结果。取值范围：非空。
 * @param queryNanos 含义：词库候选查询耗时。取值范围：非负纳秒。
 * @param rulesNanos 含义：规则筛选耗时。取值范围：非负纳秒。
 * @param candidateCount 含义：本次公开查询返回的候选数。取值范围：非负整数。
 */
public record MeasuredCaptionResult(
    CaptionHintResult result, long queryNanos, long rulesNanos, int candidateCount) {

  /** 校验结果与计时，拒绝负数或缺失结果。 */
  public MeasuredCaptionResult {
    Objects.requireNonNull(result, "result");
    if (queryNanos < 0 || rulesNanos < 0 || candidateCount < 0) {
      throw new IllegalArgumentException("measurements must be nonnegative");
    }
  }
}
