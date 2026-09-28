package io.lexiflow.enrichment.application.caption.model;

import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import java.util.Objects;
import java.util.OptionalLong;

/**
 * 当前新增区间的业务结果、计时与聚合查询计数。
 *
 * @param result 按片段定位的已处理覆盖和提示结果。
 * @param queryNanos 各新增区间查询已发布词库的累计纳秒耗时，非负。
 * @param rulesNanos 各新增区间执行确定性规则的累计纳秒耗时，非负。
 * @param candidateCount 各新增区间返回的候选总数，非负。
 * @param queryCounts 各新增区间词库查询计数的累计值，非空且各字段非负。
 * @param diagnostics 新增区间选择计数、发布版本安全结果与候选阶段耗时。
 */
public record MeasuredIncrementalCaptionResult(
    IncrementalHintResult result,
    long queryNanos,
    long rulesNanos,
    int candidateCount,
    LexiconLookupResult.Counts queryCounts,
    Diagnostics diagnostics) {
  /**
   * 本次增量请求的真实选择、版本安全和候选阶段测量。
   *
   * @param newRanges 实际规划出的新增连续区间数。
   * @param ambiguous 存在多个词条身份的唯一匹配范围总数。
   * @param overlapDropped 仅因与已选提示重叠而落选的候选数。
   * @param publishedVersion 唯一已知发布版本；未知或混版时为空。
   * @param versionConflict 是否观察到多个不一致版本。
   * @param candidatesNanos 区间规划与候选查表键生成累计纳秒耗时。
   */
  public record Diagnostics(
      int newRanges,
      int ambiguous,
      int overlapDropped,
      OptionalLong publishedVersion,
      boolean versionConflict,
      long candidatesNanos) {
    /** 验证不可变版本值及非负计数。 */
    public Diagnostics {
      if (newRanges < 0 || ambiguous < 0 || overlapDropped < 0 || candidatesNanos < 0)
        throw new IllegalArgumentException("diagnostics must be nonnegative");
      Objects.requireNonNull(publishedVersion, "publishedVersion");
      if (publishedVersion.isPresent() && publishedVersion.getAsLong() < 0)
        throw new IllegalArgumentException("publishedVersion must be nonnegative");
      if (versionConflict && publishedVersion.isPresent())
        throw new IllegalArgumentException("conflicting versions cannot have a published version");
    }
  }

  /** 校验结果及测量数据。 */
  public MeasuredIncrementalCaptionResult {
    Objects.requireNonNull(result, "result");
    Objects.requireNonNull(queryCounts, "queryCounts");
    Objects.requireNonNull(diagnostics, "diagnostics");
    if (queryNanos < 0 || rulesNanos < 0 || candidateCount < 0) {
      throw new IllegalArgumentException("measurements must be nonnegative");
    }
  }
}
