package io.lexiflow.enrichment.application.caption.model;

import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import java.util.Objects;

/**
 * 当前新增区间的业务结果、计时与聚合查询计数。
 *
 * @param result 按片段定位的已处理覆盖和提示结果。
 * @param queryNanos 各新增区间查询已发布词库的累计纳秒耗时，非负。
 * @param rulesNanos 各新增区间执行确定性规则的累计纳秒耗时，非负。
 * @param candidateCount 各新增区间返回的候选总数，非负。
 * @param queryCounts 各新增区间词库查询计数的累计值，非空且各字段非负。
 */
public record MeasuredIncrementalCaptionResult(
    IncrementalHintResult result,
    long queryNanos,
    long rulesNanos,
    int candidateCount,
    LexiconLookupResult.Counts queryCounts) {
  /** 校验结果及测量数据。 */
  public MeasuredIncrementalCaptionResult {
    Objects.requireNonNull(result, "result");
    Objects.requireNonNull(queryCounts, "queryCounts");
    if (queryNanos < 0 || rulesNanos < 0 || candidateCount < 0) {
      throw new IllegalArgumentException("measurements must be nonnegative");
    }
  }
}
