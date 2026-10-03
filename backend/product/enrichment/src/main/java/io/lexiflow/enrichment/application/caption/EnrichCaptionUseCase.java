package io.lexiflow.enrichment.application.caption;

import io.lexiflow.enrichment.application.caption.model.MeasuredCaptionResult;
import io.lexiflow.enrichment.application.caption.model.MeasuredIncrementalCaptionResult;
import io.lexiflow.enrichment.domain.model.CaptionContext;
import io.lexiflow.enrichment.domain.model.CaptionHintResult;
import io.lexiflow.enrichment.domain.model.CaptionIncrementalRequest;
import io.lexiflow.enrichment.domain.policy.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.model.LexiconLookupResult;
import io.lexiflow.lexicon.domain.port.LexiconCatalog;
import java.util.ArrayList;
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
    var keys = CandidateForms.fromCaption(context.caption());
    var lookup =
        keys.isEmpty()
            ? new LexiconLookupResult(
                List.of(), java.util.OptionalLong.empty(), LexiconLookupResult.Counts.zero())
            : lexiconCatalog.lookupForms(keys);
    var candidates = lookup.candidates();
    var queried = nanoTime.getAsLong();
    var result = hintPolicy.evaluate(context, candidates);
    var finished = nanoTime.getAsLong();
    return new MeasuredCaptionResult(
        result, queried - started, finished - queried, candidates.size(), lookup.counts());
  }

  /**
   * 只在新增片段到来时查询，允许同组同视觉行紧邻的有界旧尾词补全词组。
   *
   * @param request 含义：插件已标准化的双快照请求。取值范围：非空且满足领域合同。
   * @return 所有新增片段的处理覆盖、命中提示、累计耗时及查询计数。
   */
  public MeasuredIncrementalCaptionResult enrichIncrementalMeasured(
      CaptionIncrementalRequest request) {
    Objects.requireNonNull(request, "request");
    var planningStarted = nanoTime.getAsLong();
    var plan = new IncrementalCaptionPlan().plan(request);
    var processedKeys =
        request.current().captions().stream()
            .flatMap(group -> group.segments().stream())
            .filter(CaptionIncrementalRequest.Segment::append)
            .map(CaptionIncrementalRequest.Segment::key)
            .toList();
    var planningFinished = nanoTime.getAsLong();
    var ranges = new ArrayList<IncrementalResultAssembler.RangeResult>();
    var mapper = new IncrementalHintMapper();
    for (var interval : plan) {
      var candidatesStarted = nanoTime.getAsLong();
      var forms =
          CandidateForms.fromCaption(
              interval.text().substring(interval.contextStart(), interval.appendEndOffset()));
      var candidatesFinished = nanoTime.getAsLong();
      var queryStarted = nanoTime.getAsLong();
      var lookup =
          forms.isEmpty()
              ? new LexiconLookupResult(
                  List.of(), java.util.OptionalLong.empty(), LexiconLookupResult.Counts.zero())
              : lexiconCatalog.lookupForms(forms);
      var queried = nanoTime.getAsLong();
      var selection =
          hintPolicy.evaluateSelection(
              interval.text(),
              interval.contextStart(),
              interval.appendEndOffset(),
              interval.appendStart(),
              lookup.candidates());
      var mapped = mapper.map(request, interval, selection.hints());
      var finished = nanoTime.getAsLong();
      ranges.add(
          new IncrementalResultAssembler.RangeResult(
              lookup,
              mapped,
              queried - queryStarted,
              finished - queried,
              selection.ambiguous(),
              selection.overlapDropped(),
              candidatesFinished - candidatesStarted));
    }
    return new IncrementalResultAssembler()
        .assemble(processedKeys, ranges, planningFinished - planningStarted);
  }
}
