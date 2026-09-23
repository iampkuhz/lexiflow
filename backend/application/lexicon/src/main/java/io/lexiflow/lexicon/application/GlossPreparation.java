package io.lexiflow.lexicon.application;

import java.util.Objects;

/** 仅在导入期间清洗重复表达，不选择首义或做语境消歧。 */
public final class GlossPreparation {
  private GlossPreparation() {}

  /**
   * 所有分号项去除明确领域前缀后逐字相同才合并；否则完整保留。
   *
   * @param value 含义：来源中的完整释义。取值范围：非 null 的字符串。
   * @return 唯一重复表达或完整保留的原始释义。
   */
  public static String normalize(String value) {
    Objects.requireNonNull(value, "value");
    if (!value.contains("；") && !value.contains(";")) return value;
    String common = null;
    for (var part : value.split("[；;]", -1)) {
      var expression = part.strip().replaceFirst("^\\[[\\p{IsHan}A-Za-z]{1,12}\\] *", "");
      if (expression.isEmpty() || (common != null && !common.equals(expression))) return value;
      common = expression;
    }
    return common;
  }
}
