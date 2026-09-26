package io.lexiflow.lexicon.application.importing.policy;

import io.lexiflow.lexicon.application.importing.model.LexiconImportRow;
import java.util.Arrays;
import java.util.Objects;
import java.util.Set;

/** 发布前从来源证据冻结可显示性；观看阶段不再从原始释义猜测首义。 */
public final class HintPreparation {
  private static final Set<String> LOW_INFORMATION_STARTS = Set.of("a", "an", "the", "not");
  private static final Set<String> INCOMPLETE_ENDS =
      Set.of("to", "of", "for", "by", "with", "in", "on", "at", "from");
  private static final Set<String> TIME_ADVERBS = Set.of("today", "yesterday", "tomorrow");
  private static final Set<String> FUNCTION_TOKENS =
      Set.of(
          "a", "an", "the", "not", "to", "be", "even", "when", "if", "as", "at", "in", "on", "for",
          "of", "by", "and", "or", "but");

  private HintPreparation() {}

  /**
   * 返回不可展示的确定原因；空值表示已有安全单一短释。
   *
   * @param row 含义：已完成来源清洗与基础词选择的导入记录。取值范围：非 null，已完成来源格式校验。
   * @return 固定排除原因，或可显示时的 null。
   */
  public static String exclusionReason(LexiconImportRow row) {
    Objects.requireNonNull(row, "row");
    if (row.basicVocabulary()) return "basic_vocabulary";
    if (lowInformationPhrase(row.lemma())) return "low_information_phrase";
    if (!safeGloss(row.chineseGloss())) return "unsafe_or_ambiguous_gloss";
    return null;
  }

  private static boolean lowInformationPhrase(String lemma) {
    if (!lemma.contains(" ")) return false;
    var tokens = lemma.split(" ");
    var first = tokens[0];
    var last = tokens[tokens.length - 1];
    return LOW_INFORMATION_STARTS.contains(first)
        || INCOMPLETE_ENDS.contains(last)
        || Arrays.stream(tokens).allMatch(FUNCTION_TOKENS::contains)
        || (first.equals("on") && TIME_ADVERBS.contains(last));
  }

  private static boolean safeGloss(String gloss) {
    if (gloss == null || gloss.isBlank() || gloss.codePointCount(0, gloss.length()) > 24) {
      return false;
    }
    var containsHan = false;
    for (var point : gloss.codePoints().toArray()) {
      containsHan |= Character.UnicodeScript.of(point) == Character.UnicodeScript.HAN;
      var type = Character.getType(point);
      if (!Character.isLetterOrDigit(point)
          || type == Character.FORMAT
          || type == Character.CONTROL
          || Character.isWhitespace(point)) return false;
    }
    return containsHan;
  }
}
