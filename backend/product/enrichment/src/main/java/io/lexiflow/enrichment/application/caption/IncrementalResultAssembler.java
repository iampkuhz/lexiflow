package io.lexiflow.enrichment.application.caption;

import io.lexiflow.enrichment.application.caption.model.MeasuredIncrementalCaptionResult;
import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import io.lexiflow.lexicon.domain.model.LexiconHintCandidate;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;

/** 合并单次请求覆盖、计数、版本安全性与跨区间去重。 */
final class IncrementalResultAssembler {
  /**
   * 单个已执行新增区间收集的查询与选择结果。
   *
   * @param lookup 当前区间实际执行的查询及其全部候选。
   * @param mapped 映射到片段坐标后的候选提示。
   * @param queryNanos 当前区间查询耗时。
   * @param rulesNanos 当前区间策略与映射耗时。
   * @param ambiguous 该区间的唯一歧义匹配范围数。
   * @param overlapDropped 该区间仅因重叠而落选的候选数。
   * @param candidatesNanos 该区间的候选词形生成耗时。
   */
  record RangeResult(
      LexiconLookupResult lookup,
      List<IncrementalHintResult.Hint> mapped,
      long queryNanos,
      long rulesNanos,
      int ambiguous,
      int overlapDropped,
      long candidatesNanos) {
    RangeResult {
      Objects.requireNonNull(lookup, "lookup");
      mapped = List.copyOf(Objects.requireNonNull(mapped, "mapped"));
      if (queryNanos < 0
          || rulesNanos < 0
          || ambiguous < 0
          || overlapDropped < 0
          || candidatesNanos < 0) throw new IllegalArgumentException("timings must be nonnegative");
    }
  }

  /**
   * 只合并当前请求的结果，并在提示去重前检查所有候选版本。
   *
   * @param processedKeys 本次请求所有 append 片段 key，按显示顺序排列。
   * @param ranges 本次请求逐个新增区间的查询及处理结果。
   * @param planningNanos 本次新增区间规划的单调纳秒耗时。
   * @return 合并后的业务结果、耗时和查询计数。
   */
  MeasuredIncrementalCaptionResult assemble(
      List<String> processedKeys, List<RangeResult> ranges, long planningNanos) {
    Objects.requireNonNull(processedKeys, "processedKeys");
    Objects.requireNonNull(ranges, "ranges");
    var hints = new ArrayList<IncrementalHintResult.Hint>();
    var versions = new HashSet<Long>();
    var seenOccurrences = new HashSet<IncrementalHintResult.Hint>();
    var counts = LexiconLookupResult.Counts.zero();
    int candidates = 0;
    long query = 0, rules = 0, candidatesNanos = planningNanos;
    int ambiguous = 0, overlapDropped = 0;
    if (planningNanos < 0) throw new IllegalArgumentException("planningNanos must be nonnegative");
    for (var range : ranges) {
      var lookup = range.lookup();
      lookup.publishedVersion().ifPresent(versions::add);
      for (LexiconHintCandidate candidate : lookup.candidates())
        versions.add(candidate.lexiconVersion());
      for (var hint : range.mapped()) versions.add(hint.lexiconVersion());
      counts = counts.plus(lookup.counts());
      candidates = Math.addExact(candidates, lookup.candidates().size());
      query = Math.addExact(query, range.queryNanos());
      rules = Math.addExact(rules, range.rulesNanos());
      candidatesNanos = Math.addExact(candidatesNanos, range.candidatesNanos());
      ambiguous = Math.addExact(ambiguous, range.ambiguous());
      overlapDropped = Math.addExact(overlapDropped, range.overlapDropped());
      for (var hint : range.mapped()) if (seenOccurrences.add(hint)) hints.add(hint);
    }
    boolean conflict = versions.size() > 1;
    if (conflict) hints.clear();
    var publishedVersion =
        conflict || versions.size() != 1
            ? java.util.OptionalLong.empty()
            : java.util.OptionalLong.of(versions.iterator().next());
    return new MeasuredIncrementalCaptionResult(
        new IncrementalHintResult(processedKeys, hints),
        query,
        rules,
        candidates,
        counts,
        new MeasuredIncrementalCaptionResult.Diagnostics(
            ranges.size(), ambiguous, overlapDropped, publishedVersion, conflict, candidatesNanos));
  }
}
