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
   */
  record RangeResult(
      LexiconLookupResult lookup,
      List<IncrementalHintResult.Hint> mapped,
      long queryNanos,
      long rulesNanos) {
    RangeResult {
      Objects.requireNonNull(lookup, "lookup");
      mapped = List.copyOf(Objects.requireNonNull(mapped, "mapped"));
      if (queryNanos < 0 || rulesNanos < 0)
        throw new IllegalArgumentException("timings must be nonnegative");
    }
  }

  /**
   * 只合并当前请求的结果，并在提示去重前检查所有候选版本。
   *
   * @param processedKeys 本次请求所有 append 片段 key，按显示顺序排列。
   * @param ranges 本次请求逐个新增区间的查询及处理结果。
   * @return 合并后的业务结果、耗时和查询计数。
   */
  MeasuredIncrementalCaptionResult assemble(List<String> processedKeys, List<RangeResult> ranges) {
    Objects.requireNonNull(processedKeys, "processedKeys");
    Objects.requireNonNull(ranges, "ranges");
    var hints = new ArrayList<IncrementalHintResult.Hint>();
    var versions = new HashSet<Long>();
    var seenEntries = new HashSet<String>();
    var counts = LexiconLookupResult.Counts.zero();
    int candidates = 0;
    long query = 0, rules = 0;
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
      for (var hint : range.mapped()) if (seenEntries.add(hint.lexiconEntryId())) hints.add(hint);
    }
    if (versions.size() > 1) hints.clear();
    return new MeasuredIncrementalCaptionResult(
        new IncrementalHintResult(processedKeys, hints), query, rules, candidates, counts);
  }
}
