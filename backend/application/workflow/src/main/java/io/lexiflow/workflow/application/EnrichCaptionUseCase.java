package io.lexiflow.workflow.application;

import io.lexiflow.enrichment.domain.CaptionContext;
import io.lexiflow.enrichment.domain.CaptionHintResult;
import io.lexiflow.enrichment.domain.DeterministicHintPolicy;
import io.lexiflow.lexicon.domain.LexiconCatalog;
import java.util.Objects;

/** 协调词汇材料与 Enrichment 快速提示的应用用例。 */
public final class EnrichCaptionUseCase {
  private final LexiconCatalog lexiconCatalog;
  private final DeterministicHintPolicy hintPolicy;

  /**
   * 创建一个只依赖公开领域合同的字幕提示用例。
   *
   * @param lexiconCatalog 词汇候选来源。
   * @param hintPolicy 确定性提示策略。
   */
  public EnrichCaptionUseCase(LexiconCatalog lexiconCatalog, DeterministicHintPolicy hintPolicy) {
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
    Objects.requireNonNull(context, "context");
    return hintPolicy.evaluate(context, lexiconCatalog.candidatesFor(context.caption()));
  }
}
