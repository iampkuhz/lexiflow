package io.lexiflow.lexicon.application.importing.policy;

import io.lexiflow.lexicon.application.importing.model.ImportClassification;
import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import io.lexiflow.lexicon.domain.model.LexiconHintAction;
import java.util.Objects;

/** 从来源和准备动作整理正交证据；不引入个人状态、频率阈值或新评分。 */
public final class ClassificationPolicy {
  private ClassificationPolicy() {}

  /**
   * 对完整词条分类，不将未排名 StarDict 行误判为显式 Zipf。
   *
   * @param row 含义：已校验的来源行。取值范围：非 null。
   * @param exclusionReason 含义：准备阶段阻断原因。取值范围：通过时为 null，否则为非空白原因码。
   * @return 基础、复杂词表、频率证据及独立的提示和预热开关。
   */
  public static ImportClassification classify(LexiconImportRow row, String exclusionReason) {
    Objects.requireNonNull(row, "row");
    var stardict = row.dictionary().sourceId().equals("ecdict-stardict");
    var knownFrequency = !stardict || row.sourceBncRank() != null || row.sourceFrqRank() != null;
    return new ImportClassification(
        row.basicVocabulary(),
        !row.sourceComplexTags().isEmpty() || !row.complexLists().isEmpty(),
        knownFrequency
            ? ImportClassification.FrequencyEvidence.KNOWN
            : ImportClassification.FrequencyEvidence.UNKNOWN,
        exclusionReason == null,
        row.prewarmEligible(),
        exclusionReason == null ? "eligible" : exclusionReason);
  }

  /**
   * 按词形的实际发布动作保持原缓存优先级公式，不以词条级动作覆盖降级结果。
   *
   * @param row 含义：已校验的来源行。取值范围：非 null。
   * @param classification 含义：与该行准备结果一致的分类。取值范围：非 null。
   * @param action 含义：已应用查询窗口降级的词形最终发布动作。取值范围：非 null。
   * @return 非负的原有缓存优先级；零表示不预热。
   */
  public static int cachePriority(
      LexiconImportRow row, ImportClassification classification, LexiconHintAction action) {
    Objects.requireNonNull(row, "row");
    Objects.requireNonNull(classification, "classification");
    Objects.requireNonNull(action, "action");
    if (action == LexiconHintAction.BLOCK && classification.basicWord()) return 1000;
    return classification.prewarmEligible() ? row.priority().memoryPriority() : 0;
  }
}
