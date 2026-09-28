package io.lexiflow.enrichment.application.caption;

import io.lexiflow.enrichment.application.caption.model.MeasuredCaptionResult;
import io.lexiflow.enrichment.application.caption.model.MeasuredIncrementalCaptionResult;
import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.CaptionHintResult;
import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.model.IncrementalHintResult;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.function.LongSupplier;

/** 协调词汇材料与 Enrichment 快速提示的应用用例。 */
public final class EnrichCaptionUseCase {
  private final LexiconCatalog lexiconCatalog;
  private final DeterministicHintPolicy hintPolicy;
  private final LongSupplier nanoTime;

  /**
   * 创建一个只依赖公开领域合同的字幕提示用例。
   *
   * @param lexiconCatalog 词汇候选来源。
   * @param hintPolicy 确定性提示策略。
   */
  public EnrichCaptionUseCase(LexiconCatalog lexiconCatalog, DeterministicHintPolicy hintPolicy) {
    this(lexiconCatalog, hintPolicy, System::nanoTime);
  }

  /**
   * 注入单调时钟以确定性验证分段耗时，不使用墙上时间。
   *
   * @param lexiconCatalog 含义：公开词库候选来源。取值范围：非空。
   * @param hintPolicy 含义：确定性规则。取值范围：非空。
   * @param nanoTime 含义：单调纳秒时钟。取值范围：非空且读数单调不减。
   */
  public EnrichCaptionUseCase(
      LexiconCatalog lexiconCatalog, DeterministicHintPolicy hintPolicy, LongSupplier nanoTime) {
    this.nanoTime = Objects.requireNonNull(nanoTime, "nanoTime");
    this.lexiconCatalog = Objects.requireNonNull(lexiconCatalog, "lexiconCatalog");
    this.hintPolicy = Objects.requireNonNull(hintPolicy, "hintPolicy");
  }

  /**
   * 对一段已定位英文字幕形成不阻塞的快速提示结果。
   *
   * @param context 含义：内容修订绑定的字幕上下文。取值范围：非空，且必须通过其值对象校验。
   * @return READY 或 NO_PENDING 的确定性结果。
   */
  public CaptionHintResult enrich(CaptionContext context) {
    return enrichMeasured(context).result();
  }

  /**
   * 查询和规则分别计时，返回给组合根观测；领域不依赖指标框架。
   *
   * @param context 含义：已定位字幕上下文。取值范围：非空且范围有效。
   * @return 确定性结果、候选数量与同一单调时钟下的查询和规则耗时。
   */
  public MeasuredCaptionResult enrichMeasured(CaptionContext context) {
    Objects.requireNonNull(context, "context");
    var started = nanoTime.getAsLong();
    var candidates = lexiconCatalog.candidatesFor(context.caption());
    var queried = nanoTime.getAsLong();
    var result = hintPolicy.evaluate(context, candidates);
    var finished = nanoTime.getAsLong();
    return new MeasuredCaptionResult(
        result, queried - started, finished - queried, candidates.size());
  }

  /**
   * 只在新增片段到来时查询，允许同组同视觉行紧邻的有界旧尾词补全词组。
   *
   * @param request 含义：插件已标准化的双快照请求。取值范围：非空且满足领域合同。
   * @return 所有新增片段的处理覆盖、命中提示、累计耗时及仅本次处理区间的诊断文字。
   */
  public MeasuredIncrementalCaptionResult enrichIncrementalMeasured(
      CaptionIncrementalRequest request) {
    Objects.requireNonNull(request, "request");
    var processedKeys = new ArrayList<String>();
    var hints = new ArrayList<IncrementalHintResult.Hint>();
    var intervals = new ArrayList<Interval>();
    var publishedVersions = new HashSet<Long>();
    var seenEntries = new HashSet<String>();
    long queryNanos = 0;
    long rulesNanos = 0;
    int candidateCount = 0;
    for (var group : request.current().captions()) {
      var text = new StringBuilder();
      var starts = new ArrayList<Integer>();
      for (var segment : group.segments()) {
        starts.add(text.length());
        text.append(segment.text());
        if (segment.append()) processedKeys.add(segment.key());
      }
      for (var index = 0; index < group.segments().size(); ) {
        if (!group.segments().get(index).append()) {
          index++;
          continue;
        }
        var first = index;
        var line = group.segments().get(first).line();
        while (index < group.segments().size()
            && group.segments().get(index).append()
            && group.segments().get(index).line() == line) index++;
        var start = starts.get(first);
        var end = starts.get(index - 1) + group.segments().get(index - 1).text().length();
        var contextFirst = first;
        var contextLength = 0;
        while (contextFirst > 0
            && first - contextFirst < 2
            && !group.segments().get(contextFirst - 1).append()
            && group.segments().get(contextFirst - 1).line() == line
            && contextLength + group.segments().get(contextFirst - 1).text().length() <= 48) {
          contextFirst--;
          contextLength += group.segments().get(contextFirst).text().length();
        }
        var contextStart = starts.get(contextFirst);
        var groupText = text.toString();
        var started = nanoTime.getAsLong();
        var candidates = lexiconCatalog.candidatesFor(groupText.substring(contextStart, end));
        var queried = nanoTime.getAsLong();
        var selected = hintPolicy.evaluate(groupText, contextStart, end, start, candidates);
        // 先收集版本再去重，不能让同词条的重复命中掩盖跨区间版本冲突。
        for (var hint : selected) publishedVersions.add(hint.lexiconVersion());
        selected =
            selected.stream().filter(hint -> seenEntries.add(hint.lexiconEntryId())).toList();
        var finished = nanoTime.getAsLong();
        queryNanos += queried - started;
        rulesNanos += finished - queried;
        candidateCount += candidates.size();
        for (var hint : selected) {
          var startIndex = segmentAtStart(group, starts, contextFirst, index, hint.startOffset());
          var endIndex = segmentAtEnd(group, starts, contextFirst, index, hint.endOffset());
          hints.add(
              new IncrementalHintResult.Hint(
                  group.segments().get(startIndex).key(),
                  hint.startOffset() - starts.get(startIndex),
                  group.segments().get(endIndex).key(),
                  hint.endOffset() - starts.get(endIndex),
                  hint.chineseGloss(),
                  hint.lexiconEntryId(),
                  hint.lexiconVersion(),
                  hint.senseId()));
        }
        intervals.add(
            new Interval(
                groupText,
                start,
                end,
                selected.stream().filter(hint -> hint.startOffset() >= start).toList()));
      }
    }
    if (publishedVersions.size() > 1) {
      hints.clear();
      intervals.replaceAll(
          interval -> new Interval(interval.text(), interval.start(), interval.end(), List.of()));
    }
    var english = new StringBuilder();
    var finalText = new StringBuilder();
    for (var interval : intervals) {
      if (!english.isEmpty()) {
        english.append('\n');
        finalText.append('\n');
      }
      english.append(interval.text(), interval.start(), interval.end());
      var cursor = interval.start();
      for (var hint : interval.hints()) {
        finalText.append(interval.text(), cursor, hint.endOffset());
        finalText.append('(').append(hint.chineseGloss()).append(')');
        cursor = hint.endOffset();
      }
      finalText.append(interval.text(), cursor, interval.end());
    }
    return new MeasuredIncrementalCaptionResult(
        new IncrementalHintResult(processedKeys, hints),
        queryNanos,
        rulesNanos,
        candidateCount,
        english.toString(),
        finalText.toString());
  }

  private static int segmentAtStart(
      CaptionIncrementalRequest.Group group, List<Integer> starts, int first, int end, int offset) {
    for (var i = first; i < end; i++) {
      if (offset < starts.get(i) + group.segments().get(i).text().length()) return i;
    }
    throw new IllegalArgumentException("hint start is outside append interval");
  }

  private static int segmentAtEnd(
      CaptionIncrementalRequest.Group group, List<Integer> starts, int first, int end, int offset) {
    for (var i = first; i < end; i++) {
      if (offset <= starts.get(i) + group.segments().get(i).text().length()) return i;
    }
    throw new IllegalArgumentException("hint end is outside append interval");
  }

  /**
   * 保存一个新增区间的完整组上下文，诊断输出只截取该区间。
   *
   * @param text 区间所属组的完整英文，用于保持词边界。
   * @param start 新增区间在组内的 UTF-16 起点，包含该位置。
   * @param end 新增区间在组内的 UTF-16 终点，不包含该位置。
   * @param hints 完全位于该区间内的已排序提示。
   */
  private record Interval(
      String text,
      int start,
      int end,
      List<io.lexiflow.enrichment.domain.model.AnnotationHint> hints) {}
}
