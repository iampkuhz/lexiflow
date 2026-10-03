package io.lexiflow.lexicon.application.importing.model;

import java.util.Objects;

/**
 * 正交的公开分类证据，不以新阈值把连续 priority 排序伪装为长尾二元标签。
 *
 * @param basicWord 词条是基础单词；短语不继承组成词或单词别名的基础标记。
 * @param complexListed 存在来源复杂词表证据；不重算原 priority。
 * @param frequencyEvidence 频率是显式已知还是来源未排名；非 null。
 * @param hintEligible 准备结果能否按需提示；不由预热资格决定。
 * @param prewarmEligible 来源行的独立预热开关；最终词形仍按动作和分数判定。
 * @param decisiveReason 准备结果的决定原因；非空白。
 */
public record ImportClassification(
    boolean basicWord,
    boolean complexListed,
    FrequencyEvidence frequencyEvidence,
    boolean hintEligible,
    boolean prewarmEligible,
    String decisiveReason) {
  /** 来源是否提供可核对的频率证据；未知不是零频率。 */
  public enum FrequencyEvidence {
    KNOWN,
    UNKNOWN
  }

  /** 校验词条级分类和准备动作；词形级缓存资格由发布动作另行决定。 */
  public ImportClassification {
    Objects.requireNonNull(frequencyEvidence, "frequencyEvidence");
    decisiveReason = Objects.requireNonNull(decisiveReason, "decisiveReason");
    if (decisiveReason.isBlank()) {
      throw new IllegalArgumentException("decisiveReason must not be blank");
    }
    if (basicWord && hintEligible) {
      throw new IllegalArgumentException("basic words cannot be hint eligible");
    }
    if (basicWord && prewarmEligible) {
      throw new IllegalArgumentException("basic words use blocked-score prewarm only");
    }
  }
}
