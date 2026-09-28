package io.lexiflow.lexicon.application.importing.model;

import java.util.List;
import java.util.Objects;

/**
 * 单次准备决策，供词义与查询投影共同消费；不代表语义审查凭证。
 *
 * @param gloss 含义：安全首候选。取值范围：通过时非空，阻断时为 null。
 * @param exclusionReason 含义：稳定阻断原因。取值范围：通过时为 null，阻断时非空。
 * @param decisiveRule 含义：决定终态的规则身份。取值范围：非空。
 * @param matchedRules 含义：执行过的清洗变换。取值范围：非 null，不可变。
 */
public record PreparedHint(
    String gloss, String exclusionReason, String decisiveRule, List<String> matchedRules) {
  /** 检查互斥终态并冻结轨迹，不在结果模型中执行业务策略。 */
  public PreparedHint {
    if ((gloss == null) == (exclusionReason == null)) {
      throw new IllegalArgumentException("exactly one of gloss and exclusion reason is required");
    }
    Objects.requireNonNull(decisiveRule, "decisiveRule");
    matchedRules = List.copyOf(matchedRules);
  }
}
