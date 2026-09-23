package io.lexiflow.workflow.application;

import io.lexiflow.enrichment.domain.CaptionContext;
import io.lexiflow.enrichment.domain.CaptionHintResult;
import io.lexiflow.enrichment.domain.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.LexiconCatalog;
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
}
