package io.lexiflow.lexicon.application.importing.validation;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Pattern;

/** StarDict 来源释义的纯文本解析与安全检查。 */
public final class StardictGlossPreparation {
  private static final Pattern PART_OF_SPEECH = Pattern.compile("^(?:[a-z]{1,6}\\.)\\s*");
  private static final Pattern DOMAIN_LABEL = Pattern.compile("^\\[[\\p{IsHan}A-Za-z]{1,12}\\] *");
  private static final Set<Integer> CLOSERS = Set.of((int) ')', (int) '）', (int) ']', (int) '】');

  private StardictGlossPreparation() {}

  /**
   * 按 StarDict 的括号外分隔符原样返回首候选；首项为空或括号不合法时返回空字符串。
   *
   * @param gloss 含义：StarDict 原始释义。取值范围：非 null 字符串。
   * @return 含义：清理过词性及首个领域标签的首候选；结构错误或空首项返回空字符串。
   */
  public static String firstCandidate(String gloss) {
    Objects.requireNonNull(gloss, "gloss");
    try {
      return candidates(gloss).getFirst();
    } catch (IllegalArgumentException exception) {
      return "";
    }
  }

  /**
   * 按 StarDict 五种括号外分隔符切分并按来源规则清理每项；空项身份与顺序均保留。
   *
   * @param gloss 含义：StarDict 原始释义。取值范围：非 null 字符串。
   * @return 含义：按出现顺序排列的候选项，不丢弃空项。
   * @throws IllegalArgumentException 括号未闭合、多余闭合或括号类型错配时抛出。
   */
  public static List<String> candidates(String gloss) {
    Objects.requireNonNull(gloss, "gloss");
    var stack = new ArrayDeque<Integer>();
    var result = new ArrayList<String>();
    var current = new StringBuilder();
    for (int offset = 0; offset < gloss.length(); ) {
      int point = gloss.codePointAt(offset);
      int closer = closer(point);
      if (closer != 0) {
        stack.push(closer);
        current.appendCodePoint(point);
      } else if (CLOSERS.contains(point)) {
        if (stack.isEmpty() || stack.pop() != point) {
          throw new IllegalArgumentException("mismatched closing bracket in gloss");
        }
        current.appendCodePoint(point);
      } else if (stack.isEmpty() && "；;，,、".indexOf(point) >= 0) {
        result.add(clean(current.toString()));
        current.setLength(0);
      } else {
        current.appendCodePoint(point);
      }
      offset += Character.charCount(point);
    }
    if (!stack.isEmpty()) throw new IllegalArgumentException("unclosed opening bracket in gloss");
    result.add(clean(current.toString()));
    return List.copyOf(result);
  }

  /**
   * 判断短释可否安全作为默认提示：不空、至多 24 个 code point、含汉字且只含字母数字。
   *
   * @param gloss 含义：待展示的单一候选。取值范围：可为 null。
   * @return 含义：满足长度、汉字及字符安全合同返回 true，否则 false。
   */
  public static boolean safeGloss(String gloss) {
    if (gloss == null || gloss.isBlank() || gloss.codePointCount(0, gloss.length()) > 24)
      return false;
    boolean han = false;
    for (int point : gloss.codePoints().toArray()) {
      han |= Character.UnicodeScript.of(point) == Character.UnicodeScript.HAN;
      int type = Character.getType(point);
      if (!Character.isLetterOrDigit(point)
          || type == Character.FORMAT
          || type == Character.CONTROL
          || Character.isWhitespace(point)) return false;
    }
    return han;
  }

  private static int closer(int point) {
    return switch (point) {
      case '(' -> ')';
      case '（' -> '）';
      case '[' -> ']';
      case '【' -> '】';
      default -> 0;
    };
  }

  private static String clean(String value) {
    return DOMAIN_LABEL
        .matcher(PART_OF_SPEECH.matcher(value.trim()).replaceFirst(""))
        .replaceFirst("")
        .trim();
  }
}
