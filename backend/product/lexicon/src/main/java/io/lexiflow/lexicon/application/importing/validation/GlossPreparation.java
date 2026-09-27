package io.lexiflow.lexicon.application.importing.validation;

import java.util.ArrayDeque;
import java.util.Objects;

/** 仅在导入期间清洗重复表达并提取默认首候选，不做语境消歧。 */
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

  /**
   * 以通用规范分隔符 {@code ；} 或 {@code ;} 取首候选并清理明确的领域或词性前缀。
   *
   * <p>校验完整表达的括号配对，不在括号内部截断；空首项不补位。
   *
   * @param gloss 含义：可能含多候选的完整来源短释。取值范围：非 null。
   * @return 首候选；清理后为空或括号结构不可靠时返回空字符串，由准备政策阻断展示。
   */
  public static String selectFirstCandidate(String gloss) {
    Objects.requireNonNull(gloss, "gloss");
    var closers = new ArrayDeque<Integer>();
    var boundary = gloss.length();
    for (var offset = 0; offset < gloss.length(); ) {
      var point = gloss.codePointAt(offset);
      var closer =
          switch (point) {
            case '(' -> ')';
            case '（' -> '）';
            case '[' -> ']';
            case '【' -> '】';
            default -> 0;
          };
      if (closer != 0) {
        closers.push(closer);
      } else if (point == ')' || point == '）' || point == ']' || point == '】') {
        if (closers.isEmpty() || closers.pop() != point) {
          return "";
        }
      } else if (closers.isEmpty() && (point == '；' || point == ';')) {
        boundary = Math.min(boundary, offset);
      }
      offset += Character.charCount(point);
    }
    if (!closers.isEmpty()) {
      return "";
    }
    return gloss
        .substring(0, boundary)
        .strip()
        .replaceFirst("^(?:[a-z]{1,6}\\.)\\s*", "")
        .replaceFirst("^\\[[\\p{IsHan}A-Za-z]{1,12}\\] *", "")
        .strip();
  }
}
